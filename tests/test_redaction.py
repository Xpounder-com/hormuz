from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hormuz.config import ConfigError, GatewayConfig, SecretControls
from hormuz.redaction import MAX_JSON_DEPTH, REPLACEMENT, RedactionError, SecretRedactor


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
