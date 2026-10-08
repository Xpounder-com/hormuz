"""Exact provider child custody checks without a Docker daemon or live secrets."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hormuz._hosted_provider import PROVIDER_CHILD_ENV_NAMES
from tools import verify_render_gateway as verifier


class RenderProviderBoundaryTests(unittest.TestCase):
    def boundary(self, **changes):
        return {
            "backend_present": True,
            "backend_names": list(PROVIDER_CHILD_ENV_NAMES),
            "backend_work_billing_populated": [],
            **changes,
        }

    def test_exact_reviewed_child_environment_matches_including_empty_billing(self):
        self.assertTrue(verifier.provider_backend_boundary_matches(self.boundary()))
        self.assertTrue(verifier.provider_backend_boundary_matches(
            self.boundary(backend_names=[*PROVIDER_CHILD_ENV_NAMES, "LC_CTYPE"])))

    def test_extra_missing_or_populated_inactive_secrets_fail_closed(self):
        cases = (
            self.boundary(backend_names=[*PROVIDER_CHILD_ENV_NAMES, "UNRELATED_SECRET"]),
            self.boundary(backend_names=list(PROVIDER_CHILD_ENV_NAMES)[1:]),
            self.boundary(backend_present=False),
            self.boundary(backend_work_billing_populated=["HORMUZ_WORK_BILLING_API_KEY"]),
        )
        for boundary in cases:
            with self.subTest(boundary=boundary):
                self.assertFalse(verifier.provider_backend_boundary_matches(boundary))
        missing = self.boundary()
        missing.pop("backend_work_billing_populated")
        self.assertFalse(verifier.provider_backend_boundary_matches(missing))

    def test_process_inspection_reports_only_names_and_inactive_billing_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            child = proc / "123"
            child.mkdir()
            (child / "cmdline").write_bytes(
                b"/opt/hormuz/bin/python\0-I\0-m\0hormuz.hosted\0provider-backend\0")
            values = {name: "" for name in PROVIDER_CHILD_ENV_NAMES}
            values["HORMUZ_SESSION_MASTER_KEY"] = "private_value_never_exported"
            for populated in (False, True):
                values["HORMUZ_WORK_BILLING_API_KEY"] = (
                    "private_billing_value_never_exported" if populated else "")
                (child / "environ").write_bytes(b"\0".join(
                    (name + "=" + value).encode() for name, value in values.items()) + b"\0")
                output = io.StringIO()
                with patch("pathlib.Path", return_value=proc), redirect_stdout(output):
                    exec(verifier.PROCESS_BOUNDARY, {})
                text = output.getvalue()
                self.assertNotIn("private_value_never_exported", text)
                self.assertNotIn("private_billing_value_never_exported", text)
                boundary = json.loads(text)
                self.assertEqual(verifier.provider_backend_boundary_matches(boundary), not populated)
                self.assertEqual(boundary["backend_work_billing_populated"],
                    ["HORMUZ_WORK_BILLING_API_KEY"] if populated else [])


if __name__ == "__main__":
    unittest.main()
