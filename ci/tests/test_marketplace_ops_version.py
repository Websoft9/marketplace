from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci/scripts"))

from marketplace_ops_version import compare, parse_version  # noqa: E402


class ParseVersionTests(unittest.TestCase):
    def test_strips_v_prefix_and_trailing_zeros(self) -> None:
        self.assertEqual(parse_version("v20.0"), ((20,), False))
        self.assertEqual(parse_version("20.0.0"), ((20,), False))
        self.assertEqual(parse_version("7.1"), ((7, 1), False))

    def test_marks_prerelease(self) -> None:
        self.assertEqual(parse_version("20.0-rc1"), ((20,), True))
        self.assertEqual(parse_version("20.0-beta"), ((20,), True))

    def test_rejects_non_numeric_scheme(self) -> None:
        self.assertIsNone(parse_version("stable-11248"))
        self.assertIsNone(parse_version("latest"))


class CompareTests(unittest.TestCase):
    def test_multi_version_list_matches_baseline(self) -> None:
        result = compare(["20", "19", "18", "17"], ["20.0", "19.0", "18.0", "17.0", "latest"])
        self.assertEqual(result["status"], "current")

    def test_older_marketplace_version_is_outdated(self) -> None:
        result = compare(["19.0"], ["20.0", "latest"])
        self.assertEqual(result["status"], "outdated")

    def test_newer_marketplace_version_is_not_outdated(self) -> None:
        self.assertEqual(compare(["21"], ["20.0", "latest"])["status"], "current")

    def test_prerelease_is_not_newer_than_stable(self) -> None:
        self.assertEqual(compare(["20.0-beta"], ["20.0"])["status"], "outdated")

    def test_missing_marketplace_version_is_unverified(self) -> None:
        self.assertEqual(compare([], ["20.0"])["status"], "unverified")

    def test_unparseable_marketplace_version_is_unverified(self) -> None:
        self.assertEqual(compare(["latest"], ["20.0"])["status"], "unverified")

    def test_latest_only_baseline_is_unsupported(self) -> None:
        self.assertEqual(compare(["7.1"], ["latest"])["status"], "unsupported")

    def test_unparseable_baseline_is_unsupported(self) -> None:
        self.assertEqual(compare(["11248"], ["stable-11248"])["status"], "unsupported")

    def test_v_prefix_baseline_compares_equal(self) -> None:
        self.assertEqual(compare(["v2026.4"], ["v2026.4"])["status"], "current")

    def test_reports_both_newest_versions(self) -> None:
        result = compare(["19.0"], ["20.0", "18.0"])
        self.assertEqual(result["marketplace_newest"], "19")
        self.assertEqual(result["baseline_newest"], "20")


if __name__ == "__main__":
    unittest.main()
