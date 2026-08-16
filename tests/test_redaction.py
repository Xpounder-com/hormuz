from __future__ import annotations

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
