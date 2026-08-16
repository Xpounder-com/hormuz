from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from hormuz.config import (
    ConfigError,
    DLPControls,
    DLPRuleConfig,
    GatewayConfig,
    SecretControls,
)
from hormuz.redaction import (
    DLP_REPLACEMENT,
    MAX_ENCODED_TEXT_BYTES,
    MAX_ENCODED_TEXT_DEPTH,
    MAX_JSON_DEPTH,
    REPLACEMENT,
    RedactionError,
    SecretRedactor,
)


ROOT = Path(__file__).resolve().parents[1]


class SecretRedactorTests(unittest.TestCase):
    def test_builtin_and_custom_values_are_replaced_without_changing_keys(self) -> None:
        custom = "INTERNAL-COMPANY-CODE-9472"
        openai_key = "sk-" + "proj-" + ("C" * 24)
        redactor = SecretRedactor(
            SecretControls(
                mode="redact",
                custom_secret_envs=("TEST_COMPANY_SECRET",),
                custom_secret_values=(("custom:TEST_COMPANY_SECRET", custom),),
            )
        )

        result = redactor.inspect(
            {
                "input": [f"first {openai_key}", {"note": f"second {custom} and {custom}"}],
                "model": "gpt-test",
            }
        )

        self.assertEqual(result.count, 3)
        self.assertEqual(result.rules, ("custom:TEST_COMPANY_SECRET", "openai_api_key"))
        self.assertEqual(result.value["model"], "gpt-test")
        self.assertIn(REPLACEMENT, result.value["input"][0])
        self.assertEqual(result.value["input"][1]["note"].count(REPLACEMENT), 2)

    def test_json_key_findings_preserve_keys_and_fail_closed_when_redaction_is_required(self) -> None:
        secret = "sk-" + "proj-" + ("K" * 24)
        encoded_secret = base64.b64encode(
            f"credential={secret}".encode("utf-8")
        ).decode("ascii")
        ssn = "123-45-6789"
        email = "key-owner@example.com"
        protected = "PROJECT-KEYSTONE"
        cases = (
            (
                "builtin secret",
                SecretRedactor(SecretControls(mode="redact")),
                secret,
                "openai_api_key",
                "deny",
            ),
            (
                "encoded builtin secret",
                SecretRedactor(SecretControls(mode="redact")),
                encoded_secret,
                "openai_api_key",
                "deny",
            ),
            (
                "redact DLP",
                SecretRedactor(
                    SecretControls(mode="off"),
                    dlp_controls=DLPControls(
                        rules=(
                            DLPRuleConfig(
                                rule_id="us_ssn",
                                category="regulated_identifier",
                                confidence="high",
                                action="redact",
                            ),
                        ),
                    ),
                ),
                ssn,
                "us_ssn",
                "deny",
            ),
            (
                "detect DLP",
                SecretRedactor(
                    SecretControls(mode="off"),
                    dlp_controls=DLPControls(
                        rules=(
                            DLPRuleConfig(
                                rule_id="email_address",
                                category="personal_data",
                                confidence="low",
                                action="detect",
                            ),
                        ),
                    ),
                ),
                email,
                "email_address",
                "detect",
            ),
            (
                "approval DLP",
                SecretRedactor(
                    SecretControls(mode="off"),
                    dlp_controls=DLPControls(
                        rules=(
                            DLPRuleConfig(
                                rule_id="company.codename",
                                category="company_dictionary",
                                confidence="high",
                                action="require_approval",
                                exact_values=(protected,),
                            ),
                        ),
                    ),
                ),
                protected,
                "company.codename",
                "require_approval",
            ),
        )

        for name, redactor, key, rule_id, action in cases:
            with self.subTest(name=name):
                value = {"metadata": {key: "safe-value"}}
                result = redactor.inspect(value, protocol="openai", model="gpt-test")

                self.assertEqual(result.value, value)
                self.assertIn(key, result.value["metadata"])
                self.assertEqual(result.count, 1)
                self.assertEqual(result.redaction_count, 0)
                self.assertEqual(result.rules, (rule_id,))
                self.assertEqual(result.action, action)
                self.assertEqual(result.findings[0].action, action)
                self.assertNotIn(key, repr(result.findings))

    def test_off_mode_returns_original_value(self) -> None:
        value = {"input": "sk-" + "proj-" + ("D" * 24)}
        result = SecretRedactor(SecretControls(mode="off")).inspect(value)
        self.assertIs(result.value, value)
        self.assertEqual(result.count, 0)

    def test_hormuz_human_session_credentials_are_builtin_secrets(self) -> None:
        access = "hox_a_" + "a" * 43
        refresh = "hox_r_" + "r" * 43
        result = SecretRedactor(SecretControls()).inspect(
            {"input": f"accidentally pasted {access} and {refresh}"}
        )
        self.assertEqual(result.count, 2)
        self.assertEqual(result.rules, ("hormuz_session_credential",))
        self.assertNotIn(access, result.value["input"])
        self.assertNotIn(refresh, result.value["input"])

    def test_excessive_json_depth_is_rejected(self) -> None:
        value: dict = {"input": "safe"}
        for _ in range(MAX_JSON_DEPTH + 1):
            value = {"nested": value}

        with self.assertRaises(RedactionError):
            SecretRedactor(SecretControls()).inspect(value)

    def test_base64_encoded_secret_is_redacted_and_safely_reencoded(self) -> None:
        secret = "sk-" + "proj-" + ("A" * 24)
        encoded = base64.b64encode(f"credential={secret}".encode("utf-8")).decode("ascii")

        result = SecretRedactor(SecretControls(mode="redact")).inspect(
            {
                "input": [
                    {
                        "type": "function_call_output",
                        "output": encoded,
                    }
                ]
            },
            protocol="openai",
            model="gpt-test",
        )

        transformed = result.value["input"][0]["output"]
        decoded = base64.b64decode(transformed).decode("utf-8")
        self.assertEqual(result.action, "redact")
        self.assertEqual(result.count, 1)
        self.assertEqual(result.redaction_count, 1)
        self.assertEqual(result.rules, ("openai_api_key",))
        self.assertNotIn(secret, decoded)
        self.assertEqual(decoded, f"credential={REPLACEMENT}")

    def test_base64_shaped_exact_secret_keeps_direct_match_precedence(self) -> None:
        protected = base64.b64encode(b"test-company-secret").decode("ascii")
        redactor = SecretRedactor(
            SecretControls(
                mode="redact",
                custom_secret_values=(("custom:BASE64_SECRET", protected),),
            )
        )

        result = redactor.inspect({"input": protected})

        self.assertEqual(result.value["input"], REPLACEMENT)
        self.assertEqual(result.count, 1)
        self.assertEqual(result.rules, ("custom:BASE64_SECRET",))

    def test_text_data_uri_dlp_is_redacted_without_changing_its_media_type(self) -> None:
        ssn = "123-45-6789"
        payload = base64.b64encode(f'{{"employee":"{ssn}"}}'.encode("utf-8")).decode("ascii")
        prefix = "data:application/json;charset=utf-8;base64,"
        controls = DLPControls(
            policy_version="encoded-dlp-v1",
            rules=(
                DLPRuleConfig(
                    rule_id="us_ssn",
                    category="regulated_identifier",
                    confidence="high",
                    action="redact",
                ),
            ),
        )

        result = SecretRedactor(
            SecretControls(mode="off"),
            dlp_controls=controls,
        ).inspect({"input": prefix + payload}, protocol="openai", model="gpt-test")

        transformed = result.value["input"]
        self.assertTrue(transformed.startswith(prefix))
        decoded = base64.b64decode(transformed[len(prefix) :]).decode("utf-8")
        self.assertNotIn(ssn, decoded)
        self.assertIn(DLP_REPLACEMENT, decoded)
        self.assertEqual(result.redaction_count, 1)
        self.assertEqual(result.findings[0].rule_id, "us_ssn")

    def test_encoded_dictionary_denial_preserves_payload_and_finding_privacy(self) -> None:
        protected = "PROJECT-ORBITAL"
        encoded = base64.urlsafe_b64encode(
            f"codename={protected}".encode("utf-8")
        ).decode("ascii").rstrip("=")
        controls = DLPControls(
            policy_version="encoded-company-v1",
            rules=(
                DLPRuleConfig(
                    rule_id="company.codename",
                    category="company_dictionary",
                    confidence="high",
                    action="deny",
                    exact_values=(protected,),
                ),
            ),
        )

        result = SecretRedactor(
            SecretControls(mode="off"),
            dlp_controls=controls,
        ).inspect({"input": encoded}, protocol="openai", model="gpt-test")

        self.assertEqual(result.action, "deny")
        self.assertEqual(result.value["input"], encoded)
        self.assertEqual(result.count, 1)
        self.assertEqual(result.redaction_count, 0)
        self.assertNotIn(protected, repr(result.findings))

    def test_nested_encoded_text_is_bounded_and_benign_text_is_unchanged(self) -> None:
        benign = base64.b64encode(b"ordinary encoded tool output").decode("ascii")
        benign_result = SecretRedactor(SecretControls()).inspect({"input": benign})
        self.assertEqual(benign_result.value["input"], benign)
        self.assertEqual(benign_result.count, 0)

        too_deep = "sk-" + "proj-" + ("N" * 24)
        for _ in range(MAX_ENCODED_TEXT_DEPTH + 1):
            too_deep = base64.b64encode(too_deep.encode("utf-8")).decode("ascii")
        with self.assertRaisesRegex(RedactionError, "maximum nesting depth"):
            SecretRedactor(SecretControls()).inspect({"input": too_deep})

        oversized = "A" * ((((MAX_ENCODED_TEXT_BYTES + 2) // 3) * 4) + 4)
        with self.assertRaisesRegex(RedactionError, "maximum decoded size"):
            SecretRedactor(SecretControls()).inspect(
                {"input": "data:text/plain;base64," + oversized}
            )

    def test_high_confidence_regulated_identifiers_are_redacted(self) -> None:
        controls = DLPControls(
            policy_version="regulated-v3",
            rules=(
                DLPRuleConfig(
                    rule_id="us_ssn",
                    category="regulated_identifier",
                    confidence="high",
                    action="redact",
                ),
                DLPRuleConfig(
                    rule_id="payment_card",
                    category="regulated_identifier",
                    confidence="high",
                    action="redact",
                ),
            ),
        )
        redactor = SecretRedactor(SecretControls(mode="off"), dlp_controls=controls)

        result = redactor.inspect(
            {"input": "SSN 123-45-6789 card 4242 4242 4242 4242"},
            protocol="openai",
            model="gpt-test",
        )

        self.assertEqual(result.action, "redact")
        self.assertEqual(result.count, 2)
        self.assertEqual(result.redaction_count, 2)
        self.assertEqual(result.policy_version, "regulated-v3")
        self.assertEqual(result.value["input"].count(DLP_REPLACEMENT), 2)
        self.assertEqual(
            {finding.rule_id for finding in result.findings},
            {"payment_card", "us_ssn"},
        )

    def test_invalid_or_unicode_numeric_candidates_are_not_classified(self) -> None:
        controls = DLPControls(
            rules=(
                DLPRuleConfig(
                    rule_id="us_ssn",
                    category="regulated_identifier",
                    confidence="high",
                    action="redact",
                ),
                DLPRuleConfig(
                    rule_id="payment_card",
                    category="regulated_identifier",
                    confidence="high",
                    action="redact",
                ),
            ),
        )
        value = {
            "input": "invalid 000-12-3456, 4242 4242 4242 4243, and ١٢٣-٤٥-٦٧٨٩"
        }

        result = SecretRedactor(
            SecretControls(mode="off"),
            dlp_controls=controls,
        ).inspect(value, protocol="openai", model="gpt-test")

        self.assertEqual(result.action, "allow")
        self.assertEqual(result.count, 0)
        self.assertEqual(result.value, value)

    def test_low_confidence_email_is_detect_only(self) -> None:
        controls = DLPControls(
            rules=(
                DLPRuleConfig(
                    rule_id="email_address",
                    category="pii",
                    confidence="low",
                    action="detect",
                ),
            ),
        )
        value = {"input": "Contact engineer@example.com before deployment."}

        result = SecretRedactor(
            SecretControls(mode="off"),
            dlp_controls=controls,
        ).inspect(value, protocol="anthropic", model="claude-test")

        self.assertEqual(result.action, "detect")
        self.assertEqual(result.count, 1)
        self.assertEqual(result.redaction_count, 0)
        self.assertEqual(result.value, value)
        self.assertEqual(result.findings[0].to_dict()["confidence"], "low")

    def test_openai_opaque_media_is_denied_only_in_provider_content_positions(self) -> None:
        controls = DLPControls(
            policy_version="opaque-v1",
            rules=(
                DLPRuleConfig(
                    rule_id="opaque_media",
                    category="unsupported_media",
                    confidence="high",
                    action="deny",
                    providers=("openai",),
                ),
            ),
        )
        redactor = SecretRedactor(SecretControls(mode="off"), dlp_controls=controls)

        result = redactor.inspect(
            {
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Inspect these."},
                            {"type": "input_image", "image_url": "https://example.invalid/a.png"},
                            {"type": "input_file", "file_id": "file_opaque"},
                        ],
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_1",
                        "output": [{"type": "input_file", "file_data": "opaque-data"}],
                    },
                    {
                        "type": "computer_call_output",
                        "call_id": "call_2",
                        "output": {
                            "type": "computer_screenshot",
                            "image_url": "data:image/png;base64,opaque",
                        },
                    },
                ],
                "metadata": {
                    "type": "input_image",
                    "image_url": "https://example.invalid/not-provider-content.png",
                },
            },
            protocol="openai",
            model="gpt-test",
        )

        self.assertEqual(result.action, "deny")
        self.assertEqual(result.count, 4)
        self.assertEqual(result.redaction_count, 0)
        self.assertEqual(result.findings[0].rule_id, "opaque_media")
        self.assertEqual(result.findings[0].category, "unsupported_media")

    def test_opaque_media_does_not_bypass_json_depth_limit(self) -> None:
        controls = DLPControls(
            rules=(
                DLPRuleConfig(
                    rule_id="opaque_media",
                    category="unsupported_media",
                    confidence="high",
                    action="deny",
                    providers=("openai",),
                ),
            ),
        )
        nested: dict = {"value": "safe"}
        for _ in range(MAX_JSON_DEPTH + 1):
            nested = {"nested": nested}

        with self.assertRaises(RedactionError):
            SecretRedactor(
                SecretControls(mode="off"),
                dlp_controls=controls,
            ).inspect(
                {
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_image", "file_id": "file_opaque"}],
                        }
                    ],
                    "metadata": nested,
                },
                protocol="openai",
                model="gpt-test",
            )

    def test_anthropic_opaque_media_denies_binary_sources_but_allows_inline_text(self) -> None:
        controls = DLPControls(
            rules=(
                DLPRuleConfig(
                    rule_id="opaque_media",
                    category="unsupported_media",
                    confidence="high",
                    action="deny",
                    providers=("anthropic",),
                ),
            ),
        )
        redactor = SecretRedactor(SecretControls(mode="off"), dlp_controls=controls)

        result = redactor.inspect(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "text",
                                    "media_type": "text/plain",
                                    "data": "inspectable document text",
                                },
                            },
                            {
                                "type": "document",
                                "source": {
                                    "type": "content",
                                    "content": [{"type": "text", "text": "inspectable block"}],
                                },
                            },
                            {
                                "type": "tool_result",
                                "tool_use_id": "tool_1",
                                "content": [
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "image/png",
                                            "data": "opaque-image",
                                        },
                                    }
                                ],
                            },
                            {
                                "type": "document",
                                "source": {"type": "url", "url": "https://example.invalid/a.pdf"},
                            },
                            {
                                "type": "document",
                                "source": {"type": "file", "file_id": "file_opaque"},
                            },
                        ],
                    }
                ]
            },
            protocol="anthropic",
            model="claude-test",
        )

        self.assertEqual(result.action, "deny")
        self.assertEqual(result.count, 3)
        self.assertEqual(result.redaction_count, 0)

    def test_opaque_media_rule_is_provider_scoped_and_can_be_disabled(self) -> None:
        controls = DLPControls(
            rules=(
                DLPRuleConfig(
                    rule_id="opaque_media",
                    category="unsupported_media",
                    confidence="high",
                    action="deny",
                    providers=("anthropic",),
                ),
            ),
        )
        opaque_secret = "sk-" + "proj-" + ("Z" * 24)
        value = {
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "file_data": opaque_secret,
                            "filename": "opaque.bin",
                        }
                    ],
                }
            ]
        }

        result = SecretRedactor(
            SecretControls(mode="redact"),
            dlp_controls=controls,
        ).inspect(value, protocol="openai", model="gpt-test")

        self.assertEqual(result.action, "allow")
        self.assertEqual(result.value, value)
        self.assertIn(opaque_secret, result.value["input"][0]["content"][0]["file_data"])

    def test_disabling_opaque_media_does_not_disable_sibling_dlp(self) -> None:
        controls = DLPControls(
            policy_version="opaque-off-v1",
            rules=(
                DLPRuleConfig(
                    rule_id="opaque_media",
                    category="unsupported_media",
                    confidence="high",
                    action="off",
                    providers=("openai",),
                ),
                DLPRuleConfig(
                    rule_id="us_ssn",
                    category="regulated_identifier",
                    confidence="high",
                    action="redact",
                    providers=("openai",),
                ),
            ),
        )
        ssn = "123-45-6789"
        image_url = "https://example.invalid/allowed-by-policy.png"
        value = {
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": image_url},
                        {"type": "input_text", "text": f"Employee ID {ssn}."},
                    ],
                }
            ]
        }

        result = SecretRedactor(
            SecretControls(mode="redact"),
            dlp_controls=controls,
        ).inspect(value, protocol="openai", model="gpt-test")

        self.assertEqual(result.action, "redact")
        self.assertEqual(result.count, 1)
        self.assertEqual(result.redaction_count, 1)
        self.assertEqual(result.rules, ("us_ssn",))
        self.assertEqual(
            result.value["input"][0]["content"][0]["image_url"],
            image_url,
        )
        self.assertNotIn(ssn, result.value["input"][0]["content"][1]["text"])
        self.assertIn(
            "[REDACTED:HORMUZ_DLP]",
            result.value["input"][0]["content"][1]["text"],
        )

    def test_strongest_scoped_action_wins_without_leaking_dictionary_value(self) -> None:
        protected = "PROJECT-ORBITAL"
        controls = DLPControls(
            policy_version="company-v2",
            rules=(
                DLPRuleConfig(
                    rule_id="company.codename",
                    category="company_dictionary",
                    confidence="high",
                    action="require_approval",
                    providers=("openai",),
                    models=("gpt-approved",),
                    values_env="COMPANY_TERMS",
                    exact_values=(protected,),
                ),
                DLPRuleConfig(
                    rule_id="email_address",
                    category="pii",
                    confidence="low",
                    action="detect",
                ),
            ),
        )
        redactor = SecretRedactor(SecretControls(mode="off"), dlp_controls=controls)

        result = redactor.inspect(
            {"input": f"{protected} owner engineer@example.com"},
            protocol="openai",
            model="gpt-approved",
        )
        out_of_scope = redactor.inspect(
            {"input": protected},
            protocol="anthropic",
            model="claude-test",
        )

        self.assertEqual(result.action, "require_approval")
        self.assertEqual(result.redaction_count, 0)
        self.assertNotIn(protected, repr(result.findings))
        self.assertEqual(out_of_scope.action, "allow")

    def test_configured_custom_secret_must_exist_and_is_hidden_from_repr(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["egress_controls"]["secrets"]["custom_secret_envs"] = ["TEST_COMPANY_SECRET"]
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "TEST_COMPANY_SECRET"):
                GatewayConfig.load(config_path, environ={"HORMUZ_TOKEN": "employee-token-long"})

            secret = "internal-value-never-log"
            config = GatewayConfig.load(
                config_path,
                environ={"HORMUZ_TOKEN": "employee-token-long", "TEST_COMPANY_SECRET": secret},
            )
            self.assertEqual(config.secret_controls.custom_secret_envs, ("TEST_COMPANY_SECRET",))
            self.assertNotIn(secret, repr(config.secret_controls))

    def test_dlp_dictionary_config_is_bounded_hidden_and_model_validated(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["egress_controls"]["dlp"]["dictionaries"] = [
            {
                "rule_id": "company.codename",
                "category": "company_dictionary",
                "confidence": "high",
                "action": "deny",
                "providers": ["openai"],
                "models": ["gpt-5.4"],
                "values_env": "COMPANY_TERMS",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            secret = "PROJECT-ORBITAL"
            environment = {
                "HORMUZ_TOKEN": "employee-token-long",
                "COMPANY_TERMS": json.dumps([secret]),
            }

            config = GatewayConfig.load(config_path, environ=environment)

            self.assertEqual(config.dlp_controls.policy_version, "organization-dlp-v1")
            dictionary = next(
                rule for rule in config.dlp_controls.rules if rule.rule_id == "company.codename"
            )
            self.assertEqual(dictionary.models, ("gpt-5.4",))
            self.assertNotIn(secret, repr(config.dlp_controls))

            raw["egress_controls"]["dlp"]["dictionaries"][0]["models"] = ["unknown-model"]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "must match a routed upstream model"):
                GatewayConfig.load(config_path, environ=environment)

    def test_dlp_overlays_resolve_team_then_actor_without_duplicate_rules(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["egress_controls"]["dlp"]["overlays"] = {
            "teams": {
                "engineering": {
                    "policy_version": "engineering-dlp-v2",
                    "rules": {"email_address": {"action": "redact"}},
                }
            },
            "actors": {
                "alice": {
                    "policy_version": "alice-dlp-v1",
                    "rules": {
                        "email_address": {
                            "action": "deny",
                            "providers": ["openai"],
                            "models": ["gpt-5.4"],
                        }
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(
                config_path,
                environ={"HORMUZ_TOKEN": "employee-token-long"},
            )

        identity = config.identities_by_actor["alice"]
        openai = config.resolved_dlp_controls(
            identity,
            protocol="openai",
            model="gpt-5.4",
        )
        openai_other_model = config.resolved_dlp_controls(
            identity,
            protocol="openai",
            model="gpt-5.4-mini",
        )
        anthropic = config.resolved_dlp_controls(
            identity,
            protocol="anthropic",
            model="claude-sonnet-5",
        )

        def action(controls: DLPControls, rule_id: str) -> str:
            matches = [rule.action for rule in controls.rules if rule.rule_id == rule_id]
            self.assertEqual(len(matches), 1)
            return matches[0]

        self.assertEqual(action(openai, "email_address"), "deny")
        self.assertEqual(action(openai_other_model, "email_address"), "redact")
        self.assertEqual(action(anthropic, "email_address"), "redact")
        self.assertEqual(openai.policy_version, openai_other_model.policy_version)
        self.assertEqual(openai.policy_version, anthropic.policy_version)
        self.assertRegex(openai.policy_version, r"\Adlp-effective-v1:[0-9a-f]{32}\Z")
        self.assertEqual(
            next(
                rule.action
                for rule in config.dlp_controls.rules
                if rule.rule_id == "email_address"
            ),
            "detect",
        )

    def test_dlp_actor_overlay_cannot_weaken_a_stronger_team_overlay(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["egress_controls"]["dlp"]["overlays"] = {
            "teams": {
                "engineering": {
                    "policy_version": "engineering-dlp-v3",
                    "rules": {"email_address": {"action": "deny"}},
                }
            },
            "actors": {
                "alice": {
                    "policy_version": "alice-dlp-v2",
                    "rules": {"email_address": {"action": "redact"}},
                }
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(
                config_path,
                environ={"HORMUZ_TOKEN": "employee-token-long"},
            )

        effective = config.resolved_dlp_controls(
            config.identities_by_actor["alice"],
            protocol="openai",
            model="gpt-5.4",
        )
        self.assertEqual(
            next(rule.action for rule in effective.rules if rule.rule_id == "email_address"),
            "deny",
        )

    def test_dlp_overlay_approval_requires_an_organization_approver(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["egress_controls"]["dlp"]["approval"] = {
            "enabled": True,
            "fingerprint_key_env": "DLP_FINGERPRINT_KEY",
        }
        raw["egress_controls"]["dlp"]["overlays"] = {
            "teams": {
                "engineering": {
                    "policy_version": "engineering-approval-v1",
                    "rules": {"email_address": {"action": "require_approval"}},
                }
            },
            "actors": {},
        }
        environment = {
            "HORMUZ_TOKEN": "employee-token-long",
            "DLP_FINGERPRINT_KEY": base64.urlsafe_b64encode(b"k" * 32).decode("ascii"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "no dlp_approver"):
                GatewayConfig.load(config_path, environ=environment)

            raw["identities"][0]["capabilities"] = ["dlp_approver"]
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(config_path, environ=environment)

        effective = config.resolved_dlp_controls(
            config.identities_by_actor["alice"],
            protocol="openai",
            model="gpt-5.4",
        )
        self.assertEqual(
            next(rule.action for rule in effective.rules if rule.rule_id == "email_address"),
            "require_approval",
        )

    def test_dlp_overlay_inherits_dictionary_without_exposing_values(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["egress_controls"]["dlp"]["dictionaries"] = [
            {
                "rule_id": "company.codename",
                "action": "detect",
                "providers": ["openai"],
                "values_env": "COMPANY_TERMS",
            }
        ]
        raw["egress_controls"]["dlp"]["overlays"] = {
            "teams": {
                "engineering": {
                    "policy_version": "engineering-codenames-v1",
                    "rules": {"company.codename": {"action": "deny"}},
                }
            },
            "actors": {},
        }
        protected = "PROJECT-ORBITAL"
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            config = GatewayConfig.load(
                config_path,
                environ={
                    "HORMUZ_TOKEN": "employee-token-long",
                    "COMPANY_TERMS": json.dumps([protected]),
                },
            )

        effective = config.resolved_dlp_controls(
            config.identities_by_actor["alice"],
            protocol="openai",
            model="gpt-5.4",
        )
        rule = next(rule for rule in effective.rules if rule.rule_id == "company.codename")
        self.assertEqual(rule.action, "deny")
        self.assertEqual(rule.exact_values, (protected,))
        self.assertNotIn(protected, repr(config))
        self.assertNotIn(protected, effective.policy_version)

    def test_dlp_overlays_reject_weaker_unknown_or_expanded_scope(self) -> None:
        baseline = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))

        cases = (
            (
                {"email_address": {"action": "detect"}},
                "must be stricter than organization action detect",
            ),
            (
                {"unknown.rule": {"action": "deny"}},
                "must reference an enabled organization DLP rule",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            for rules, message in cases:
                raw = json.loads(json.dumps(baseline))
                raw["egress_controls"]["dlp"]["overlays"] = {
                    "teams": {
                        "engineering": {
                            "policy_version": "invalid-v1",
                            "rules": rules,
                        }
                    },
                    "actors": {},
                }
                config_path.write_text(json.dumps(raw), encoding="utf-8")
                with self.subTest(message=message), self.assertRaisesRegex(ConfigError, message):
                    GatewayConfig.load(
                        config_path,
                        environ={"HORMUZ_TOKEN": "employee-token-long"},
                    )

            raw = json.loads(json.dumps(baseline))
            raw["egress_controls"]["dlp"]["rules"]["email_address"]["providers"] = [
                "openai"
            ]
            raw["egress_controls"]["dlp"]["overlays"] = {
                "teams": {
                    "engineering": {
                        "policy_version": "invalid-provider-v1",
                        "rules": {
                            "email_address": {
                                "action": "redact",
                                "providers": ["anthropic"],
                            }
                        },
                    }
                },
                "actors": {},
            }
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "non-empty subset of organization providers"):
                GatewayConfig.load(
                    config_path,
                    environ={"HORMUZ_TOKEN": "employee-token-long"},
                )

            raw = json.loads(json.dumps(baseline))
            raw["egress_controls"]["dlp"]["overlays"] = {
                "teams": {
                    "engineering": {
                        "policy_version": "invalid-model-v1",
                        "rules": {
                            "email_address": {
                                "action": "redact",
                                "models": [],
                            }
                        },
                    }
                },
                "actors": {},
            }
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "non-empty model scope"):
                GatewayConfig.load(
                    config_path,
                    environ={"HORMUZ_TOKEN": "employee-token-long"},
                )

            raw = json.loads(json.dumps(baseline))
            raw["egress_controls"]["dlp"]["overlays"] = {
                "teams": {
                    "unknown-team": {
                        "policy_version": "unknown-team-v1",
                        "rules": {"email_address": {"action": "redact"}},
                    }
                },
                "actors": {},
            }
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "unknown teams"):
                GatewayConfig.load(
                    config_path,
                    environ={"HORMUZ_TOKEN": "employee-token-long"},
                )

            raw = json.loads(json.dumps(baseline))
            raw["identities"].append(
                {
                    "token_env": "SECOND_HORMUZ_TOKEN",
                    "actor_id": "bob",
                    "actor_name": "Bob Example",
                    "team_id": "engineering",
                    "team_name": "Engineering",
                    "organization_id": "another-organization",
                    "allowed_clients": ["codex"],
                }
            )
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "exactly one organization"):
                GatewayConfig.load(
                    config_path,
                    environ={
                        "HORMUZ_TOKEN": "employee-token-long",
                        "SECOND_HORMUZ_TOKEN": "second-employee-token",
                    },
                )

    def test_dlp_dictionary_rejects_non_json_or_unsafe_configuration(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["egress_controls"]["dlp"]["dictionaries"] = [
            {
                "rule_id": "company.codename",
                "values_env": "COMPANY_TERMS",
            }
        ]
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "JSON string array"):
                GatewayConfig.load(
                    config_path,
                    environ={
                        "HORMUZ_TOKEN": "employee-token-long",
                        "COMPANY_TERMS": "not-json",
                    },
                )

            raw["egress_controls"]["dlp"]["dictionaries"][0]["rule_id"] = "Unsafe Rule"
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "lowercase safe identifier"):
                GatewayConfig.load(
                    config_path,
                    environ={
                        "HORMUZ_TOKEN": "employee-token-long",
                        "COMPANY_TERMS": json.dumps(["safe-value"]),
                    },
                )

    def test_opaque_media_config_only_allows_off_or_deny(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["egress_controls"]["dlp"]["rules"]["opaque_media"]["action"] = "redact"
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "must be off or deny"):
                GatewayConfig.load(
                    config_path,
                    environ={"HORMUZ_TOKEN": "employee-token-long"},
                )

    def test_budget_configuration_requires_an_effective_output_bound(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        raw["policies"]["organization"].pop("max_output_tokens")
        raw["policies"]["teams"]["engineering"].pop("max_output_tokens")
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "hormuz.json"
            config_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "max_output_tokens"):
                GatewayConfig.load(
                    config_path,
                    environ={"HORMUZ_TOKEN": "employee-token-long"},
                )


if __name__ == "__main__":
    unittest.main()
