from __future__ import annotations

import argparse
import contextlib
import http.client
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import hormuz.commands.personal as personal_commands
import hormuz.execution_methods as execution_methods
from hormuz.adapters import adapter_for, conformance_report
from hormuz.client_relay import (
    ClientRelayError,
    LocalRelayServer,
    RelayOptimizer,
    SavedClientProfile,
)
from hormuz.client_versions import SUPPORTED_CLIENT_VERSIONS
from hormuz.compaction_runtime import ContextPreferenceStore, ContextRuntimeError
from hormuz.credential_store import CredentialStoreError, ProviderCredentialStore
from hormuz.execution_methods import (
    ExecutionMethodError,
    ExecutionRouter,
    JevChoiceAdapter,
    JevQualification,
    MethodRequest,
)
from hormuz.personal_metrics import (
    OptimizationMeasurement,
    PersonalMetricsError,
    PersonalMetricsStore,
    ProviderMeasurement,
    extract_provider_usage,
)
from hormuz.personal_optimization import (
    BehaviorEvent,
    BehaviorTracker,
    BoundedReadRetry,
    ConversationTurn,
    FrictionSignal,
    InterventionComparison,
    PersonalOptimizationError,
    RecoveryBuffer,
    TypedToolHistoryCompactor,
)
from hormuz.personal_adapter_example import ExampleAgentAdapter
from hormuz.personal_qualification import run_product_qualification
from hormuz.personal_profiles import (
    PersonalProfile,
    PersonalProfileError,
    PersonalProfileStore,
    PersonalRemovalState,
)
from hormuz.personal_runtime import direct_credential_reader, run_personal_client


LOCAL_TOKEN = "hox_l_" + "l" * 43
DIRECT_TOKEN = "sk-direct-test-only"
COUNTERS = {"cl100k_base": len, "o200k_base": len}


class _MemoryKeyring:
    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class _DirectProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _DirectProviderHandler)
        self.requests: list[tuple[bytes, dict[str, str]]] = []


class _DirectProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        self.server.requests.append((body, dict(self.headers)))
        response = json.dumps(
            {
                "id": "response-test",
                "usage": {
                    "input_tokens": 40,
                    "output_tokens": 5,
                    "input_tokens_details": {"cached_tokens": 10},
                    "output_tokens_details": {"reasoning_tokens": 2},
                },
            },
            separators=(",", ":"),
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        return


class _AiderProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _AiderProviderHandler)
        self.requests: list[tuple[bytes, dict[str, str]]] = []


class _AiderProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        self.server.requests.append((body, dict(self.headers)))
        response = json.dumps(
            {
                "id": "chatcmpl-personal-aider",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 2,
                    "total_tokens": 42,
                },
            },
            separators=(",", ":"),
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        return


class _TruncatedProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _TruncatedProviderHandler)


class _TruncatedProviderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "100")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b"{}")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, format: str, *args: object) -> None:
        return


class PersonalContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name)
        self.state.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def profile(self, **changes) -> PersonalProfile:
        values = {
            "key": "personal-a",
            "mode": "direct",
            "agent": "codex",
            "provider": "openai",
            "endpoint": "https://api.openai.com",
            "model": "qualified-model",
            "generation": "1" * 64,
            "credential_env": "OPENAI_API_KEY",
        }
        values.update(changes)
        return PersonalProfile(**values)

    def test_provider_credential_uses_distinct_secure_store_and_profile_has_no_secret(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("personal-a", DIRECT_TOKEN)
        self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)

        profiles = PersonalProfileStore(self.state)
        profiles.save(self.profile())
        serialized = profiles.path_for("personal-a").read_text(encoding="utf-8")
        self.assertNotIn(DIRECT_TOKEN, serialized)
        self.assertEqual(profiles.load("personal-a"), self.profile())
        credentials.delete("personal-a")
        self.assertIsNone(credentials.get("personal-a"))

    def test_profile_save_rejects_an_unreadable_oversized_document(self) -> None:
        store = PersonalProfileStore(self.state)
        with (
            mock.patch("hormuz.personal_profiles.MAX_PROFILE_BYTES", 1),
            self.assertRaisesRegex(PersonalProfileError, "personal_profile_invalid"),
        ):
            store.save(self.profile())
        self.assertFalse(store.path_for("personal-a").exists())

    def test_personal_profile_keys_require_canonical_lowercase_ascii(self) -> None:
        store = PersonalProfileStore(self.state)
        for key in ("Personal-A", "PERSONAL-A", "pérsonal-a", ".personal-a"):
            with self.subTest(key=key), self.assertRaisesRegex(
                PersonalProfileError, "invalid_personal_profile_key"
            ):
                store.path_for(key)
        self.assertEqual(store.path_for("personal-a").name, "personal-a.json")

    def test_profile_schema_one_remains_removable_after_the_schema_upgrade(self) -> None:
        store = PersonalProfileStore(self.state)
        store.directory.mkdir(parents=True, mode=0o700)
        legacy = self.profile().to_dict()
        legacy["schema_version"] = 1
        legacy.pop("previous_preference_enabled")
        legacy.pop("generation")
        path = store.path_for("personal-a")
        path.write_text(json.dumps(legacy), encoding="utf-8")
        path.chmod(0o600)
        loaded = store.load("personal-a")
        self.assertIsNone(loaded.previous_preference_enabled)
        self.assertRegex(loaded.generation, r"^[0-9a-f]{64}$")
        self.assertTrue(store.remove("personal-a"))

    def test_profile_schema_two_gets_a_stable_legacy_generation(self) -> None:
        store = PersonalProfileStore(self.state)
        store.directory.mkdir(parents=True, mode=0o700)
        legacy = self.profile(previous_preference_enabled=False).to_dict()
        legacy["schema_version"] = 2
        legacy.pop("generation")
        path = store.path_for("personal-a")
        path.write_text(json.dumps(legacy), encoding="utf-8")
        path.chmod(0o600)
        first = store.load("personal-a")
        second = store.load("personal-a")
        self.assertFalse(first.previous_preference_enabled)
        self.assertEqual(first.generation, second.generation)
        self.assertRegex(first.generation, r"^[0-9a-f]{64}$")

    def test_profile_save_removes_the_link_after_a_post_link_failure(self) -> None:
        store = PersonalProfileStore(self.state)
        real_fsync = os.fsync
        calls = 0

        def fail_directory_fsync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("directory fsync failed")
            real_fsync(descriptor)

        with (
            mock.patch("hormuz.personal_profiles.os.fsync", side_effect=fail_directory_fsync),
            self.assertRaisesRegex(PersonalProfileError, "personal_profile_write_failed"),
        ):
            store.save(self.profile())
        self.assertFalse(store.path_for("personal-a").exists())

    def test_removal_state_is_private_strict_and_content_free(self) -> None:
        store = PersonalProfileStore(self.state)
        state = PersonalRemovalState(
            key="personal-a",
            credential_cleanup_required=True,
            preference_action="disable",
            profile_generation="1" * 64,
        )
        store.save_removal_state(state)
        path = store.removal_state_path_for("personal-a")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("personal-a", path.name)
        self.assertEqual(store.load_removal_state("personal-a"), state)
        serialized = path.read_text(encoding="utf-8")
        self.assertNotIn(DIRECT_TOKEN, serialized)
        self.assertIn('"profile_generation"', serialized)
        path.write_text(
            '{"schema_version":1,"key":"personal-a",'
            '"credential_cleanup_required":true,"preference_action":"disable"}',
            encoding="utf-8",
        )
        path.chmod(0o600)
        self.assertEqual(
            store.load_removal_state("personal-a"),
            PersonalRemovalState(
                key="personal-a",
                credential_cleanup_required=True,
                preference_action="disable",
                profile_generation=None,
            ),
        )
        path.write_text(
            '{"schema_version":1,"key":"personal-a",'
            '"credential_cleanup_required":true,"preference_action":"guess"}',
            encoding="utf-8",
        )
        path.chmod(0o600)
        with self.assertRaisesRegex(
            PersonalProfileError, "personal_removal_state_invalid"
        ):
            store.load_removal_state("personal-a")
        self.assertTrue(store.clear_removal_state("personal-a"))

    def test_profile_modes_are_closed_and_managed_cannot_use_aider(self) -> None:
        store = PersonalProfileStore(self.state)
        store.save(self.profile())
        with self.assertRaisesRegex(Exception, "managed_agent_unsupported"):
            store.save(
                self.profile(
                    key="managed-aider",
                    mode="managed",
                    agent="aider",
                    provider="hormuz",
                    endpoint="https://gateway.example",
                    managed_profile="managed-source",
                    credential_env=None,
                )
            )

    def test_remove_before_profile_directory_exists_is_an_idempotent_noop(self) -> None:
        profiles = PersonalProfileStore(self.state)
        self.assertFalse(profiles.directory.exists())
        output = io.StringIO()
        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                side_effect=AssertionError("empty removal must not open a keyring"),
            ),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(
                personal_commands.run(
                    argparse.Namespace(
                        personal_command="remove",
                        profile="never-created",
                        state_directory=self.state,
                    )
                ),
                0,
            )
        self.assertIn("removed=false", output.getvalue())
        self.assertFalse(profiles.directory.exists())

    def test_connect_is_create_only_and_remove_restores_transient_setup(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)

        def connect_args() -> argparse.Namespace:
            return argparse.Namespace(
                personal_command="connect",
                profile="personal-a",
                state_directory=self.state,
                mode="direct",
                agent="codex",
                provider="openai",
                endpoint="https://api.openai.com",
                model="qualified-model",
                credential_env="PERSONAL_TEST_PROVIDER_KEY",
                allow_loopback_http=False,
            )

        output = io.StringIO()
        errors = io.StringIO()
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            with mock.patch.dict(
                os.environ, {"PERSONAL_TEST_PROVIDER_KEY": DIRECT_TOKEN}, clear=False
            ):
                self.assertEqual(personal_commands.run(connect_args()), 0)
            self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)
            self.assertEqual(
                PersonalProfileStore(self.state).load("personal-a").credential_env,
                "PERSONAL_TEST_PROVIDER_KEY",
            )
            self.assertTrue(ContextPreferenceStore(self.state, "personal-a").load().enabled)

            with mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-second-test-only"},
                clear=False,
            ):
                self.assertEqual(personal_commands.run(connect_args()), 1)
            self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)

            with mock.patch.dict(
                os.environ,
                {"OTHER_PROVIDER_KEY": "sk-other-test-only"},
                clear=False,
            ):
                self.assertEqual(
                    personal_commands.run(
                        argparse.Namespace(
                            personal_command="credential",
                            profile="personal-a",
                            state_directory=self.state,
                            credential_env="OTHER_PROVIDER_KEY",
                        )
                    ),
                    2,
                )
            self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)

            with mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-second-test-only"},
                clear=False,
            ):
                self.assertEqual(
                    personal_commands.run(
                        argparse.Namespace(
                            personal_command="credential",
                            profile="personal-a",
                            state_directory=self.state,
                            credential_env="PERSONAL_TEST_PROVIDER_KEY",
                        )
                    ),
                    0,
                )
            self.assertEqual(credentials.get("personal-a"), "sk-second-test-only")

            self.assertEqual(
                personal_commands.run(
                    argparse.Namespace(
                        personal_command="off",
                        profile="personal-a",
                        state_directory=self.state,
                    )
                ),
                0,
            )
            self.assertFalse(ContextPreferenceStore(self.state, "personal-a").load().enabled)
            self.assertEqual(
                personal_commands.run(
                    argparse.Namespace(
                        personal_command="remove",
                        profile="personal-a",
                        state_directory=self.state,
                    )
                ),
                0,
            )
        self.assertIsNone(credentials.get("personal-a"))
        self.assertFalse(PersonalProfileStore(self.state).path_for("personal-a").exists())
        self.assertFalse(ContextPreferenceStore(self.state, "personal-a").path.exists())

    def test_managed_remove_restores_the_preexisting_preference(self) -> None:
        preference = ContextPreferenceStore(self.state, "managed-source")
        preference.save(False)
        managed = SavedClientProfile(
            key="managed-source",
            gateway="https://gateway.example",
            client="codex",
            model="qualified-model",
            allow_insecure_http=False,
        )
        connect = argparse.Namespace(
            personal_command="connect",
            profile="managed-source",
            state_directory=self.state,
            mode="managed",
            agent="codex",
            provider=None,
            endpoint=None,
            model=None,
            credential_env=None,
            allow_loopback_http=False,
        )
        remove = argparse.Namespace(
            personal_command="remove",
            profile="managed-source",
            state_directory=self.state,
        )
        with (
            mock.patch.object(personal_commands, "load_saved_profile", return_value=managed),
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                side_effect=AssertionError("managed removal must not open a keyring"),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(connect), 0)
            profile = PersonalProfileStore(self.state).load("managed-source")
            self.assertFalse(profile.previous_preference_enabled)
            self.assertTrue(preference.load().enabled)
            self.assertEqual(personal_commands.run(remove), 0)
            self.assertFalse(preference.load().enabled)
            self.assertEqual(personal_commands.run(remove), 0)
        self.assertFalse(preference.load().enabled)

    def test_failed_managed_connect_keeps_retryable_preference_restoration(self) -> None:
        preference = ContextPreferenceStore(self.state, "managed-source")
        preference.save(False)
        managed = SavedClientProfile(
            key="managed-source",
            gateway="https://gateway.example",
            client="codex",
            model="qualified-model",
            allow_insecure_http=False,
        )
        connect = argparse.Namespace(
            personal_command="connect",
            profile="managed-source",
            state_directory=self.state,
            mode="managed",
            agent="codex",
            provider=None,
            endpoint=None,
            model=None,
            credential_env=None,
            allow_loopback_http=False,
        )
        remove = argparse.Namespace(
            personal_command="remove",
            profile="managed-source",
            state_directory=self.state,
        )
        original_save = ContextPreferenceStore.save
        save_calls = 0

        def fail_connect_and_rollback(
            preference_store: ContextPreferenceStore, enabled: bool
        ) -> None:
            nonlocal save_calls
            save_calls += 1
            if save_calls == 1:
                original_save(preference_store, enabled)
                raise ContextRuntimeError("settings_write_failed")
            if save_calls == 2:
                raise ContextRuntimeError("settings_write_failed")
            original_save(preference_store, enabled)

        with (
            mock.patch.object(personal_commands, "load_saved_profile", return_value=managed),
            mock.patch.object(
                ContextPreferenceStore,
                "save",
                autospec=True,
                side_effect=fail_connect_and_rollback,
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(connect), 1)
        profiles = PersonalProfileStore(self.state)
        self.assertFalse(profiles.path_for("managed-source").exists())
        self.assertTrue(profiles.removal_state_path_for("managed-source").exists())
        self.assertTrue(preference.load().enabled)
        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                side_effect=AssertionError("managed recovery must not open a keyring"),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 0)
        self.assertFalse(preference.load().enabled)
        self.assertFalse(profiles.removal_state_path_for("managed-source").exists())

    def test_preference_retry_preserves_a_restored_preexisting_credential(self) -> None:
        key = "personal-a"
        preference = ContextPreferenceStore(self.state, key)
        preference.save(False)
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        previous_secret = "sk-preexisting-test-only"
        credentials.set(key, previous_secret)
        connect = argparse.Namespace(
            personal_command="connect",
            profile=key,
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="qualified-model",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        remove = argparse.Namespace(
            personal_command="remove",
            profile=key,
            state_directory=self.state,
        )
        original_save = ContextPreferenceStore.save
        save_calls = 0

        def fail_connect_and_preference_rollback(
            preference_store: ContextPreferenceStore, enabled: bool
        ) -> None:
            nonlocal save_calls
            save_calls += 1
            if save_calls == 1:
                original_save(preference_store, enabled)
            if save_calls <= 2:
                raise ContextRuntimeError("settings_write_failed")
            original_save(preference_store, enabled)

        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            mock.patch.object(
                ContextPreferenceStore,
                "save",
                autospec=True,
                side_effect=fail_connect_and_preference_rollback,
            ),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-replacement-test-only"},
                clear=False,
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(connect), 1)
        profiles = PersonalProfileStore(self.state)
        self.assertFalse(profiles.entry_exists(key))
        removal_state = profiles.load_removal_state(key)
        self.assertIsNotNone(removal_state)
        assert removal_state is not None
        self.assertFalse(removal_state.credential_cleanup_required)
        self.assertEqual(removal_state.preference_action, "disable")
        self.assertEqual(credentials.get(key), previous_secret)

        retry_errors = io.StringIO()
        with contextlib.redirect_stderr(retry_errors):
            self.assertEqual(personal_commands.run(connect), 1)
        self.assertIn("personal_removal_incomplete", retry_errors.getvalue())

        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                side_effect=AssertionError("preference retry must not open a keyring"),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 0)
        self.assertFalse(preference.load().enabled)
        self.assertEqual(credentials.get(key), previous_secret)
        self.assertFalse(profiles.removal_state_path_for(key).exists())

    def test_remove_rejects_cleanup_state_from_another_profile_generation(self) -> None:
        profiles = PersonalProfileStore(self.state)
        profiles.save(self.profile())
        profiles.save_removal_state(
            PersonalRemovalState(
                key="personal-a",
                credential_cleanup_required=False,
                preference_action="preserve",
                profile_generation="2" * 64,
            )
        )
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(
                personal_commands.run(
                    argparse.Namespace(
                        personal_command="remove",
                        profile="personal-a",
                        state_directory=self.state,
                    )
                ),
                1,
            )
        self.assertIn("personal_removal_state_invalid", errors.getvalue())
        self.assertTrue(profiles.entry_exists("personal-a"))
        self.assertTrue(profiles.removal_state_path_for("personal-a").exists())

    def test_connect_keeps_profile_when_narrowed_retry_state_is_not_durable(self) -> None:
        key = "personal-a"
        preference = ContextPreferenceStore(self.state, key)
        preference.save(False)
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        previous_secret = "sk-preexisting-test-only"
        credentials.set(key, previous_secret)
        connect = argparse.Namespace(
            personal_command="connect",
            profile=key,
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="qualified-model",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        remove = argparse.Namespace(
            personal_command="remove",
            profile=key,
            state_directory=self.state,
        )
        original_preference_save = ContextPreferenceStore.save
        preference_save_calls = 0

        def fail_connect_and_preference_rollback(
            preference_store: ContextPreferenceStore, enabled: bool
        ) -> None:
            nonlocal preference_save_calls
            preference_save_calls += 1
            if preference_save_calls == 1:
                original_preference_save(preference_store, enabled)
            if preference_save_calls <= 2:
                raise ContextRuntimeError("settings_write_failed")
            original_preference_save(preference_store, enabled)

        original_state_save = PersonalProfileStore.save_removal_state
        state_save_calls = 0

        def fail_narrowed_state_save(
            profile_store: PersonalProfileStore, state: PersonalRemovalState
        ) -> None:
            nonlocal state_save_calls
            state_save_calls += 1
            if state_save_calls == 2:
                raise PersonalProfileError("personal_removal_state_write_failed")
            original_state_save(profile_store, state)

        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            mock.patch.object(
                ContextPreferenceStore,
                "save",
                autospec=True,
                side_effect=fail_connect_and_preference_rollback,
            ),
            mock.patch.object(
                PersonalProfileStore,
                "save_removal_state",
                autospec=True,
                side_effect=fail_narrowed_state_save,
            ),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-replacement-test-only"},
                clear=False,
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(connect), 1)

        profiles = PersonalProfileStore(self.state)
        self.assertTrue(profiles.entry_exists(key))
        removal_state = profiles.load_removal_state(key)
        self.assertIsNotNone(removal_state)
        assert removal_state is not None
        retained_profile = profiles.load(key)
        self.assertFalse(removal_state.credential_cleanup_required)
        self.assertEqual(removal_state.preference_action, "disable")
        self.assertEqual(
            removal_state.profile_generation,
            retained_profile.generation,
        )
        self.assertEqual(credentials.get(key), previous_secret)

        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                side_effect=AssertionError(
                    "safe retained-state cleanup must preserve the old credential"
                ),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 0)
        self.assertFalse(profiles.entry_exists(key))
        self.assertFalse(profiles.removal_state_path_for(key).exists())
        self.assertFalse(preference.load().enabled)
        self.assertEqual(credentials.get(key), previous_secret)

    def test_preference_change_serializes_with_managed_profile_removal(self) -> None:
        profiles = PersonalProfileStore(self.state)
        profiles.save(
            self.profile(
                mode="managed",
                provider="hormuz",
                endpoint="https://gateway.example",
                managed_profile="managed-source",
                credential_env=None,
                previous_preference_enabled=True,
            )
        )
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        off = argparse.Namespace(
            personal_command="off",
            profile="personal-a",
            state_directory=self.state,
        )
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        off_started = threading.Event()
        release_off = threading.Event()
        original_save = ContextPreferenceStore.save

        def blocking_save(
            preference_store: ContextPreferenceStore, enabled: bool
        ) -> None:
            if not enabled:
                off_started.set()
                if not release_off.wait(5):
                    raise AssertionError("test did not release preference update")
            original_save(preference_store, enabled)

        results: dict[str, int] = {}
        off_thread = threading.Thread(
            target=lambda: results.setdefault("off", personal_commands.run(off))
        )
        remove_thread = threading.Thread(
            target=lambda: results.setdefault("remove", personal_commands.run(remove))
        )
        with (
            mock.patch.object(
                ContextPreferenceStore,
                "save",
                autospec=True,
                side_effect=blocking_save,
            ),
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                side_effect=AssertionError("managed removal must not open a keyring"),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            off_thread.start()
            self.assertTrue(off_started.wait(5))
            remove_thread.start()
            remove_thread.join(timeout=0.2)
            self.assertTrue(remove_thread.is_alive())
            release_off.set()
            off_thread.join(timeout=5)
            remove_thread.join(timeout=5)
        self.assertFalse(off_thread.is_alive())
        self.assertFalse(remove_thread.is_alive())
        self.assertEqual(results, {"off": 0, "remove": 0})
        self.assertFalse(profiles.path_for("personal-a").exists())
        self.assertTrue(preference.load().enabled)

    def test_repeated_remove_retries_credential_cleanup_without_touching_preference(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("personal-a", DIRECT_TOKEN)
        PersonalProfileStore(self.state).save(self.profile())
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(False)
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        delete_calls = 0
        original_delete = credentials.delete

        def fail_once(key: str) -> None:
            nonlocal delete_calls
            delete_calls += 1
            if delete_calls == 1:
                raise CredentialStoreError("secure_store_unavailable")
            original_delete(key)

        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            mock.patch.object(credentials, "delete", side_effect=fail_once),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 1)
            self.assertFalse(PersonalProfileStore(self.state).path_for("personal-a").exists())
            self.assertTrue(
                PersonalProfileStore(self.state)
                .removal_state_path_for("personal-a")
                .exists()
            )
            self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)
            self.assertFalse(preference.load().enabled)
            self.assertEqual(personal_commands.run(remove), 0)
        self.assertEqual(delete_calls, 2)
        self.assertIsNone(credentials.get("personal-a"))
        self.assertFalse(preference.load().enabled)
        self.assertFalse(
            PersonalProfileStore(self.state)
            .removal_state_path_for("personal-a")
            .exists()
        )

    def test_interrupted_remove_resumes_from_content_free_state(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("personal-a", DIRECT_TOKEN)
        PersonalProfileStore(self.state).save(
            self.profile(previous_preference_enabled=False)
        )
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        PersonalMetricsStore(self.state, "personal-a").record_session()
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            mock.patch.object(
                personal_commands,
                "_complete_removal_cleanup",
                side_effect=KeyboardInterrupt,
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 130)
        profiles = PersonalProfileStore(self.state)
        self.assertFalse(profiles.path_for("personal-a").exists())
        self.assertTrue(profiles.removal_state_path_for("personal-a").exists())
        self.assertEqual(credentials.get("personal-a"), DIRECT_TOKEN)
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 0)
        self.assertIsNone(credentials.get("personal-a"))
        self.assertFalse(preference.load().enabled)
        self.assertFalse(PersonalMetricsStore(self.state, "personal-a").path.exists())
        self.assertFalse(profiles.removal_state_path_for("personal-a").exists())

    def test_credential_update_serializes_with_profile_removal(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("personal-a", DIRECT_TOKEN)
        PersonalProfileStore(self.state).save(self.profile())
        credential = argparse.Namespace(
            personal_command="credential",
            profile="personal-a",
            state_directory=self.state,
            credential_env="OPENAI_API_KEY",
        )
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        set_started = threading.Event()
        release_set = threading.Event()
        original_set = credentials.set

        def blocking_set(key: str, secret: str) -> None:
            set_started.set()
            if not release_set.wait(5):
                raise AssertionError("test did not release credential replacement")
            original_set(key, secret)

        results: dict[str, int] = {}
        credential_thread = threading.Thread(
            target=lambda: results.setdefault(
                "credential", personal_commands.run(credential)
            )
        )
        remove_thread = threading.Thread(
            target=lambda: results.setdefault("remove", personal_commands.run(remove))
        )
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            mock.patch.object(credentials, "set", side_effect=blocking_set),
            mock.patch.dict(
                os.environ, {"OPENAI_API_KEY": "sk-replacement-test-only"}, clear=False
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            credential_thread.start()
            self.assertTrue(set_started.wait(5))
            remove_thread.start()
            remove_thread.join(timeout=0.2)
            self.assertTrue(remove_thread.is_alive())
            release_set.set()
            credential_thread.join(timeout=5)
            remove_thread.join(timeout=5)
        self.assertFalse(credential_thread.is_alive())
        self.assertFalse(remove_thread.is_alive())
        self.assertEqual(results, {"credential": 0, "remove": 0})
        self.assertFalse(PersonalProfileStore(self.state).path_for("personal-a").exists())
        self.assertIsNone(credentials.get("personal-a"))

    def test_invalid_metrics_do_not_block_profile_removal(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("personal-a", DIRECT_TOKEN)
        PersonalProfileStore(self.state).save(self.profile())
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        metrics.record_session()
        outside = self.state / "preserved-metrics.json"
        outside.write_text("preserve", encoding="utf-8")
        metrics.path.unlink()
        metrics.path.symlink_to(outside)
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 1)
        self.assertFalse(PersonalProfileStore(self.state).path_for("personal-a").exists())
        self.assertIsNone(credentials.get("personal-a"))
        self.assertFalse(preference.path.exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")

    def test_invalid_preference_does_not_block_profile_removal(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("personal-a", DIRECT_TOKEN)
        PersonalProfileStore(self.state).save(self.profile())
        preference = ContextPreferenceStore(self.state, "personal-a")
        outside = self.state / "preserved-preference.json"
        outside.write_text("preserve", encoding="utf-8")
        outside.chmod(0o600)
        preference.path.symlink_to(outside)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        metrics.record_session()
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 1)
        self.assertFalse(PersonalProfileStore(self.state).path_for("personal-a").exists())
        self.assertIsNone(credentials.get("personal-a"))
        self.assertFalse(metrics.path.exists())
        self.assertTrue(preference.path.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")

    def test_post_unlink_profile_error_still_cleans_ancillary_state(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("personal-a", DIRECT_TOKEN)
        PersonalProfileStore(self.state).save(self.profile())
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        metrics.record_session()
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        real_fsync = os.fsync
        fsync_calls = 0

        def fail_profile_unlink_fsync(descriptor: int) -> None:
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 3:
                raise OSError("post-unlink directory sync failed")
            real_fsync(descriptor)

        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            mock.patch(
                "hormuz.personal_profiles.os.fsync",
                side_effect=fail_profile_unlink_fsync,
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 1)
        self.assertGreaterEqual(fsync_calls, 3)
        self.assertFalse(PersonalProfileStore(self.state).path_for("personal-a").exists())
        self.assertIsNone(credentials.get("personal-a"))
        self.assertFalse(preference.path.exists())
        self.assertFalse(metrics.path.exists())

    def test_remove_serializes_reconnect_until_ancillary_cleanup_finishes(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("personal-a", DIRECT_TOKEN)
        PersonalProfileStore(self.state).save(self.profile())
        ContextPreferenceStore(self.state, "personal-a").save(True)
        PersonalMetricsStore(self.state, "personal-a").record_session()
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        connect = argparse.Namespace(
            personal_command="connect",
            profile="personal-a",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="qualified-model",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        delete_started = threading.Event()
        release_delete = threading.Event()
        original_delete = credentials.delete

        def blocking_delete(key: str) -> None:
            delete_started.set()
            if not release_delete.wait(5):
                raise AssertionError("test did not release credential cleanup")
            original_delete(key)

        results: dict[str, int] = {}
        remove_thread = threading.Thread(
            target=lambda: results.setdefault("remove", personal_commands.run(remove))
        )
        connect_thread = threading.Thread(
            target=lambda: results.setdefault("connect", personal_commands.run(connect))
        )
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            mock.patch.object(credentials, "delete", side_effect=blocking_delete),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-reconnected-test-only"},
                clear=False,
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            remove_thread.start()
            self.assertTrue(delete_started.wait(5))
            connect_thread.start()
            connect_thread.join(timeout=0.2)
            self.assertTrue(connect_thread.is_alive())
            release_delete.set()
            remove_thread.join(timeout=5)
            connect_thread.join(timeout=5)
        self.assertFalse(remove_thread.is_alive())
        self.assertFalse(connect_thread.is_alive())
        self.assertEqual(results, {"remove": 0, "connect": 0})
        self.assertEqual(credentials.get("personal-a"), "sk-reconnected-test-only")
        self.assertEqual(
            PersonalProfileStore(self.state).load("personal-a").key, "personal-a"
        )
        self.assertTrue(ContextPreferenceStore(self.state, "personal-a").load().enabled)

    def test_remove_recovers_from_a_corrupt_profile_without_guessing_preference(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        profiles = PersonalProfileStore(self.state)
        profiles.save(
            self.profile(
                mode="managed",
                provider="hormuz",
                endpoint="https://gateway.example",
                managed_profile="managed-source",
                credential_env=None,
                previous_preference_enabled=True,
            )
        )
        profile_path = profiles.path_for("personal-a")
        profile_path.write_text("{not-json", encoding="utf-8")
        profile_path.chmod(0o600)
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        metrics.record_session()
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 0)
        self.assertFalse(profile_path.exists())
        self.assertIsNone(credentials.get("personal-a"))
        self.assertTrue(preference.load().enabled)
        self.assertFalse(metrics.path.exists())

    def test_remove_unlinks_a_dangling_profile_symlink_without_following_it(self) -> None:
        profiles = PersonalProfileStore(self.state)
        profiles.directory.mkdir(parents=True, mode=0o700)
        profile_path = profiles.path_for("personal-a")
        missing_target = self.state / "must-remain-missing"
        profile_path.symlink_to(missing_target)
        remove = argparse.Namespace(
            personal_command="remove",
            profile="personal-a",
            state_directory=self.state,
        )
        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                return_value=ProviderCredentialStore(
                    _MemoryKeyring(), trust_injected_backend=True
                ),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 0)
        self.assertFalse(profile_path.is_symlink())
        self.assertFalse(missing_target.exists())

    def test_failed_connect_restores_a_preexisting_keyring_credential(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set("rollback-profile", "sk-preexisting-test-only")
        args = argparse.Namespace(
            personal_command="connect",
            profile="rollback-profile",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="gpt-5.4",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                return_value=credentials,
            ),
            mock.patch.object(
                ContextPreferenceStore,
                "save",
                side_effect=ContextRuntimeError("settings_write_failed"),
            ),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-new-test-only"},
                clear=False,
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(args), 1)
        self.assertEqual(credentials.get("rollback-profile"), "sk-preexisting-test-only")
        self.assertFalse(
            PersonalProfileStore(self.state).path_for("rollback-profile").exists()
        )

    def test_failed_connect_restores_a_preexisting_preference(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        preference = ContextPreferenceStore(self.state, "rollback-preference")
        preference.save(True)
        args = argparse.Namespace(
            personal_command="connect",
            profile="rollback-preference",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="gpt-5.4",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                return_value=credentials,
            ),
            mock.patch.object(
                credentials,
                "set",
                side_effect=CredentialStoreError("credential_write_failed"),
            ),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-new-test-only"},
                clear=False,
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(args), 1)
        self.assertTrue(preference.load().enabled)
        self.assertFalse(
            PersonalProfileStore(self.state).path_for("rollback-preference").exists()
        )

    def test_failed_connect_removes_profile_even_when_credential_rollback_fails(self) -> None:
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        args = argparse.Namespace(
            personal_command="connect",
            profile="rollback-cleanup",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="gpt-5.4",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        remove = argparse.Namespace(
            personal_command="remove",
            profile="rollback-cleanup",
            state_directory=self.state,
        )
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            mock.patch.object(
                ContextPreferenceStore,
                "save",
                side_effect=ContextRuntimeError("settings_write_failed"),
            ),
            mock.patch.object(
                credentials,
                "delete",
                side_effect=CredentialStoreError("secure_store_unavailable"),
            ),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-new-test-only"},
                clear=False,
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(args), 1)
        self.assertFalse(
            PersonalProfileStore(self.state).path_for("rollback-cleanup").exists()
        )
        self.assertTrue(
            PersonalProfileStore(self.state)
            .removal_state_path_for("rollback-cleanup")
            .exists()
        )
        self.assertEqual(credentials.get("rollback-cleanup"), "sk-new-test-only")
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(personal_commands.run(remove), 0)
        self.assertIsNone(credentials.get("rollback-cleanup"))
        self.assertFalse(
            PersonalProfileStore(self.state)
            .removal_state_path_for("rollback-cleanup")
            .exists()
        )

    def test_interrupted_connect_rolls_back_profile_and_credential(self) -> None:
        class InterruptingKeyring(_MemoryKeyring):
            def set_password(self, service: str, username: str, password: str) -> None:
                super().set_password(service, username, password)
                raise KeyboardInterrupt

        credentials = ProviderCredentialStore(
            InterruptingKeyring(), trust_injected_backend=True
        )
        args = argparse.Namespace(
            personal_command="connect",
            profile="interrupted-profile",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint="https://api.openai.com",
            model="gpt-5.4",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=False,
        )
        with (
            mock.patch.object(
                personal_commands,
                "ProviderCredentialStore",
                return_value=credentials,
            ),
            mock.patch.dict(
                os.environ,
                {"PERSONAL_TEST_PROVIDER_KEY": "sk-interrupted-test-only"},
                clear=False,
            ),
        ):
            self.assertEqual(personal_commands.run(args), 130)
        self.assertIsNone(credentials.get("interrupted-profile"))
        self.assertFalse(
            PersonalProfileStore(self.state).path_for("interrupted-profile").exists()
        )

    def test_adapter_conformance_scrubs_provider_secret_and_keeps_local_tools(self) -> None:
        for name in ("codex", "claude-code", "aider"):
            with self.subTest(agent=name):
                report = conformance_report(adapter_for(name))
                self.assertTrue(report["passed"])

    def test_product_owned_qualification_fixture_passes_without_provider_calls(self) -> None:
        report = run_product_qualification()
        self.assertTrue(report["passed"])
        self.assertEqual(report["provider_calls"], 0)
        self.assertRegex(report["fixture_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreaterEqual(len(report["checks"]), 9)
        self.assertEqual(report["qualification_kind"], "provider_free_contract")
        self.assertEqual(
            report["experiment_status"]["cycle_5"],
            "no_builtin_adapter_execution_yield",
        )
        self.assertTrue(
            all(
                check["scope"]
                in {"adapter_contract", "release_contract", "experimental_contract"}
                for check in report["checks"]
            )
        )

    def test_metrics_distinguish_missing_from_zero_and_report_whole_workload(self) -> None:
        store = PersonalMetricsStore(self.state, "personal-a")
        store.record_optimization(
            OptimizationMeasurement(
                eligible=False,
                applied=False,
                reason="no_eligible_result",
                before_bytes=100,
                after_bytes=100,
                before_tokens={"cl100k_base": 100, "o200k_base": 100},
                after_tokens={"cl100k_base": 100, "o200k_base": 100},
                overhead_us=8,
            )
        )
        store.record_optimization(
            OptimizationMeasurement(
                eligible=True,
                applied=True,
                reason="compacted",
                before_bytes=100,
                after_bytes=40,
                before_tokens={"cl100k_base": 100, "o200k_base": 100},
                after_tokens={"cl100k_base": 40, "o200k_base": 40},
                overhead_us=12,
            )
        )
        store.record_provider(
            ProviderMeasurement(
                succeeded=True,
                cancelled=False,
                response_received=True,
                response_bytes=20,
                time_to_first_byte_ms=5,
                total_latency_ms=9,
                usage={
                    "input_tokens": 0,
                    "output_tokens": 2,
                    "cache_read_tokens": None,
                    "cache_write_tokens": None,
                    "reasoning_tokens": 0,
                    "total_tokens": 2,
                    "actual_cost_microusd": None,
                },
            )
        )
        benefit = store.benefit(enabled=True)
        self.assertEqual(benefit["traffic"], {
            "total_requests": 2, "eligible_requests": 1, "transformed_requests": 1
        })
        self.assertEqual(
            benefit["estimated_token_reduction"]["all_captured_traffic"]["tokens"], 60
        )
        self.assertEqual(
            benefit["estimated_token_reduction"]["eligible_traffic"]["percent"], 60.0
        )
        self.assertTrue(
            benefit["estimated_token_reduction"]["all_captured_traffic"]["complete"]
        )
        self.assertEqual(
            benefit["request_byte_reduction"]["all_captured_traffic"]["bytes"], 60
        )
        self.assertEqual(benefit["provider_reported"]["input_tokens"]["total"], 0)
        self.assertEqual(benefit["provider_reported"]["total_tokens"]["total"], 2)
        self.assertEqual(benefit["provider_reported"]["cache_read_tokens"]["missing"], 1)
        self.assertEqual(
            benefit["provider_activity"],
            {"attempts": 1, "responses": 1, "failures": 0, "cancelled": 0},
        )
        self.assertIsNone(benefit["cost"]["actual_microusd"]["total"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            personal_commands._print_document(benefit, as_json=False)
        self.assertIn("total=2", output.getvalue())
        self.assertIn("actual=unavailable microusd", output.getvalue())
        raw = store.path.read_text(encoding="utf-8")
        self.assertNotIn("prompt", raw)
        self.assertNotIn("response-test", raw)

    def test_metrics_serialize_updates_across_store_instances(self) -> None:
        workers = 16
        barrier = threading.Barrier(workers)
        failures: list[Exception] = []

        def record() -> None:
            try:
                barrier.wait(timeout=5)
                PersonalMetricsStore(self.state, "personal-a").record_session()
            except Exception as error:  # pragma: no cover - asserted below
                failures.append(error)

        threads = [threading.Thread(target=record) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(
            PersonalMetricsStore(self.state, "personal-a").snapshot()["counters"][
                "sessions_started"
            ],
            workers,
        )

    def test_active_days_remain_sorted_when_the_clock_moves_backward(self) -> None:
        store = PersonalMetricsStore(self.state, "personal-a")
        later = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
        earlier = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
        with mock.patch("hormuz.personal_metrics.datetime", wraps=datetime) as clock:
            clock.now.side_effect = [later, earlier]
            store.record_session()
            store.record_session()
        self.assertEqual(
            store.snapshot()["active_days"],
            [earlier.date().toordinal(), later.date().toordinal()],
        )

    def test_usage_parser_handles_openai_sse_and_anthropic_json(self) -> None:
        sse = (
            b'data: {"type":"response.completed","response":{"usage":'
            b'{"input_tokens":10,"output_tokens":3,"input_tokens_details":'
            b'{"cached_tokens":4},"output_tokens_details":{"reasoning_tokens":1}}}}\n\n'
            b"data: [DONE]\n\n"
        )
        usage = extract_provider_usage(sse, "responses", "text/event-stream")
        self.assertEqual(
            (usage["input_tokens"], usage["cache_read_tokens"], usage["reasoning_tokens"]),
            (10, 4, 1),
        )
        anthropic = json.dumps({
            "usage": {"input_tokens": 12, "output_tokens": 4,
                      "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2}
        }).encode()
        usage = extract_provider_usage(anthropic, "anthropic", "application/json")
        self.assertEqual((usage["cache_read_tokens"], usage["cache_write_tokens"]), (5, 2))

    def test_metrics_clear_refuses_a_replaced_symlink(self) -> None:
        store = PersonalMetricsStore(self.state, "personal-a")
        store.record_session()
        outside = self.state / "must-remain.json"
        outside.write_text("preserve", encoding="utf-8")
        store.path.unlink()
        store.path.symlink_to(outside)
        with self.assertRaisesRegex(PersonalMetricsError, "metrics_invalid"):
            store.clear()
        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")

    def test_measurement_clear_serializes_with_profile_replacement(self) -> None:
        profile = self.profile()
        profiles = PersonalProfileStore(self.state)
        profiles.save(profile)
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set(profile.key, DIRECT_TOKEN)
        PersonalMetricsStore(
            self.state,
            profile.key,
            profile_generation=profile.generation,
        ).record_session()
        clear = argparse.Namespace(
            personal_command="clear",
            profile=profile.key,
            state_directory=self.state,
        )
        remove = argparse.Namespace(
            personal_command="remove",
            profile=profile.key,
            state_directory=self.state,
        )
        reconnect = argparse.Namespace(
            personal_command="connect",
            profile=profile.key,
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint=profile.endpoint,
            model=profile.model,
            credential_env=profile.credential_env,
            allow_loopback_http=False,
        )
        clear_started = threading.Event()
        release_clear = threading.Event()
        original_clear = PersonalMetricsStore.clear
        results: dict[str, int] = {}
        failures: list[BaseException] = []

        def blocking_clear(store: PersonalMetricsStore) -> bool:
            if not clear_started.is_set():
                clear_started.set()
                if not release_clear.wait(5):
                    raise AssertionError("test did not release measurement clear")
            return original_clear(store)

        def replace_profile() -> None:
            try:
                results["remove"] = personal_commands.run(remove)
                results["connect"] = personal_commands.run(reconnect)
                replacement = profiles.load(profile.key)
                PersonalMetricsStore(
                    self.state,
                    replacement.key,
                    profile_generation=replacement.generation,
                ).record_session()
            except BaseException as error:  # pragma: no cover - asserted below
                failures.append(error)

        clear_thread = threading.Thread(
            target=lambda: results.setdefault("clear", personal_commands.run(clear))
        )
        replacement_thread = threading.Thread(target=replace_profile)
        with (
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            mock.patch.object(
                PersonalMetricsStore,
                "clear",
                autospec=True,
                side_effect=blocking_clear,
            ),
            mock.patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "sk-reconnected-test-only"},
                clear=False,
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            clear_thread.start()
            self.assertTrue(clear_started.wait(5))
            replacement_thread.start()
            try:
                replacement_thread.join(timeout=0.2)
                self.assertTrue(replacement_thread.is_alive())
            finally:
                release_clear.set()
                clear_thread.join(timeout=5)
                replacement_thread.join(timeout=5)
        self.assertFalse(clear_thread.is_alive())
        self.assertFalse(replacement_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(results, {"clear": 0, "remove": 0, "connect": 0})
        replacement = profiles.load(profile.key)
        self.assertNotEqual(replacement.generation, profile.generation)
        self.assertEqual(
            PersonalMetricsStore(
                self.state,
                replacement.key,
                profile_generation=replacement.generation,
            ).snapshot()["counters"]["sessions_started"],
            1,
        )

    def test_measurement_failure_does_not_block_the_ordinary_relay_path(self) -> None:
        class FailingMetrics:
            def record_optimization(self, _measurement) -> None:
                raise PersonalMetricsError("metrics_write_failed")

        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        optimizer = RelayOptimizer(
            preference_store=preference,
            client="codex",
            gateway_compatible=True,
            counters=COUNTERS,
            metrics=FailingMetrics(),  # type: ignore[arg-type]
        )
        paths = "".join(f"src/generated/item_{index}.py\n" for index in range(100))
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "rg --files src/generated"}),
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": paths,
                    },
                ]
            }
        ).encode()
        changed, _headers = optimizer.prepare(body, "/v1/responses")
        self.assertNotEqual(changed, body)
        self.assertIsNone(optimizer.metrics)

    def test_claude_count_tokens_is_supported_passthrough_traffic(self) -> None:
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        optimizer = RelayOptimizer(
            preference_store=preference,
            client="claude-code",
            gateway_compatible=True,
            counters=COUNTERS,
            metrics=metrics,
        )
        body = b'{"messages":[{"role":"user","content":"hello"}]}'
        changed, headers = optimizer.prepare(body, "/v1/messages/count_tokens")
        self.assertEqual(changed, body)
        self.assertEqual(headers, {})
        self.assertEqual(optimizer.status.code, "ready")
        benefit = metrics.benefit(enabled=True)
        self.assertEqual(benefit["traffic"]["total_requests"], 1)
        self.assertNotIn("unsupported_client", benefit["exceptions"])

    def test_optimizer_measures_the_actual_request_body_bytes(self) -> None:
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        optimizer = RelayOptimizer(
            preference_store=preference,
            client="codex",
            gateway_compatible=True,
            counters=COUNTERS,
            metrics=metrics,
        )
        paths = "".join(
            f"src/generated/very_long_item_name_{index}.py\n"
            for index in range(100)
        )
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "rg --files src/generated"}),
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": paths,
                    },
                ]
            },
            indent=4,
        ).encode()
        changed, _headers = optimizer.prepare(body, "/v1/responses")
        self.assertNotEqual(changed, body)
        snapshot = metrics.snapshot()
        self.assertEqual(
            snapshot["metrics"]["request_before_bytes"]["total"], len(body)
        )
        self.assertEqual(
            snapshot["metrics"]["request_after_bytes"]["total"], len(changed)
        )
        self.assertEqual(
            snapshot["metrics"]["cl100k_before_tokens"]["total"],
            len(body.decode("utf-8")),
        )
        self.assertEqual(
            snapshot["metrics"]["cl100k_after_tokens"]["total"],
            len(changed.decode("utf-8")),
        )

    def test_recognized_call_without_matching_result_is_not_eligible(self) -> None:
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        optimizer = RelayOptimizer(
            preference_store=preference,
            client="codex",
            gateway_compatible=True,
            counters=COUNTERS,
            metrics=metrics,
        )
        body = json.dumps(
            {
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call-without-result",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "rg --files src/generated"}),
                    }
                ]
            }
        ).encode()
        changed, headers = optimizer.prepare(body, "/v1/responses")
        self.assertEqual(changed, body)
        self.assertEqual(headers, {})
        self.assertEqual(optimizer.status.code, "ready")
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["counters"]["eligible_requests"], 0)
        self.assertEqual(snapshot["reasons"], {"no_eligible_result": 1})

    def test_public_adapter_example_preserves_non_provider_environment(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "HOME": "/tmp/example-home",
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "must-not-survive",
            },
            clear=True,
        ):
            plan = ExampleAgentAdapter().launch_plan(
                executable="example-agent",
                relay_origin="http://127.0.0.1:1234",
                local_credential=LOCAL_TOKEN,
                model="qualified-model",
            )
        self.assertEqual(plan.environment["HOME"], "/tmp/example-home")
        self.assertEqual(plan.environment["PATH"], "/usr/bin")
        self.assertNotIn("OPENAI_API_KEY", plan.environment)

    def test_managed_personal_run_uses_the_managed_client_version_pin(self) -> None:
        profile = self.profile(
            key="managed-personal",
            mode="managed",
            provider="hormuz",
            endpoint="http://127.0.0.1:9",
            allow_insecure_http=True,
            managed_profile="managed-source",
            credential_env=None,
        )
        with (
            mock.patch(
                "hormuz.personal_runtime.supported_client_executable",
                return_value="/usr/bin/true",
            ) as supported,
            mock.patch(
                "hormuz.personal_runtime.probe_gateway_capability",
                return_value=True,
            ),
            mock.patch(
                "hormuz.personal_runtime.subprocess.run",
                return_value=mock.Mock(returncode=0),
            ) as launched,
            mock.patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "must-not-reach-managed-version-probe",
                    "CODEX_API_KEY": "must-not-reach-managed-version-probe",
                    "ANTHROPIC_API_KEY": "must-not-reach-managed-version-probe",
                    "TOOL_INTEGRATION_TOKEN": "preserved-tool-token",
                },
                clear=True,
            ),
        ):
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: "hox_a_" + "a" * 43,
                    counters=COUNTERS,
                ),
                0,
            )
        supported.assert_called_once()
        self.assertEqual(supported.call_args.args, ("codex",))
        self.assertEqual(
            supported.call_args.kwargs["expected_version"],
            SUPPORTED_CLIENT_VERSIONS["codex"],
        )
        probe_environment = supported.call_args.kwargs["environment"]
        self.assertNotIn("OPENAI_API_KEY", probe_environment)
        self.assertNotIn("CODEX_API_KEY", probe_environment)
        self.assertNotIn("ANTHROPIC_API_KEY", probe_environment)
        self.assertEqual(
            probe_environment["TOOL_INTEGRATION_TOKEN"], "preserved-tool-token"
        )
        launch_environment = launched.call_args.kwargs["env"]
        self.assertNotIn("OPENAI_API_KEY", launch_environment)
        self.assertNotIn("CODEX_API_KEY", launch_environment)
        self.assertEqual(
            launch_environment["ANTHROPIC_API_KEY"],
            "must-not-reach-managed-version-probe",
        )
        self.assertEqual(
            launch_environment["TOOL_INTEGRATION_TOKEN"], "preserved-tool-token"
        )

    def test_direct_version_probe_cannot_receive_the_provider_credential(self) -> None:
        profile = self.profile(
            key="sanitized-version-probe",
            endpoint="http://127.0.0.1:9",
            allow_insecure_http=True,
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
        )
        with (
            mock.patch(
                "hormuz.personal_runtime.supported_client_executable",
                return_value="/usr/bin/true",
            ) as supported,
            mock.patch.dict(
                os.environ,
                {
                    "PERSONAL_TEST_PROVIDER_KEY": DIRECT_TOKEN,
                    "PROVIDER_SECRET_ALIAS": DIRECT_TOKEN,
                    "TOOL_INTEGRATION_TOKEN": "preserved-tool-token",
                },
                clear=True,
            ),
        ):
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: DIRECT_TOKEN,
                    counters=COUNTERS,
                ),
                0,
            )
        probe_environment = supported.call_args.kwargs["environment"]
        self.assertNotIn("PERSONAL_TEST_PROVIDER_KEY", probe_environment)
        self.assertNotIn(DIRECT_TOKEN, probe_environment.values())
        self.assertEqual(
            probe_environment["TOOL_INTEGRATION_TOKEN"], "preserved-tool-token"
        )

    def test_session_measurement_failure_does_not_prevent_agent_launch(self) -> None:
        class FailingMetrics:
            def record_session(self, *, completed: bool = False) -> None:
                raise PersonalMetricsError("metrics_write_failed")

        profile = self.profile(
            key="metrics-fail-open",
            endpoint="http://127.0.0.1:9",
            allow_insecure_http=True,
        )
        with mock.patch(
            "hormuz.personal_runtime.PersonalMetricsStore",
            return_value=FailingMetrics(),
        ):
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: DIRECT_TOKEN,
                    counters=COUNTERS,
                    executable="/usr/bin/true",
                ),
                0,
            )

    def test_active_run_cannot_recreate_metrics_after_remove(self) -> None:
        profile = self.profile(
            endpoint="http://127.0.0.1:9",
            allow_insecure_http=True,
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
        )
        profiles = PersonalProfileStore(self.state)
        profiles.save(profile)
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        credentials.set(profile.key, DIRECT_TOKEN)
        stale_credential = direct_credential_reader(
            credentials,
            profile.key,
            state_directory=self.state,
            profile_generation=profile.generation,
        )
        remove = argparse.Namespace(
            personal_command="remove",
            profile=profile.key,
            state_directory=self.state,
        )
        reconnect = argparse.Namespace(
            personal_command="connect",
            profile=profile.key,
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint=profile.endpoint,
            model=profile.model,
            credential_env=profile.credential_env,
            allow_loopback_http=True,
        )
        launched = threading.Event()
        release_launch = threading.Event()
        outcomes: list[int] = []
        failures: list[BaseException] = []

        def block_agent(*_args, **_kwargs):
            launched.set()
            if not release_launch.wait(5):
                raise AssertionError("test did not release the active agent")
            return mock.Mock(returncode=0)

        def run_agent() -> None:
            try:
                outcomes.append(
                    run_personal_client(
                        profile=profile,
                        state_directory=self.state,
                        upstream_credential=lambda: DIRECT_TOKEN,
                        counters=COUNTERS,
                        executable="/usr/bin/true",
                    )
                )
            except BaseException as error:  # pragma: no cover - asserted below
                failures.append(error)

        agent_thread = threading.Thread(target=run_agent)
        guarded_metrics = PersonalMetricsStore(
            self.state,
            profile.key,
            profile_generation=profile.generation,
        )
        replacement_metrics: PersonalMetricsStore | None = None
        with (
            mock.patch(
                "hormuz.personal_runtime.subprocess.run", side_effect=block_agent
            ),
            mock.patch.object(
                personal_commands, "ProviderCredentialStore", return_value=credentials
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            try:
                agent_thread.start()
                self.assertTrue(launched.wait(5), failures)
                self.assertTrue(guarded_metrics.path.exists())
                self.assertEqual(personal_commands.run(remove), 0)
                self.assertFalse(guarded_metrics.path.exists())
                with self.assertRaisesRegex(
                    PersonalMetricsError, "metrics_profile_removed"
                ):
                    guarded_metrics.record_session(completed=True)
                with mock.patch.dict(
                    os.environ,
                    {"PERSONAL_TEST_PROVIDER_KEY": "sk-reconnected-test-only"},
                    clear=False,
                ):
                    self.assertEqual(personal_commands.run(reconnect), 0)
                replacement = profiles.load(profile.key)
                self.assertNotEqual(replacement.generation, profile.generation)
                with self.assertRaisesRegex(
                    ClientRelayError, "provider_credential_unavailable"
                ):
                    stale_credential()
                self.assertEqual(
                    direct_credential_reader(
                        credentials,
                        profile.key,
                        state_directory=self.state,
                        profile_generation=replacement.generation,
                    )(),
                    "sk-reconnected-test-only",
                )
                replacement_metrics = PersonalMetricsStore(
                    self.state,
                    profile.key,
                    profile_generation=replacement.generation,
                )
                replacement_metrics.record_session()
                with self.assertRaisesRegex(
                    PersonalMetricsError, "metrics_profile_replaced"
                ):
                    guarded_metrics.record_session(completed=True)
                with self.assertRaisesRegex(
                    PersonalMetricsError, "metrics_profile_replaced"
                ):
                    guarded_metrics.record_optimization(
                        OptimizationMeasurement(
                            eligible=False,
                            applied=False,
                            reason="disabled",
                            before_bytes=2,
                            after_bytes=2,
                            before_tokens={"cl100k_base": 2, "o200k_base": 2},
                            after_tokens={"cl100k_base": 2, "o200k_base": 2},
                            overhead_us=0,
                        )
                    )
            finally:
                release_launch.set()
                agent_thread.join(timeout=5)

        self.assertFalse(agent_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(outcomes, [0])
        self.assertIsNotNone(replacement_metrics)
        assert replacement_metrics is not None
        replacement_counters = replacement_metrics.snapshot()["counters"]
        self.assertEqual(replacement_counters["sessions_started"], 1)
        self.assertEqual(replacement_counters["sessions_completed"], 0)

    def test_execution_router_uses_exact_code_and_preserves_call_contract(self) -> None:
        request = MethodRequest(
            "step-1", "call-1", "json_get",
            {"document": {"items": [3, 7]}, "path": ["items", 1]},
            "read", False, False, 300,
        )
        result = ExecutionRouter().route(request)
        self.assertEqual((result.method, result.result, result.tool_call_id), ("deterministic", 7, "call-1"))
        self.assertTrue(result.model_step_avoided)
        unsafe = MethodRequest("step-2", "call-2", "json_count", [], "write", True, False)
        self.assertEqual(ExecutionRouter().route(unsafe).method, "model")

    def test_jev_requires_fixture_net_benefit_and_discloses_state_shape(self) -> None:
        def transport(_url, _headers, _body, _timeout):
            return json.dumps({
                "model": "jev-1.13.0",
                "answers": {"handler": {"type": "choice", "choice": "tests",
                                           "probabilities": {"tests": 0.91, "docs": 0.09},
                                           "confidence": 0.82}},
                "usage": {"input_tokens": 40, "output_tokens": 10},
            }).encode()

        jev = JevChoiceAdapter(
            credential="typesafe-test-key",
            qualification=JevQualification(True, 0.8, 100, 1000),
            developer_selected_provider=True,
            transport=transport,
        )
        request = MethodRequest(
            "step-1", "call-1", "handler_select", {"files": ["tests/a.py"]},
            "read", False, False, 500,
        )
        result = ExecutionRouter(jev=jev).route(
            request,
            options={"tests": "Testing work", "docs": "Documentation work"},
            instructions="Choose the supplied handler for this typed work item.",
        )
        self.assertEqual((result.method, result.result["choice"]), ("jev", "tests"))
        self.assertEqual(result.result["state_disclosure"]["top_level_fields"], ["files"])
        self.assertIsNone(result.usage["actual_cost_microusd"])

        unqualified = JevChoiceAdapter(
            credential="typesafe-test-key",
            qualification=JevQualification(False, 0.8, 100, 1000),
            developer_selected_provider=True,
            transport=transport,
        )
        self.assertEqual(
            ExecutionRouter(jev=unqualified).route(
                request, options={"tests": None, "docs": None}, instructions="Choose."
            ).method,
            "model",
        )
        with self.assertRaises(ExecutionMethodError):
            JevQualification(True, True, 100, 1000)  # type: ignore[arg-type]
        with self.assertRaises(ExecutionMethodError):
            JevChoiceAdapter(
                credential=" credential-with-space ",
                qualification=JevQualification(True, 0.8, 100, 1000),
                developer_selected_provider=True,
            )
        self.assertEqual(
            ExecutionRouter(jev=jev).route(
                request,
                options={"tests": object(), "docs": None},  # type: ignore[dict-item]
                instructions="Choose.",
            ).method,
            "model",
        )

    def test_jev_transport_rejects_redirects_before_forwarding_authorization(self) -> None:
        destination_authorizations: list[str | None] = []

        class DestinationHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                destination_authorizations.append(self.headers.get("Authorization"))
                self.send_response(204)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                self.do_GET()

            def log_message(self, format: str, *args: object) -> None:
                return

        destination = ThreadingHTTPServer(("127.0.0.1", 0), DestinationHandler)
        destination_url = f"http://127.0.0.1:{destination.server_port}/capture"

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(302)
                self.send_header("Location", destination_url)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        redirect_url = f"http://127.0.0.1:{redirect.server_port}/redirect"
        destination_thread = threading.Thread(target=destination.serve_forever)
        redirect_thread = threading.Thread(target=redirect.serve_forever)
        destination_thread.start()
        redirect_thread.start()
        try:
            with (
                mock.patch.object(execution_methods, "JEV_ENDPOINT", redirect_url),
                self.assertRaisesRegex(ExecutionMethodError, "jev_unavailable"),
            ):
                execution_methods._http_transport(
                    redirect_url,
                    {"Authorization": "Bearer jev-test-credential"},
                    b"{}",
                    2,
                )
            self.assertEqual(destination_authorizations, [])
        finally:
            redirect.shutdown()
            destination.shutdown()
            redirect.server_close()
            destination.server_close()
            redirect_thread.join(timeout=5)
            destination_thread.join(timeout=5)

    def test_semantic_experiment_preserves_constraints_questions_state_and_recovers(self) -> None:
        recovery = RecoveryBuffer()
        compactor = TypedToolHistoryCompactor(recovery, recent_turns=4)
        turns = (
            ConversationTurn("system", "Never write secrets.", ("Never write secrets.",)),
            ConversationTurn("user", "old resolved question " * 50),
            ConversationTurn("assistant", "old resolved answer " * 50),
            ConversationTurn("tool", "older state " * 50, tool_name="tests", tool_state="failed"),
            ConversationTurn("user", "What remains?", unresolved_question=True),
            ConversationTurn("assistant", "Investigating current state."),
            ConversationTurn("tool", "new state", tool_name="tests", tool_state="passed"),
            ConversationTurn("assistant", "Recent conclusion."),
        )
        result = compactor.compact(turns)
        self.assertGreater(result.before_bytes, result.after_bytes)
        self.assertIsNotNone(result.recovery_key)
        serialized = "\n".join(turn.content for turn in result.turns)
        self.assertIn("Never write secrets.", serialized)
        self.assertIn("What remains?", serialized)
        self.assertIn("passed", serialized)
        self.assertEqual(recovery.recover(result.recovery_key), turns)

    def test_behavior_signature_excludes_normal_loops_and_retries_only_safe_reads(self) -> None:
        tracker = BehaviorTracker()
        poll = BehaviorEvent(
            "check status", "status", "failure", "pending", "read", False, True,
            polling=True,
        )
        self.assertEqual(tracker.observe(poll).excluded_reason, "polling")
        failed = BehaviorEvent(
            "read config", "read_file", "failure", "temporarily_unavailable",
            "read", False, True,
        )
        self.assertFalse(tracker.observe(failed).possible_friction)
        signal = tracker.observe(failed)
        self.assertTrue(signal.possible_friction)
        self.assertEqual(BoundedReadRetry().decide(failed, signal).action, "retry_once")
        unsafe = BehaviorEvent(
            "publish release", "publish", "failure", "timeout", "write", True, True
        )
        unsafe_signal = tracker.observe(unsafe)
        unsafe_signal = tracker.observe(unsafe)
        self.assertEqual(BoundedReadRetry().decide(unsafe, unsafe_signal).action, "return_control")
        spoofed = FrictionSignal(
            True,
            "repeated_failed_tool_call",
            1,
            "observed_typed_events_only",
            "possible_friction_not_model_failure",
            None,
        )
        self.assertEqual(BoundedReadRetry().decide(failed, spoofed).action, "return_control")
        with self.assertRaises(PersonalOptimizationError):
            tracker.observe(
                BehaviorEvent(
                    "read config",
                    "read_file",
                    "failure",
                    "temporarily_unavailable",
                    "read",
                    False,
                    1,  # type: ignore[arg-type]
                )
            )
        with self.assertRaises(PersonalOptimizationError):
            InterventionComparison(-1, 0, 1.0, 1.0, False)

    def test_direct_relay_uses_provider_auth_records_usage_and_off_is_exact(self) -> None:
        provider = _DirectProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(True)
        metrics = PersonalMetricsStore(self.state, "personal-a")
        optimizer = RelayOptimizer(
            preference_store=preference,
            client="codex",
            gateway_compatible=True,
            counters=COUNTERS,
            metrics=metrics,
        )
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{provider.server_port}",
            client="codex",
            local_credential=LOCAL_TOKEN,
            gateway_credential=lambda: DIRECT_TOKEN,
            optimizer=optimizer,
            upstream_auth="openai",
            metrics=metrics,
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        try:
            paths = "".join(f"src/generated/very_long_item_name_{index}.py\n" for index in range(100))
            payload = {"model": "qualified-model", "input": [
                {"type": "function_call", "call_id": "call-1", "name": "exec_command",
                 "arguments": json.dumps({"cmd": "rg --files src/generated"})},
                {"type": "function_call_output", "call_id": "call-1", "output": paths},
            ]}
            original = json.dumps(payload, separators=(",", ":")).encode()
            connection = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
            connection.request("POST", "/v1/responses", body=original, headers={
                "Authorization": "Bearer " + LOCAL_TOKEN, "Content-Type": "application/json"
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
            changed, headers = provider.requests[0]
            self.assertNotEqual(changed, original)
            normalized = {name.lower(): value for name, value in headers.items()}
            self.assertEqual(normalized["authorization"], "Bearer " + DIRECT_TOKEN)
            self.assertNotIn("x-hormuz-context-format", normalized)

            preference.save(False)
            off = b'{ "model" : "qualified-model", "input" : [] }\n'
            connection = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
            connection.request("POST", "/v1/responses", body=off, headers={
                "Authorization": "Bearer " + LOCAL_TOKEN, "Content-Type": "application/json"
            })
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(provider.requests[1][0], off)
            deadline = time.monotonic() + 2
            while (
                metrics.snapshot()["counters"]["provider_responses"] < 2
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            benefit = metrics.benefit(enabled=False)
            self.assertEqual(benefit["traffic"]["total_requests"], 2)
            self.assertEqual(benefit["provider_reported"]["cache_read_tokens"]["total"], 20)
        finally:
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    def test_codex_model_catalog_probe_stays_local_and_requires_authentication(self) -> None:
        provider = _DirectProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{provider.server_port}",
            client="codex",
            local_credential=LOCAL_TOKEN,
            gateway_credential=lambda: DIRECT_TOKEN,
            optimizer=RelayOptimizer(
                preference_store=ContextPreferenceStore(self.state, "personal-a"),
                client="codex",
                gateway_compatible=True,
                counters=COUNTERS,
            ),
            upstream_auth="openai",
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        try:
            unauthenticated = http.client.HTTPConnection(
                "127.0.0.1", relay.server_port, timeout=5
            )
            unauthenticated.request("GET", "/v1/models?client_version=0.148.0")
            denied = unauthenticated.getresponse()
            self.assertEqual(denied.status, 401)
            denied.read()
            unauthenticated.close()

            authenticated = http.client.HTTPConnection(
                "127.0.0.1", relay.server_port, timeout=5
            )
            authenticated.request(
                "GET",
                "/v1/models?client_version=0.148.0",
                headers={"Authorization": "Bearer " + LOCAL_TOKEN},
            )
            response = authenticated.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"models": []})
            authenticated.close()
            self.assertEqual(provider.requests, [])
        finally:
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    def test_truncated_upstream_response_records_one_failed_provider_attempt(self) -> None:
        provider = _TruncatedProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        metrics = PersonalMetricsStore(self.state, "personal-a")
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(False)
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{provider.server_port}",
            client="codex",
            local_credential=LOCAL_TOKEN,
            gateway_credential=lambda: DIRECT_TOKEN,
            optimizer=RelayOptimizer(
                preference_store=preference,
                client="codex",
                gateway_compatible=True,
                counters=COUNTERS,
                metrics=metrics,
            ),
            upstream_auth="openai",
            metrics=metrics,
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", relay.server_port, timeout=5
            )
            connection.request(
                "POST",
                "/v1/responses",
                body=b'{"model":"gpt-5.4","input":[]}',
                headers={
                    "Authorization": "Bearer " + LOCAL_TOKEN,
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            try:
                response.read()
            except http.client.IncompleteRead:
                pass
            connection.close()
            snapshot = metrics.snapshot()
            self.assertEqual(snapshot["counters"]["provider_attempts"], 1)
            self.assertEqual(snapshot["counters"]["provider_responses"], 1)
            self.assertEqual(snapshot["counters"]["provider_failures"], 1)
        finally:
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    def test_truncated_local_upload_records_no_provider_attempt(self) -> None:
        metrics = PersonalMetricsStore(self.state, "personal-a")
        preference = ContextPreferenceStore(self.state, "personal-a")
        preference.save(False)
        relay = LocalRelayServer(
            gateway="http://127.0.0.1:9",
            client="codex",
            local_credential=LOCAL_TOKEN,
            gateway_credential=lambda: DIRECT_TOKEN,
            optimizer=RelayOptimizer(
                preference_store=preference,
                client="codex",
                gateway_compatible=True,
                counters=COUNTERS,
                metrics=metrics,
            ),
            upstream_auth="openai",
            metrics=metrics,
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        client = socket.create_connection(("127.0.0.1", relay.server_port), timeout=2)
        try:
            request = (
                "POST /v1/responses HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{relay.server_port}\r\n"
                f"Authorization: Bearer {LOCAL_TOKEN}\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: 100\r\n"
                "\r\n"
                "{}"
            ).encode()
            client.sendall(request)
            client.shutdown(socket.SHUT_WR)
            self.assertEqual(client.recv(4096), b"")
            snapshot = metrics.snapshot()
            self.assertEqual(snapshot["counters"]["provider_attempts"], 0)
            self.assertEqual(snapshot["counters"]["provider_failures"], 0)
        finally:
            client.close()
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()

    def test_complete_personal_journey_uses_transient_codex_configuration(self) -> None:
        provider = _DirectProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        backend = _MemoryKeyring()
        credentials = ProviderCredentialStore(backend, trust_injected_backend=True)
        sentinel = self.state / "existing-codex-config.toml"
        sentinel.write_text("model = 'developer-owned'\n", encoding="utf-8")
        sentinel.chmod(0o600)
        executable = self.state / "codex"
        executable.write_text(
            """#!/usr/bin/env python3
import http.client, json, os, re, sys, urllib.parse
if 'PERSONAL_TEST_PROVIDER_KEY' in os.environ:
    raise SystemExit(2)
provider = next(value for value in sys.argv if value.startswith('model_providers.hormuz_context_relay='))
origin = re.search(r'base_url=\"([^\"]+)/v1\"', provider).group(1)
url = urllib.parse.urlsplit(origin)
paths = ''.join(f'src/generated/very_long_item_name_{index}.py\\n' for index in range(100))
payload = {'model': 'qualified-model', 'input': [
    {'type': 'function_call', 'call_id': 'call-1', 'name': 'exec_command',
     'arguments': json.dumps({'cmd': 'rg --files src/generated'})},
    {'type': 'function_call_output', 'call_id': 'call-1', 'output': paths},
]}
body = json.dumps(payload, separators=(',', ':')).encode()
connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
connection.request('POST', '/v1/responses', body=body, headers={
    'Authorization': 'Bearer ' + os.environ['HORMUZ_LOCAL_RELAY_TOKEN'],
    'Content-Type': 'application/json',
})
response = connection.getresponse()
response.read()
raise SystemExit(0 if response.status == 200 else 1)
""",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        paths = "".join(
            f"src/generated/very_long_item_name_{index}.py\n" for index in range(100)
        )
        original = json.dumps(
            {
                "model": "qualified-model",
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "exec_command",
                        "arguments": json.dumps({"cmd": "rg --files src/generated"}),
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": paths,
                    },
                ],
            },
            separators=(",", ":"),
        ).encode()

        def qualified_run(**kwargs):
            return run_personal_client(
                **kwargs, counters=COUNTERS, executable=str(executable)
            )

        connect = argparse.Namespace(
            personal_command="connect",
            profile="journey-codex",
            state_directory=self.state,
            mode="direct",
            agent="codex",
            provider="openai",
            endpoint=f"http://127.0.0.1:{provider.server_port}",
            model="qualified-model",
            credential_env="PERSONAL_TEST_PROVIDER_KEY",
            allow_loopback_http=True,
        )
        output = io.StringIO()
        errors = io.StringIO()
        try:
            with (
                mock.patch.object(
                    personal_commands, "ProviderCredentialStore", return_value=credentials
                ),
                mock.patch.object(
                    personal_commands, "run_personal_client", side_effect=qualified_run
                ),
                mock.patch.dict(
                    os.environ, {"PERSONAL_TEST_PROVIDER_KEY": DIRECT_TOKEN}, clear=False
                ),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                self.assertEqual(personal_commands.run(connect), 0)
                os.environ["PERSONAL_TEST_PROVIDER_KEY"] = "sk-new-shell-value-test-only"
                command = lambda name: argparse.Namespace(
                    personal_command=name,
                    profile="journey-codex",
                    state_directory=self.state,
                )
                self.assertEqual(personal_commands.run(command("run")), 0)
                preference = ContextPreferenceStore(self.state, "journey-codex")
                benefit = PersonalMetricsStore(
                    self.state, "journey-codex"
                ).benefit(enabled=preference.load().enabled)
                self.assertEqual(benefit["traffic"]["transformed_requests"], 1)
                self.assertNotEqual(provider.requests[0][0], original)
                self.assertEqual(personal_commands.run(command("off")), 0)
                self.assertEqual(personal_commands.run(command("run")), 0)
                self.assertEqual(provider.requests[1][0], original)
                self.assertEqual(personal_commands.run(command("remove")), 0)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "model = 'developer-owned'\n")
            self.assertIsNone(credentials.get("journey-codex"))
        finally:
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    def test_aider_normal_work_reports_zero_structural_eligibility(self) -> None:
        provider = _DirectProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        executable = self.state / "aider"
        executable.write_text(
            """#!/usr/bin/env python3
import http.client, json, os, urllib.parse
url = urllib.parse.urlsplit(os.environ['OPENAI_API_BASE'])
body = json.dumps({'model': 'qualified-model', 'messages': [
    {'role': 'user', 'content': 'ordinary work'}
]}, separators=(',', ':')).encode()
connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
connection.request('POST', url.path + '/chat/completions', body=body, headers={
    'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'],
    'Content-Type': 'application/json',
})
response = connection.getresponse()
response.read()
raise SystemExit(0 if response.status == 200 else 1)
""",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        profile = self.profile(
            key="journey-aider", agent="aider", model="qualified-model",
            endpoint=f"http://127.0.0.1:{provider.server_port}",
            allow_insecure_http=True,
        )
        PersonalProfileStore(self.state).save(profile)
        ContextPreferenceStore(self.state, profile.key).save(True)
        try:
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: DIRECT_TOKEN,
                    counters=COUNTERS,
                    executable=str(executable),
                ),
                0,
            )
            benefit = PersonalMetricsStore(self.state, profile.key).benefit(enabled=True)
            self.assertEqual(
                benefit["traffic"],
                {"total_requests": 1, "eligible_requests": 0, "transformed_requests": 0},
            )
            self.assertEqual(len(provider.requests), 1)
            headers = {name.lower(): value for name, value in provider.requests[0][1].items()}
            self.assertEqual(headers["authorization"], "Bearer " + DIRECT_TOKEN)
        finally:
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    @unittest.skipUnless(
        os.environ.get("HORMUZ_RUN_PERSONAL_AIDER_TEST") == "1" and shutil.which("aider"),
        "Set HORMUZ_RUN_PERSONAL_AIDER_TEST=1 and install Aider 0.86.2",
    )
    def test_official_aider_0862_completes_direct_personal_interaction(self) -> None:
        real_aider = shutil.which("aider")
        assert real_aider is not None
        provider = _AiderProvider()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        root = self.state / "actual-aider-root"
        root.mkdir()
        executable_directory = self.state / "actual-aider-bin"
        executable_directory.mkdir()
        wrapper = executable_directory / "aider"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"real = {real_aider!r}\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    os.execv(real, [real, '--version'])\n"
            f"root = {str(root)!r}\n"
            "os.chdir(root)\n"
            "arguments = [real, *sys.argv[1:], '--message', 'Reply exactly OK.', "
            "'--no-git', '--no-stream', '--no-pretty', '--yes-always']\n"
            "os.execv(real, arguments)\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        profile = self.profile(
            key="actual-aider-0862",
            agent="aider",
            model="gpt-4.1-mini",
            endpoint=f"http://127.0.0.1:{provider.server_port}",
            allow_insecure_http=True,
        )
        PersonalProfileStore(self.state).save(profile)
        ContextPreferenceStore(self.state, profile.key).save(True)
        prior_path = os.environ.get("PATH")
        os.environ["PATH"] = str(executable_directory) + (
            ":" + prior_path if prior_path else ""
        )
        try:
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: DIRECT_TOKEN,
                    counters=COUNTERS,
                ),
                0,
            )
            self.assertGreaterEqual(len(provider.requests), 1)
            payload = json.loads(provider.requests[-1][0])
            self.assertTrue(payload.get("messages"))
            headers = {
                name.lower(): value for name, value in provider.requests[-1][1].items()
            }
            self.assertEqual(headers["authorization"], "Bearer " + DIRECT_TOKEN)
            benefit = PersonalMetricsStore(self.state, profile.key).benefit(enabled=True)
            self.assertGreaterEqual(benefit["traffic"]["total_requests"], 1)
            self.assertEqual(benefit["traffic"]["eligible_requests"], 0)
            self.assertEqual(benefit["traffic"]["transformed_requests"], 0)
        finally:
            if prior_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = prior_path
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()

    @unittest.skipUnless(
        os.environ.get("HORMUZ_RUN_PERSONAL_CODEX_TEST") == "1" and shutil.which("codex"),
        "Set HORMUZ_RUN_PERSONAL_CODEX_TEST=1 and install Codex CLI 0.148.0",
    )
    def test_official_codex_0148_completes_direct_personal_interaction(self) -> None:
        from tests.test_client_relay import _CodexToolGateway

        real_codex = shutil.which("codex")
        assert real_codex is not None
        provider = _CodexToolGateway()
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        root = self.state / "actual-codex-root"
        generated = root / "generated" / "structural_context_repetition_for_hormuz"
        generated.mkdir(parents=True)
        for index in range(48):
            (generated / f"file_{index:03}.py").write_text(
                "# personal optimizer probe\n", encoding="utf-8"
            )
        executable_directory = self.state / "actual-codex-bin"
        executable_directory.mkdir()
        wrapper = executable_directory / "codex"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"real = {real_codex!r}\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    os.execv(real, [real, '--version'])\n"
            f"root = {str(root)!r}\n"
            "arguments = [real, 'exec', '--ignore-user-config', '--skip-git-repo-check', "
            "'--ephemeral', '--dangerously-bypass-approvals-and-sandbox', '-C', root, "
            "*sys.argv[1:], 'Call exec_command with exactly `rg --files generated`, then finish.']\n"
            "os.execv(real, arguments)\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        profile = self.profile(
            key="actual-codex-0148",
            endpoint=f"http://127.0.0.1:{provider.server_port}",
            allow_insecure_http=True,
            model="gpt-5.4",
        )
        PersonalProfileStore(self.state).save(profile)
        ContextPreferenceStore(self.state, profile.key).save(True)
        prior_path = os.environ.get("PATH")
        os.environ["PATH"] = str(executable_directory) + (
            ":" + prior_path if prior_path else ""
        )
        try:
            self.assertEqual(
                run_personal_client(
                    profile=profile,
                    state_directory=self.state,
                    upstream_credential=lambda: DIRECT_TOKEN,
                    counters=COUNTERS,
                ),
                0,
            )
            output_requests = [
                (json.loads(body), headers)
                for body, headers in provider.requests
                if any(
                    isinstance(item, dict)
                    and item.get("type") == "function_call_output"
                    for item in json.loads(body).get("input", [])
                )
            ]
            self.assertEqual(len(output_requests), 1)
            payload, headers = output_requests[0]
            compact = next(
                item["output"]
                for item in payload["input"]
                if item.get("type") == "function_call_output"
            )
            self.assertIn("hormuz-path-list-v1", compact)
            normalized = {name.lower(): value for name, value in headers.items()}
            self.assertEqual(normalized["authorization"], "Bearer " + DIRECT_TOKEN)
            self.assertNotIn("x-hormuz-context-format", normalized)
            benefit = PersonalMetricsStore(self.state, profile.key).benefit(enabled=True)
            self.assertGreaterEqual(benefit["traffic"]["transformed_requests"], 1)
        finally:
            if prior_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = prior_path
            provider.shutdown()
            provider_thread.join(timeout=2)
            provider.server_close()


if __name__ == "__main__":
    unittest.main()
