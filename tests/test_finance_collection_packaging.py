"""The #8 provider-collection preflight must ship as a complete source kit."""

from pathlib import Path
import unittest
from unittest import mock

from tools import verify_core_wheel as packaging


class FinanceCollectionPackagingTests(unittest.TestCase):
    def test_complete_finance_collection_source_kit_is_required(self):
        paths = packaging.REQUIRED_FINANCE_COLLECTION_PREFLIGHT_SDIST_PATHS
        self.assertGreaterEqual(len(paths), 15)
        members = ["hormuz-1.0.0/" + name for name in paths]
        with mock.patch.object(packaging, "_sdist_members", return_value=members):
            packaging._assert_finance_collection_preflight_sdist_boundary(
                Path("test.tar.gz")
            )
        for missing in members:
            with self.subTest(missing=missing), mock.patch.object(
                packaging,
                "_sdist_members",
                return_value=[name for name in members if name != missing],
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Finance collection source kit incomplete",
                ):
                    packaging._assert_finance_collection_preflight_sdist_boundary(
                        Path("test.tar.gz")
                    )

    def test_complete_finance_collection_runtime_kit_is_required(self):
        paths = packaging.REQUIRED_FINANCE_COLLECTION_RUNTIME_SDIST_PATHS
        self.assertGreaterEqual(len(paths), 10)
        members = ["hormuz-1.1.0/" + name for name in paths]
        with mock.patch.object(packaging, "_sdist_members", return_value=members):
            packaging._assert_finance_collection_runtime_sdist_boundary(
                Path("test.tar.gz")
            )
        for missing in members:
            with self.subTest(missing=missing), mock.patch.object(
                packaging,
                "_sdist_members",
                return_value=[name for name in members if name != missing],
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Finance collection runtime incomplete",
                ):
                    packaging._assert_finance_collection_runtime_sdist_boundary(
                        Path("test.tar.gz")
                    )


if __name__ == "__main__":
    unittest.main()
