from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from cli import aws_marketplace, offer, offer_assets
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
    def test_renders_logo_and_at_most_five_images(self):
        value = offer_assets.render_assets(
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
        value = offer_assets.render_assets(
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
        value = offer_assets.render_assets(
            item_with([image("same"), image("same"), image("other")])
        )

        media = value["content"]["PromotionalMedia"]
        self.assertEqual(len(media), 2)
        self.assertEqual(
            [entry["Url"].rsplit("/", 1)[-1] for entry in media],
            ["same.png", "other.png"],
        )

    def test_missing_logo_and_screenshots_produce_warnings_not_errors(self):
        value = offer_assets.render_assets(item_with(None, logo=False))

        self.assertNotIn("LogoUrl", value["content"])
        self.assertNotIn("PromotionalMedia", value["content"])
        self.assertIn("no logo image URL", " ".join(value["warnings"]))
        self.assertIn("no screenshots", " ".join(value["warnings"]))

    def test_non_s3_urls_are_reported_before_apply(self):
        value = offer_assets.render_assets(
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
        self.assertEqual(template["assets"]["PromotionalMedia"]["maxItems"], 5)

    def test_rejects_more_than_five_template_slots(self):
        template = offer.read_json(offer_assets.ASSETS_TEMPLATE)
        template["assets"]["PromotionalMedia"]["maxItems"] = 6
        with self.assertRaisesRegex(ValueError, "1–5"):
            offer_assets.validate_assets_template(template)

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
            self.assertEqual(document["templateId"], "aws-ami-assets-v1")
            self.assertEqual(len(document["content"]["PromotionalMedia"]), 2)
            self.assertTrue(document["content"]["LogoUrl"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
