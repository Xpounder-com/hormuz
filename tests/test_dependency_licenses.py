from __future__ import annotations

import copy
import unittest

from tools import verify_dependency_licenses as licenses


LICENSE_METADATA = {
    "boto3": (None, "Apache-2.0"),
    "botocore": (None, "Apache-2.0"),
    "cffi": ("MIT-0", None),
    "cryptography": ("Apache-2.0 OR BSD-3-Clause", None),
    "hormuz": ("Apache-2.0", None),
    "hormuz-context-experiment": ("Apache-2.0", None),
    "jmespath": (None, "MIT"),
    "psycopg": ("LGPL-3.0-only", None),
    "psycopg-binary": ("LGPL-3.0-only", None),
    "psycopg-pool": ("LGPL-3.0-only", None),
    "pycparser": ("BSD-3-Clause", None),
    "pyjwt": ("MIT", None),
    "python-dateutil": (None, "Dual License"),
    "s3transfer": (None, "Apache License 2.0"),
    "six": (None, "MIT"),
    "typing_extensions": ("PSF-2.0", None),
    "urllib3": ("MIT", None),
}


class DependencyLicenseTests(unittest.TestCase):
    def _records(self) -> list[dict[str, object]]:
        records = []
        for name, (expression, legacy) in LICENSE_METADATA.items():
            classifiers = []
            if name == "python-dateutil":
                classifiers = sorted(licenses.DATEUTIL_CLASSIFIERS)
            records.append(
                {
                    "classifiers": classifiers,
                    "legacy_license": legacy,
                    "license_expression": expression,
                    "license_files": 1,
                    "name": name,
                    "version": "1.0.0",
                }
            )
        records.extend(
            [
                {"name": "pip", "version": "1", "license_files": 0},
                {"name": "setuptools", "version": "1", "license_files": 0},
            ]
        )
        return records

    def test_all_declared_extras_have_reviewed_license_identities(self) -> None:
        summary = licenses.validate_inventory(self._records())

        self.assertEqual(summary["schema_id"], "hormuz.dependency-license-inventory")
        self.assertEqual(summary["application_license"], "Apache-2.0")
        self.assertEqual(summary["counts"], {
            "application": 1,
            "experimental_package": 1,
            "permissive": 12,
            "weak_copyleft_dependency": 3,
        })
        self.assertEqual(summary["verdict"], "pass")
        self.assertFalse(summary["policy"]["legal_opinion_claimed"])

    def test_unknown_distribution_or_license_fails_closed(self) -> None:
        unexpected = self._records()
        unexpected.append(
            {
                "name": "surprise",
                "version": "1",
                "license_expression": "MIT",
                "license_files": 1,
            }
        )
        with self.assertRaisesRegex(licenses.DependencyLicenseError, "distribution_set_mismatch"):
            licenses.validate_inventory(unexpected)

        changed = self._records()
        next(item for item in changed if item["name"] == "cryptography")[
            "license_expression"
        ] = "GPL-3.0-only"
        with self.assertRaisesRegex(licenses.DependencyLicenseError, "license_mismatch"):
            licenses.validate_inventory(changed)

    def test_missing_license_material_or_unverified_dual_license_fails_closed(self) -> None:
        missing_file = copy.deepcopy(self._records())
        next(item for item in missing_file if item["name"] == "pyjwt")["license_files"] = 0
        with self.assertRaisesRegex(licenses.DependencyLicenseError, "license_file_missing"):
            licenses.validate_inventory(missing_file)

        ambiguous = copy.deepcopy(self._records())
        next(item for item in ambiguous if item["name"] == "python-dateutil")[
            "classifiers"
        ] = []
        with self.assertRaisesRegex(licenses.DependencyLicenseError, "dual_license_unverified"):
            licenses.validate_inventory(ambiguous)


if __name__ == "__main__":
    unittest.main()
