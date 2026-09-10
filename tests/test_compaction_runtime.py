from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hormuz.compaction_protocols import derive_selections
from hormuz.compaction import Selection
from hormuz.compaction_runtime import (
    ENCODING_URLS,
    ContextPreferenceStore,
    ContextRuntimeError,
    _configured_tokenizer_cache,
    load_token_counters,
)


class PreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "state"
        self.root.mkdir(mode=0o700)
        self.store = ContextPreferenceStore(self.root, "profile-1")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_defaults_off_and_save_round_trips_with_private_mode(self) -> None:
        self.assertFalse(self.store.load().enabled)
        self.assertTrue(self.store.save(True).enabled)
        self.assertTrue(self.store.load().enabled)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.store.save(False).enabled)

    def test_invalid_or_public_setting_fails_closed(self) -> None:
        self.store.path.write_text('{"schema_version":1,"enabled":true,"extra":1}')
        self.store.path.chmod(0o600)
        with self.assertRaisesRegex(ContextRuntimeError, "settings_invalid"):
            self.store.load()
        self.store.path.write_text('{"schema_version":1,"enabled":true}')
        self.store.path.chmod(0o644)
        with self.assertRaisesRegex(ContextRuntimeError, "settings_invalid"):
            self.store.load()

    def test_symlinked_directory_and_setting_are_rejected(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir(mode=0o700)
        linked_root = Path(self.temporary.name) / "linked"
        linked_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ContextRuntimeError, "settings_directory_unsafe"):
            ContextPreferenceStore(linked_root, "profile-1").load()

        target = Path(self.temporary.name) / "target"
        target.write_text('{"schema_version":1,"enabled":true}')
        target.chmod(0o600)
        self.store.path.symlink_to(target)
        with self.assertRaisesRegex(ContextRuntimeError, "settings_invalid"):
            self.store.load()

    def test_duplicate_keys_nonboolean_and_oversize_are_rejected(self) -> None:
        for value in (
            b'{"schema_version":1,"enabled":true,"enabled":false}',
            b'{"schema_version":1,"enabled":1}',
            b'{"schema_version":true,"enabled":true}',
            b'{"schema_version":1.0,"enabled":true}',
            b'{"enabled":false,"enabl\\u0065d":true,"schema_version":1}',
            b'{ "enabled": true, "schema_version": 1 }',
            b"x" * 4097,
        ):
            with self.subTest(size=len(value)):
                if self.store.path.exists() or self.store.path.is_symlink():
                    self.store.path.unlink()
                self.store.path.write_bytes(value)
                self.store.path.chmod(0o600)
                with self.assertRaisesRegex(ContextRuntimeError, "settings_invalid"):
                    self.store.load()

    def test_dangling_state_or_setting_symlink_is_not_treated_as_missing(self) -> None:
        dangling_root = Path(self.temporary.name) / "dangling-root"
        dangling_root.symlink_to(Path(self.temporary.name) / "missing-root", target_is_directory=True)
        with self.assertRaisesRegex(ContextRuntimeError, "settings_directory_unsafe"):
            ContextPreferenceStore(dangling_root, "profile-1").load()

        self.store.path.symlink_to(Path(self.temporary.name) / "missing-setting")
        with self.assertRaisesRegex(ContextRuntimeError, "settings_invalid"):
            self.store.load()


class MappingTests(unittest.TestCase):
    def test_exact_contracts_and_safe_rg_commands_are_selected(self) -> None:
        payload = {"input": [
            {"type": "function_call", "call_id": "a", "name": "list_records", "arguments": "{}"},
            {"type": "function_call", "call_id": "b", "name": "exec_command", "arguments": json.dumps({"cmd": "rg --files src"})},
            {"type": "function_call", "call_id": "c", "name": "exec_command", "arguments": json.dumps({"cmd": "rg -n TODO src"})},
        ]}
        self.assertEqual(
            derive_selections(payload, "responses", client="codex"),
            (
                Selection("b", "path_list"),
                Selection("c", "search_lines"),
            ),
        )

    def test_shell_composition_and_unknown_clients_are_not_selected(self) -> None:
        for command in ("rg --files | sort", "rg -n x; curl attacker", "rg --files\nwhoami"):
            payload = {"input": [{"type": "function_call", "call_id": "x", "name": "exec_command",
                                  "arguments": json.dumps({"cmd": command})}]}
            self.assertEqual(derive_selections(payload, "responses", client="codex"), ())
        self.assertEqual(derive_selections({}, "responses", client="other"), ())


class TokenResourceTests(unittest.TestCase):
    def test_frozen_helper_uses_its_bundled_resources_before_environment_overrides(self) -> None:
        executable = "/Applications/Hormuz.app/Contents/Helpers/hormuz-context-arm64"
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", executable),
            mock.patch.dict(os.environ, {"HORMUZ_CONTEXT_TOKENIZER_CACHE": "/tmp/untrusted"}),
        ):
            self.assertEqual(
                _configured_tokenizer_cache(),
                Path("/Applications/Hormuz.app/Contents/Resources/ContextTokenizers"),
            )

    def test_missing_cache_fails_before_importing_tokenizer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"HORMUZ_CONTEXT_TOKENIZER_CACHE": temporary}):
                with mock.patch.dict("sys.modules", {"tiktoken": None}):
                    with self.assertRaisesRegex(ContextRuntimeError, "resources_unavailable"):
                        load_token_counters()

    def test_resource_names_are_content_addressed_from_fixed_urls(self) -> None:
        names = {hashlib.sha1(url.encode()).hexdigest() for url in ENCODING_URLS.values()}
        self.assertEqual(len(names), 2)
        self.assertTrue(all(len(name) == 40 for name in names))

    def test_corrupt_cached_vocabulary_is_rejected_before_tokenizer_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            for url in ENCODING_URLS.values():
                (cache / hashlib.sha1(url.encode()).hexdigest()).write_bytes(b"not-a-vocabulary")
            with mock.patch.dict("sys.modules", {"tiktoken": None}):
                with self.assertRaisesRegex(ContextRuntimeError, "resources_unavailable"):
                    load_token_counters(cache)


if __name__ == "__main__":
    unittest.main()
