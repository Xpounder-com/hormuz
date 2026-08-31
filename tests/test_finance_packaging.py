"""Source-kit assertions; unlike value tests these require checkout tools."""

from pathlib import Path
import unittest
from unittest import mock

from tools import verify_core_wheel as packaging


class FinancePackagingTests(unittest.TestCase):
    def test_complete_finance_history_source_kit_is_required(self):
        members = ["hormuz-1.0.0/" + path for path in packaging.REQUIRED_FINANCE_HISTORY_SDIST_PATHS]
        with mock.patch.object(packaging, "_sdist_members", return_value=members):
            packaging._assert_finance_history_sdist_boundary(Path("test.tar.gz"))
        for missing in members:
            with self.subTest(missing=missing), mock.patch.object(packaging, "_sdist_members", return_value=[item for item in members if item != missing]):
                with self.assertRaisesRegex(RuntimeError, "Finance history incomplete"):
                    packaging._assert_finance_history_sdist_boundary(Path("test.tar.gz"))

    def test_complete_finance_value_source_kit_is_required(self):
        members = ["hormuz-1.0.0/" + path for path in packaging.REQUIRED_FINANCE_VALUES_SDIST_PATHS]
        with mock.patch.object(packaging, "_sdist_members", return_value=members):
            packaging._assert_finance_values_sdist_boundary(Path("test.tar.gz"))
        for missing in members:
            with self.subTest(missing=missing), mock.patch.object(packaging, "_sdist_members", return_value=[item for item in members if item != missing]):
                with self.assertRaisesRegex(RuntimeError, "Finance values incomplete"):
                    packaging._assert_finance_values_sdist_boundary(Path("test.tar.gz"))


if __name__ == "__main__":
    unittest.main()
