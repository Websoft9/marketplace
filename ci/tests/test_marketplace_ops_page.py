from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ci/scripts"))

from marketplace_ops_page import extract  # noqa: E402


class ExtractTests(unittest.TestCase):
    def test_extracts_title_and_description(self) -> None:
        html = """
        <html><head>
        <title data-rh="true">Odoo on AWS | AWS Marketplace</title>
        <meta name="description" content="Choose from Odoo 20.0 and 19.0.">
        <meta property="og:image" content="https://img.test/a.png">
        </head><body><img src="x.png"></body></html>
        """
        result = extract(html)
        self.assertEqual(result["title"], "Odoo on AWS | AWS Marketplace")
        self.assertEqual(result["description"], "Choose from Odoo 20.0 and 19.0.")
        self.assertEqual(result["og_image"], "https://img.test/a.png")
        self.assertEqual(result["image_count"], 1)

    def test_extracts_chinese_title_and_version_samples(self) -> None:
        html = '<title>WordPress企业多站点站群建站平台-云市场-阿里云</title>' \
               '<script>{"imageVersion":"V7-Ubuntu26.04"}</script>'
        result = extract(html)
        self.assertIn("WordPress", result["title"])
        self.assertTrue(any("imageVersion" in sample for sample in result["version_samples"]))

    def test_missing_fields_are_empty(self) -> None:
        result = extract("<html><body>shell</body></html>")
        self.assertEqual(result["title"], "")
        self.assertEqual(result["description"], "")
        self.assertEqual(result["image_count"], 0)
        self.assertEqual(result["version_samples"], [])
        self.assertFalse(result["blocked_hint"])

    def test_detects_blocked_hint(self) -> None:
        self.assertTrue(extract("<p>请输入验证码</p>")["blocked_hint"])
        self.assertTrue(extract("<p>Access Denied</p>")["blocked_hint"])

    def test_dedupes_version_samples_and_caps_at_five(self) -> None:
        html = "".join(f'{{"Version":"{index}.0"}}' for index in range(10))
        samples = extract(html)["version_samples"]
        self.assertEqual(len(samples), 5)
        self.assertEqual(len(set(samples)), 5)


if __name__ == "__main__":
    unittest.main()
