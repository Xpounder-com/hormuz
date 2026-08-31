"""The new design contracts must be complete in the source distribution."""

from pathlib import Path
import unittest
from unittest import mock

from tools import verify_core_wheel as packaging


class PortfolioExtensionPackagingTests(unittest.TestCase):
    def test_complete_extension_source_kit_is_required(self):
        paths = packaging.REQUIRED_PORTFOLIO_EXTENSION_SDIST_PATHS
        self.assertGreaterEqual(len(paths), 8)
        members = ["hormuz-1.0.0/" + name for name in paths]
        with mock.patch.object(packaging, "_sdist_members", return_value=members):
            packaging._assert_portfolio_extension_sdist_boundary(Path("test.tar.gz"))
        for missing in members:
            with self.subTest(missing=missing), mock.patch.object(
                    packaging, "_sdist_members", return_value=[name for name in members if name != missing]):
                with self.assertRaisesRegex(RuntimeError, "Portfolio extensions incomplete"):
                    packaging._assert_portfolio_extension_sdist_boundary(Path("test.tar.gz"))


if __name__ == "__main__":
    unittest.main()
