from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci/scripts"))

from marketplace_ops_codes import analyze  # noqa: E402

PLATFORMS = {
    "aws": {"urlTemplate": "https://aws.test/{code}"},
    "aliyun": {"urlTemplate": "https://aliyun.test/{code}"},
    "googlecloud": {},
}
AWS = "a" * 24
MATRIX = [
    {
        "id": "odoo",
        "applications": ["odoo"],
        "productID": {"aws": [AWS, "b" * 24], "aliyun": ["cmjj0001"]},
        "variants": {"aws": {AWS: "Multisite"}},
    },
    {
        "id": "wordpress",
        "applications": ["wordpress", "wp"],
        "productID": {"aws": ["c" * 24]},
    },
    {"id": "websoft9", "applications": [], "productID": {"aws": ["d" * 24]}},
]


class AnalyzeTests(unittest.TestCase):
    def test_resolves_alias_and_builds_urls(self) -> None:
        report = analyze(MATRIX, PLATFORMS, ["wp"], ["aws"])
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0]["app"], "wp")
        self.assertEqual(report["rows"][0]["url"], "https://aws.test/" + "c" * 24)

    def test_reports_variant_and_multi_code(self) -> None:
        report = analyze(MATRIX, PLATFORMS, ["odoo"], ["aws"])
        self.assertEqual(report["rows"][0]["variant"], "Multisite")
        self.assertEqual(
            report["multi_code"],
            [{"platform": "aws", "app": "odoo", "codes": [AWS, "b" * 24]}],
        )

    def test_missing_platform_is_not_an_error(self) -> None:
        report = analyze(MATRIX, PLATFORMS, ["wordpress"], ["aliyun"])
        self.assertEqual(report["missing"], [{"platform": "aliyun", "app": "wordpress"}])
        self.assertEqual(report["rows"], [])

    def test_conflict_in_scope(self) -> None:
        matrix = [
            {"id": "a", "applications": ["a"], "productID": {"aws": [AWS]}},
            {"id": "b", "applications": ["b"], "productID": {"aws": [AWS]}},
        ]
        report = analyze(matrix, PLATFORMS, ["a"], ["aws"])
        self.assertEqual(report["conflicts_in_scope"], 1)
        self.assertEqual(report["conflicts"][0]["owners"], ["a", "b"])

    def test_conflict_outside_scope_is_counted_only(self) -> None:
        matrix = [
            {"id": "a", "applications": ["a"], "productID": {"aws": [AWS]}},
            {"id": "b", "applications": ["b"], "productID": {"aws": [AWS]}},
            {"id": "c", "applications": ["c"], "productID": {"aws": ["e" * 24]}},
        ]
        report = analyze(matrix, PLATFORMS, ["c"], ["aws"])
        self.assertEqual(report["conflicts_in_scope"], 0)
        self.assertEqual(report["conflicts_outside_scope"], 1)

    def test_same_code_on_two_platforms_is_not_a_conflict(self) -> None:
        matrix = [{"id": "a", "applications": ["a"], "productID": {"aws": ["cmjj1"], "aliyun": ["cmjj1"]}}]
        report = analyze(matrix, PLATFORMS, ["a"], ["aws", "aliyun"])
        self.assertEqual(report["conflicts"], [])

    def test_format_drift_is_reported(self) -> None:
        matrix = [{"id": "a", "applications": ["a"], "productID": {"aws": ["NOT-A-SKU"]}}]
        report = analyze(matrix, PLATFORMS, ["a"], ["aws"])
        self.assertEqual(len(report["format_drift"]), 1)

    def test_platform_without_url_template_keeps_null_url(self) -> None:
        matrix = [{"id": "a", "applications": ["a"], "productID": {"googlecloud": ["x"]}}]
        report = analyze(matrix, PLATFORMS, ["a"], ["googlecloud"])
        self.assertIsNone(report["rows"][0]["url"])

    def test_unknown_app_is_ignored_locally(self) -> None:
        report = analyze(MATRIX, PLATFORMS, ["missing"], ["aws"])
        self.assertEqual(report["rows"], [])


if __name__ == "__main__":
    unittest.main()
