from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from cli import asset_storage, aws_marketplace, offer, offer_assets
from cli.cli import cli


def item_with(screenshots, logo=True):
    item = {"id": "odoo", "trademark": "Odoo", "entityId": "prod-example12345"}
    if logo:
        item["logo"] = {
            "imageurl": "https://websoft9-assets.s3.amazonaws.com/odoo/logo.png"
        }
    if screenshots is not None:
        item["screenshots"] = screenshots
    return item


def image(name, host="websoft9-assets.s3.amazonaws.com"):
    return {"key": name, "value": f"https://{host}/odoo/{name}.png"}


class OfferAssetsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.legacy_template_path = Path(directory.name) / "assets-v1.json"
        self.legacy_template = {
            "schemaVersion": 1,
            "templateId": "aws-ami-assets-v1",
            "namespaces": ["catalog"],
            "assets": {
                "LogoUrl": {"source": "{catalog.logo.imageurl}", "optional": True},
                "PromotionalMedia": {
                    "source": "{catalog.screenshots}",
                    "maxItems": 5,
                    "type": "Image",
                    "title": "{catalog.trademark} screenshot {index}",
                },
            },
        }
        self.legacy_template_path.write_text(json.dumps(self.legacy_template), encoding="utf-8")

    def render_catalog_assets(self, item):
        return offer_assets.render_assets(item, self.legacy_template_path)

    def test_renders_logo_and_at_most_five_images(self):
        value = self.render_catalog_assets(
            item_with([image(f"screen-{index}") for index in range(1, 8)])
        )

        self.assertEqual(
            value["content"]["LogoUrl"],
            "https://websoft9-assets.s3.amazonaws.com/odoo/logo.png",
        )
        media = value["content"]["PromotionalMedia"]
        self.assertEqual(len(media), 5)
        self.assertEqual(
            [entry["Title"] for entry in media],
            [f"Odoo screenshot {index}" for index in range(1, 6)],
        )
        self.assertTrue(all(entry["Type"] == "Image" for entry in media))
        self.assertTrue(all("Description" not in entry for entry in media))
        self.assertIn("only the first 5", " ".join(value["warnings"]))

    def test_supports_string_and_object_screenshots(self):
        value = self.render_catalog_assets(
            item_with(
                [
                    "https://websoft9-assets.s3.amazonaws.com/odoo/one.png",
                    image("two"),
                ]
            )
        )

        media = value["content"]["PromotionalMedia"]
        self.assertEqual(len(media), 2)
        self.assertEqual(media[0]["Url"].rsplit("/", 1)[-1], "one.png")
        self.assertEqual(media[1]["Url"].rsplit("/", 1)[-1], "two.png")

    def test_deduplicates_media_urls(self):
        value = self.render_catalog_assets(
            item_with([image("same"), image("same"), image("other")])
        )

        media = value["content"]["PromotionalMedia"]
        self.assertEqual(len(media), 2)
        self.assertEqual(
            [entry["Url"].rsplit("/", 1)[-1] for entry in media],
            ["same.png", "other.png"],
        )

    def test_missing_logo_and_screenshots_produce_warnings_not_errors(self):
        value = self.render_catalog_assets(item_with(None, logo=False))

        self.assertNotIn("LogoUrl", value["content"])
        self.assertNotIn("PromotionalMedia", value["content"])
        self.assertIn("no logo image URL", " ".join(value["warnings"]))
        self.assertIn("no screenshots", " ".join(value["warnings"]))

    def test_non_s3_urls_are_reported_before_apply(self):
        value = self.render_catalog_assets(
            item_with([image("screen", host="libs.websoft9.com")])
        )

        self.assertIn("not S3-hosted", " ".join(value["warnings"]))

    def test_template_fields_are_declared_in_aws_field_contract(self):
        template = offer.read_json(offer_assets.ASSETS_TEMPLATE)
        fields = set(template["assets"])
        self.assertEqual(fields, {"LogoUrl", "PromotionalMedia"})
        self.assertLessEqual(fields, set(aws_marketplace.FIELD_SPECS))

    def test_template_is_valid_and_capped_at_five(self):
        template = offer.read_json(offer_assets.ASSETS_TEMPLATE)
        offer_assets.validate_assets_template(template)
        self.assertEqual(len(template["assets"]["PromotionalMedia"]), 5)

    def test_rejects_more_than_five_template_slots(self):
        template = offer.read_json(offer_assets.ASSETS_TEMPLATE)
        template["assets"]["PromotionalMedia"].append(
            dict(template["assets"]["PromotionalMedia"][0])
        )
        with self.assertRaisesRegex(ValueError, "1–5"):
            offer_assets.validate_assets_template(template)

    def test_legacy_template_remains_valid_and_capped_at_five(self):
        offer_assets.validate_assets_template(self.legacy_template)
        self.legacy_template["assets"]["PromotionalMedia"]["maxItems"] = 6
        with self.assertRaisesRegex(ValueError, "1–5"):
            offer_assets.validate_assets_template(self.legacy_template)

    def test_renders_v2_template_without_catalog_logo_or_screenshots(self):
        value = offer_assets.render_assets(item_with(None, logo=False))
        template = offer.read_json(offer_assets.ASSETS_TEMPLATE)

        self.assertEqual(value["templateId"], "aws-ami-assets-v2")
        self.assertEqual(value["content"]["LogoUrl"], f"{asset_storage.PUBLIC_BASE}/offers/logo-basic.png")
        media = value["content"]["PromotionalMedia"]
        self.assertEqual(len(media), 5)
        self.assertEqual(
            media[0]["Url"],
            f"{asset_storage.PUBLIC_BASE}/offers/odoo-installation.png",
        )
        self.assertEqual(media[0]["Title"], "Application Install Automation")
        self.assertEqual(media[0]["Description"], "Application Install Automation")
        self.assertEqual(media[1:], [{**p, "Url": template["baseUrl"] + "/" + p["Url"]}
                                     for p in template["assets"]["PromotionalMedia"][1:]])
        self.assertEqual(value["preview"]["assetSummary"], {"logo": True, "media": 5})
        self.assertEqual(value["warnings"], [])

    def test_default_template_has_one_base_url_and_relative_image_paths(self):
        template = offer.read_json(offer_assets.ASSETS_TEMPLATE)
        self.assertEqual(template["baseUrl"], asset_storage.PUBLIC_BASE)
        self.assertEqual(json.dumps(template).count(asset_storage.PUBLIC_BASE), 1)
        self.assertFalse(template["assets"]["LogoUrl"].startswith("https://"))
        self.assertTrue(all(not p["Url"].startswith("https://") for p in template["assets"]["PromotionalMedia"]))

    def test_relative_paths_cannot_escape_base_and_base_must_be_unsigned_https(self):
        for value in ("http://example.com", "https://example.com?token=SECRET", "https://user:pass@example.com",
                      "https://{catalog.domain}"):
            template = offer.read_json(offer_assets.ASSETS_TEMPLATE)
            template["baseUrl"] = value
            with self.assertRaises(ValueError):
                offer_assets.validate_assets_template(template)
        for path in ("../image.png", "%2e%2e/image.png", "//evil.example/image.png", "\\evil\\image.png"):
            with self.assertRaises(ValueError):
                offer_assets._resolve_url(path, "https://example.com/base")
        self.assertEqual(offer_assets._resolve_url("offers/image.png", "https://example.com/"),
                         "https://example.com/offers/image.png")
        with self.assertRaisesRegex(ValueError, "requires baseUrl"):
            offer_assets._resolve_url("offers/image.png", None)
        self.assertEqual(offer_assets._resolve_url("https://other.example/image.png", "https://example.com"),
                         "https://other.example/image.png")

    def test_rejects_mismatched_template_version(self):
        template = offer.read_json(offer_assets.ASSETS_TEMPLATE)
        template["templateId"] = "aws-ami-assets-v1"
        with self.assertRaisesRegex(ValueError, "unsupported or mismatched"):
            offer_assets.validate_assets_template(template)

    def test_rejects_invalid_v2_template_fields(self):
        for field, value, message in [
            ("Type", "Video", "Type must be Image"),
            ("Url", None, "Url must be non-empty text"),
            ("Title", "", "Title must be non-empty text"),
            ("Description", [], "Description must be text"),
            ("Unknown", "value", "unsupported fields"),
        ]:
            with self.subTest(field=field):
                template = offer.read_json(offer_assets.ASSETS_TEMPLATE)
                template["assets"]["PromotionalMedia"][0][field] = value
                with self.assertRaisesRegex(ValueError, message):
                    offer_assets.validate_assets_template(template)

    def test_v2_rendering_validates_resolved_content(self):
        for app_id, message in [
            ("bad id", "requires an HTTPS URL"),
            ("{unresolved}", "unresolved template placeholders"),
        ]:
            with self.subTest(app_id=app_id):
                item = item_with(None, logo=False)
                item["id"] = app_id
                with self.assertRaisesRegex(ValueError, message):
                    offer_assets.render_assets(item)

    def test_media_validation_allows_presigned_urls_and_enforces_counts(self):
        image_item = {
            "Type": "Image",
            "Url": "https://bucket.s3.amazonaws.com/image.png?X-Amz-Signature=test",
            "Title": "Image",
        }
        offer.validate_promotional_media([image_item])

        with self.assertRaisesRegex(ValueError, "at most 5 videos"):
            offer.validate_promotional_media([
                {
                    "Type": "Video",
                    "Url": f"https://bucket.s3.amazonaws.com/video-{index}.mp4",
                    "Title": f"Video {index}",
                }
                for index in range(6)
            ])
        with self.assertRaisesRegex(ValueError, "duplicates"):
            offer.validate_promotional_media([image_item, image_item])

    def test_cli_assets_build_writes_the_asset_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.json"
            output = Path(directory) / "odoo-assets.json"
            entry = item_with([image("one"), image("two")])
            entry["key"] = entry.pop("id")
            catalog.write_text(
                json.dumps({"items": [entry]}),
                encoding="utf-8",
            )
            result = CliRunner().invoke(
                cli,
                [
                    "offer", "assets", "build",
                    "--app", "odoo",
                    "--catalog", str(catalog),
                    "--output", str(output),
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(document["templateId"], "aws-ami-assets-v2")
            self.assertEqual(len(document["content"]["PromotionalMedia"]), 5)
            self.assertTrue(document["content"]["LogoUrl"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
