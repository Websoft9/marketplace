from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import offer_resolve
from cli.store_models import Product


class IdentityCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "products.json"

    def load(self, rows, cases=None):
        self.path.write_text(json.dumps(rows))
        return offer_resolve.load_identities(str(self.path), cases)

    def test_ffmpeg_null_trademark_does_not_break_symfony_and_is_not_silently_dropped(self):
        identities = self.load([
            {"key": "symfony", "trademark": "Symfony"}, {"key": "ffmpeg", "trademark": None},
        ])
        self.assertIn("ffmpeg", identities)
        self.assertEqual(identities["ffmpeg"], [])
        self.assertEqual(identities.warnings[0]["appId"], "ffmpeg")
        self.assertEqual(identities.warnings[0]["fallback"], "app-id")
        self.assertEqual(offer_resolve.identify("Symfony on AWS", "", identities), {"symfony"})
        decision = offer_resolve.identify_details("FFmpeg for Symfony", "", identities)
        self.assertEqual(decision["appId"], "ffmpeg")
        self.assertEqual(decision["method"], "title-app-id")
        resolved = offer_resolve.resolve({"key": "symfony", "trademark": "Symfony", "multiOffers": None}, [
            Product("symfony-code", "prod-symfony", "Symfony on AWS"),
            Product("ffmpeg-code", "prod-ffmpeg", "FFmpeg for Symfony"),
        ], identities=identities)
        self.assertEqual([p["code"] for p in resolved["targets"]], ["symfony-code"])

    def test_missing_blank_and_decoration_only_names_use_safe_ids_with_warnings(self):
        for row in ({"key": "ffmpeg"}, {"key": "ffmpeg", "trademark": None},
                    {"key": "ffmpeg", "trademark": ""}, {"key": "ffmpeg", "trademark": "  "},
                    {"key": "ffmpeg", "trademark": "™®"}):
            with self.subTest(row=row):
                identities = self.load([row])
                self.assertEqual(dict(identities), {"ffmpeg": []})
                self.assertEqual(len(identities.warnings), 1)
                self.assertEqual(offer_resolve.identify("FFmpeg platform", "", identities), {"ffmpeg"})
                # Dict-compatible serialization keeps ID-only entries in the effective identity hash.
                self.assertEqual(json.loads(json.dumps(identities)), {"ffmpeg": []})

    def test_reviewed_name_can_rescue_missing_trademark_even_with_an_unsafe_id(self):
        identities = self.load([{"key": "php", "trademark": None}], {"php": {"titleTrademark": "PHP"}})
        self.assertEqual(identities["php"], ["PHP"])
        self.assertEqual(identities.warnings[0]["fallback"], "title-trademark")
        self.assertEqual(offer_resolve.identify("PHP runtime", "", identities), {"php"})

    def test_unsafe_ids_without_names_still_block_loading_instead_of_being_ignored(self):
        for app in ("php", "go", "ai", "oracle"):
            with self.subTest(app=app):
                with self.assertRaisesRegex(offer_resolve.IdentityCatalogError, "unsafe for automatic fallback"):
                    self.load([{"key": "symfony", "trademark": "Symfony"}, {"key": app, "trademark": None}])

    def test_malformed_records_duplicate_ids_and_invalid_field_types_remain_fatal(self):
        for rows in ({}, [], [None], [{"trademark": "Symfony"}],
                     [{"key": " Symfony ", "trademark": "Symfony"}],
                     [{"key": "symfony", "trademark": "Symfony"}, {"key": "symfony", "trademark": None}],
                     [{"key": "ffmpeg", "trademark": 123}], [{"key": "ffmpeg", "trademark": {}}]):
            with self.subTest(rows=rows), self.assertRaises(offer_resolve.IdentityCatalogError):
                self.load(rows)

    def test_absent_or_unusable_target_cannot_be_bypassed_by_explicit_code(self):
        for code in (None, "target-code"):
            with self.assertRaisesRegex(ValueError, "absent from"):
                offer_resolve.resolve({"key": "missing", "trademark": "Missing", "multiOffers": None},
                                      [Product("target-code", "prod", "Symfony")],
                                      identities={"symfony": ["Symfony"]}, code=code)
            with self.assertRaisesRegex(ValueError, "no usable identity"):
                offer_resolve.resolve({"key": "php", "trademark": "PHP", "multiOffers": None},
                                      [Product("target-code", "prod", "PHP")], identities={"php": []}, code=code)

    def test_real_identity_ties_remain_ambiguous_after_record_degradation(self):
        identities = self.load([
            {"key": "app-a", "trademark": "Shared brand"}, {"key": "app-b", "trademark": "Shared brand"},
            {"key": "ffmpeg", "trademark": None},
        ])
        self.assertEqual(offer_resolve.identify_details("Shared brand", "", identities)["status"], "ambiguous")
        with self.assertRaisesRegex(ValueError, "ambiguous application"):
            offer_resolve.resolve({"key": "app-a", "trademark": "Shared brand", "multiOffers": None},
                                  [Product("code", "prod", "Shared brand")], identities=identities)

    def test_unavailable_catalog_errors_do_not_expose_signed_source_urls(self):
        with mock.patch.object(offer_resolve, "load_json", side_effect=OSError("SECRET signed request")):
            with self.assertRaises(offer_resolve.IdentityCatalogError) as raised:
                offer_resolve.load_identities("https://example.com/catalog?token=SECRET")
        self.assertNotIn("SECRET", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
