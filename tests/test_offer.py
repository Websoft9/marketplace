from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from cli import aws_inventory, aws_marketplace, catalog_data, offer, offer_resolve, store
from cli.cli import cli
from cli.commands import offer as offer_commands
from tests.click_support import separated_runner
from tests.offer_fixtures import odoo_item


class OfferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.catalog_path = self.directory / "catalog.json"
        self.versions_path = self.directory / "versions.json"
        self.vendor_path = self.directory / "vendor.json"
        self.plan_path = self.directory / "plan.json"
        self.item = odoo_item()
        labels_patch = mock.patch.object(offer_commands, "labels_for", return_value={})
        labels_patch.start()
        self.addCleanup(labels_patch.stop)
        self.item.update({
            "entityId": None,
            "hasenterprise": False,
            "templateId": "aws-ami-v1",
            "locale": "en-US",
            "contentOverrides": {},
            "categories": [],
            "additionalResources": [
                {"Text": "Odoo guide by Websoft9", "Url": "https://support.websoft9.com/en/docs/odoo"},
                {"Text": "Websoft9 Console login guide", "Url": "https://support.websoft9.com/en/docs/login-console"},
            ],
            "logoUrl": None,
            "videoUrls": [],
        })
        self.vendor_data = offer.read_json(offer.DATA / "vendor.json")
        self.write()
        self.runner = CliRunner()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        self.catalog_path.write_text(json.dumps({"items": [self.item]}), encoding="utf-8")
        self.versions_path.write_text(json.dumps([{"id": "odoo", "version": "20.0"}]), encoding="utf-8")
        self.vendor_path.write_text(json.dumps(self.vendor_data), encoding="utf-8")

    def render(self):
        return offer.render(self.item, "20.0", self.vendor_path)

    def set_entity_id(self):
        self.item["entityId"] = "prod-example12345"
        self.write()

    def entity(self, value):
        return {
            "EntityIdentifier": "prod-example12345@4",
            "EntityType": "AmiProduct@1.0",
            "DetailsDocument": aws_marketplace.to_read_document(value["content"]),
        }

    def args(self, command):
        return [
            "offer", command, "--app", "odoo",
            "--catalog", str(self.catalog_path),
            "--versions", str(self.versions_path),
            "--vendor", str(self.vendor_path),
        ]

    def test_example_is_consistent_draft_and_publish_check_fails(self):
        self.item["hasenterprise"] = True
        self.write()
        value = self.render()
        self.assertIn("Deploy Odoo 20.0", value["content"]["ShortDescription"])
        self.assertNotIn("Community Edition", value["content"]["ShortDescription"])
        self.assertIn(
            "This product packages Odoo Community Edition as a hardened AMI",
            value["content"]["LongDescription"],
        )
        self.assertIn(
            "Odoo is a trademark of its respective owner",
            value["content"]["LongDescription"],
        )
        self.assertNotIn("Jupyter", str(value["content"]))
        self.assertEqual(value["publishBlockers"], ["listing: Catalog EntityId is missing"])
        result = self.runner.invoke(
            cli, self.args("build") + ["--cloud", "aws", "--publish-ready"]
        )
        self.assertNotEqual(result.exit_code, 0)

    def test_short_description_uses_multi_version_choice(self):
        self.item["hasenterprise"] = True
        self.write()
        value = offer.render(
            self.item,
            "20.0",
            self.vendor_path,
            version_options=["20.0", "19.0", "18.0", "17.0", "latest"],
        )
        self.assertEqual(
            value["content"]["ShortDescription"],
            "Choose from Odoo 20.0, 19.0, 18.0, or 17.0 at launch for self-hosting "
            "directly from upstream release, backed by a 24-hour response SLA on business days. "
            "Use the Websoft9 Console to manage HTTPS, backups, monitoring, "
            "upgrades, and rollback.",
        )

    def test_short_description_uses_single_version(self):
        self.item["hasenterprise"] = True
        self.write()
        value = offer.render(
            self.item,
            "20.0",
            self.vendor_path,
            version_options=["20.0", "latest"],
        )
        self.assertEqual(
            value["content"]["ShortDescription"],
            "Deploy Odoo 20.0 for self-hosting directly from upstream release, backed by "
            "a 24-hour response SLA on business days. Use the Websoft9 Console "
            "to manage HTTPS, backups, monitoring, upgrades, and rollback.",
        )

    def test_missing_version_does_not_leave_consecutive_spaces(self):
        value = offer.render(self.item, "", self.vendor_path, version_options=[])
        self.assertIn(
            "Deploy Odoo for self-hosting",
            value["content"]["ShortDescription"],
        )
        self.assertNotIn("  ", value["content"]["ShortDescription"])
        self.assertIn("versions: version is missing", value["publishBlockers"])

    def test_build_reports_missing_version_as_the_source_blocker(self):
        self.item["entityId"] = "prod-example12345"
        self.write()
        self.versions_path.write_text("[]", encoding="utf-8")
        runner = separated_runner()
        output = self.directory / "aws" / "odoo.json"

        result = runner.invoke(
            cli,
            self.args("build") + ["--cloud", "aws", "--output", str(output)],
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("versions: version is missing", result.stderr)
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(document["publishBlockers"], ["versions: version is missing"])

        result = runner.invoke(
            cli,
            self.args("build")
            + ["--cloud", "aws", "--publish-ready", "--output", str(output)],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("versions: version is missing", result.stderr)

    def test_related_apps_are_inserted_before_nginx_and_docker(self):
        self.item["relatedApps"] = []
        self.item["hasenterprise"] = True
        self.write()
        empty_included = next(
            section["text"]
            for section in self.render()["preview"]["longDescriptionSections"]
            if section["heading"] == "What is included?"
        )
        self.assertEqual(
            empty_included,
            "Odoo Community Edition, Nginx, Docker and Websoft9 Console.",
        )
        self.item["relatedApps"] = ["PostgreSQL", "pgAdmin"]
        self.write()
        included = next(
            section["text"]
            for section in self.render()["preview"]["longDescriptionSections"]
            if section["heading"] == "What is included?"
        )
        self.assertEqual(
            included,
            "Odoo Community Edition, PostgreSQL, pgAdmin, Nginx, Docker and Websoft9 Console.",
        )

    def test_multi_version_apps_append_selection_to_included(self):
        self.item["hasenterprise"] = True
        self.write()
        value = offer.render(
            self.item, "20.0", self.vendor_path, version_options=["20", "19", "18"]
        )
        included = next(
            section["text"]
            for section in value["preview"]["longDescriptionSections"]
            if section["heading"] == "What is included?"
        )
        self.assertIn("Odoo Community Edition 20/19/18 for selection,", included)

    def test_latest_is_excluded_from_version_selection(self):
        self.item["hasenterprise"] = True
        self.write()
        value = offer.render(
            self.item, "20.0", self.vendor_path, version_options=["20", "latest", "19"]
        )
        included = next(
            section["text"]
            for section in value["preview"]["longDescriptionSections"]
            if section["heading"] == "What is included?"
        )
        self.assertIn("Odoo Community Edition 20/19 for selection,", included)
        self.assertNotIn("latest", included)

    def test_commercial_versions_are_excluded_from_multi_version_rendering(self):
        self.item["hasenterprise"] = True
        self.write()
        value = offer.render(
            self.item,
            "2026.10",
            self.vendor_path,
            version_options=[
                "2026.10",
                "2026.10-enterprise",
                "5.26",
                "5.26-ee",
            ],
        )
        self.assertIn(
            "Choose from Odoo 2026.10 or 5.26 at launch",
            value["content"]["ShortDescription"],
        )
        included = next(
            section["text"]
            for section in value["preview"]["longDescriptionSections"]
            if section["heading"] == "What is included?"
        )
        self.assertIn("Odoo Community Edition 2026.10/5.26 for selection,", included)
        self.assertNotIn("enterprise", value["content"]["LongDescription"])
        self.assertNotIn("5.26-ee", value["content"]["LongDescription"])

    def test_commercial_versions_do_not_enable_multi_version_wording(self):
        self.item["hasenterprise"] = True
        self.write()
        value = offer.render(
            self.item,
            "2026.10",
            self.vendor_path,
            version_options=["2026.10", "2026.10-enterprise"],
        )
        self.assertIn("Deploy Odoo 2026.10", value["content"]["ShortDescription"])
        self.assertNotIn("for selection", value["content"]["LongDescription"])

    def test_is_community_version_recognizes_edition_markers(self):
        for value in ("5.26-enterprise", "5.26-Enterprise", "5.26-ee", "5.26_EE"):
            self.assertFalse(offer.is_community_version(value))
        for value in ("5.26", "2026.10", "v3-core", "green"):
            self.assertTrue(offer.is_community_version(value))

    def test_single_version_or_non_multi_app_has_no_selection(self):
        single = offer.render(
            self.item, "20.0", self.vendor_path, version_options=["20"]
        )
        texts = [section["text"] for section in single["preview"]["longDescriptionSections"]]
        self.assertFalse(any("for selection" in text for text in texts))

        path = self.special_cases_file({"odoo": {"multiVersion": False}})
        other = offer.render(
            self.item, "20.0", self.vendor_path, version_options=["20", "19"],
            special_cases_path=path,
        )
        texts = [section["text"] for section in other["preview"]["longDescriptionSections"]]
        self.assertFalse(any("for selection" in text for text in texts))

    def test_multi_version_flag_comes_from_special_cases(self):
        path = self.special_cases_file({"odoo": {"multiVersion": True}})
        value = offer.render(
            self.item, "20.0", self.vendor_path, version_options=["20", "19"],
            special_cases_path=path,
        )
        self.assertIn("Choose from Odoo 20 or 19 at launch", value["content"]["ShortDescription"])
        included = next(
            section["text"]
            for section in value["preview"]["longDescriptionSections"]
            if section["heading"] == "What is included?"
        )
        self.assertIn("Odoo 20/19 for selection,", included)

    def test_special_case_multi_version_requires_boolean(self):
        path = self.special_cases_file({"odoo": {"multiVersion": "yes"}})
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            offer.render(
                self.item, "20.0", self.vendor_path, special_cases_path=path
            )

    def test_special_case_multi_offers_are_accepted(self):
        path = self.special_cases_file(
            {"wordpress": {"multiOffers": ["", "Multisite"]}}
        )
        cases = offer.load_special_cases(path)
        self.assertEqual(cases["wordpress"]["multiOffers"], ["", "Multisite"])

    def test_special_case_multi_offers_allow_combined_os_and_label(self):
        path = self.special_cases_file(
            {"wordpress": {"multiOffers": ["Ubuntu; Multisite"]}}
        )
        cases = offer.load_special_cases(path)
        self.assertEqual(cases["wordpress"]["multiOffers"], ["Ubuntu; Multisite"])

    def test_special_case_multi_offers_reject_multiple_os_parts(self):
        path = self.special_cases_file(
            {"wordpress": {"multiOffers": ["Ubuntu; Debian"]}}
        )
        with self.assertRaisesRegex(ValueError, "more than one OS"):
            offer.load_special_cases(path)

    def test_special_case_multi_offers_reject_versioned_os(self):
        path = self.special_cases_file(
            {"odoo": {"multiOffers": ["Ubuntu 26.04"]}}
        )
        with self.assertRaisesRegex(ValueError, "family name without a version"):
            offer.load_special_cases(path)

    def test_special_case_multi_offers_reject_empty_parts(self):
        path = self.special_cases_file(
            {"wordpress": {"multiOffers": ["Ubuntu;"]}}
        )
        with self.assertRaisesRegex(ValueError, "empty part"):
            offer.load_special_cases(path)

    def test_special_case_multi_offers_reject_empty_and_duplicates(self):
        for value, message in (
            ([], "non-empty array"),
            (["Ubuntu", "Ubuntu"], "duplicates"),
        ):
            path = self.special_cases_file({"odoo": {"multiOffers": value}})
            with self.assertRaisesRegex(ValueError, message):
                offer.load_special_cases(path)

    def test_display_version_keeps_at_most_two_components(self):
        self.assertEqual(offer.display_version("20.0.1"), "20.0")
        self.assertEqual(offer.display_version("v2026.9.24"), "v2026.9")
        self.assertEqual(offer.display_version("19.0"), "19.0")
        self.assertEqual(offer.display_version("18"), "18")
        self.assertEqual(offer.display_version("v3-core"), "v3-core")

    def test_multi_version_selection_uses_display_versions(self):
        self.item["hasenterprise"] = True
        self.write()
        value = offer.render(
            self.item,
            "20.0.1",
            self.vendor_path,
            version_options=["20.0.1", "19.0.2", "19.0.3", "latest"],
        )
        included = next(
            section["text"]
            for section in value["preview"]["longDescriptionSections"]
            if section["heading"] == "What is included?"
        )
        self.assertIn("Odoo Community Edition 20.0/19.0 for selection,", included)
        self.assertNotIn("20.0.1", value["content"]["LongDescription"])

    def test_load_versions_accepts_single_value_and_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "versions.json"
            path.write_text(
                json.dumps(
                    [
                        {"id": "odoo", "version": ["20", "19"]},
                        {"id": "mysql", "version": "26.10"},
                        {"id": "redis", "versions": ["8", "7"]},
                        {"id": "no-version"},
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                catalog_data.load_versions(str(path)),
                {"odoo": ["20", "19"], "mysql": ["26.10"], "redis": ["8", "7"]},
            )

    def test_long_description_edition_follows_hasenterprise(self):
        self.item["hasenterprise"] = False
        self.write()
        community = self.render()["preview"]["longDescriptionSections"][0]["text"]
        self.assertIn(
            "This product packages Odoo as a hardened AMI",
            community,
        )
        self.assertNotIn("Community Edition", community)
        self.item["hasenterprise"] = True
        self.write()
        enterprise = self.render()["preview"]["longDescriptionSections"][0]["text"]
        self.assertIn(
            "This product packages Odoo Community Edition as a hardened AMI",
            enterprise,
        )

    def test_hasenterprise_uses_open_source_offer_wording(self):
        self.item["hasenterprise"] = True
        self.write()
        pricing = next(
            section["text"]
            for section in self.render()["preview"]["longDescriptionSections"]
            if section["heading"] == "What you pay for"
        )
        self.assertIn(
            "This offer provides Odoo Community Edition. "
            "It does not include any commercial edition.",
            pricing,
        )
        self.assertNotIn("provided under its commercial edition", pricing)

    def test_no_community_edition_when_hasenterprise_is_false(self):
        self.item["hasenterprise"] = False
        self.write()
        value = self.render()
        first = value["preview"]["longDescriptionSections"][0]["text"]
        pricing = next(
            section["text"]
            for section in value["preview"]["longDescriptionSections"]
            if section["heading"] == "What you pay for"
        )
        self.assertIn("This product packages Odoo as a hardened AMI", first)
        self.assertNotIn("Community Edition", first)
        self.assertNotIn("Community Edition", pricing)
        self.assertTrue(
            pricing.startswith("No separate license fee is charged for the application.")
        )

    def special_item(self, app_id, trademark):
        item = copy.deepcopy(self.item)
        item["id"] = app_id
        item["trademark"] = trademark
        item["hasenterprise"] = True
        return item

    def test_special_case_edition_label_overrides_default(self):
        mongodb = offer.render(
            self.special_item("mongodb", "MongoDB"), "8.0", self.vendor_path
        )
        self.assertIn(
            "This product packages MongoDB Community Server as a hardened AMI",
            mongodb["content"]["LongDescription"],
        )
        self.assertIn(
            "This offer provides MongoDB Community Server.",
            mongodb["content"]["LongDescription"],
        )
        self.assertNotIn("MongoDB Community Edition", mongodb["content"]["LongDescription"])

        redis = offer.render(self.special_item("redis", "Redis"), "8.0", self.vendor_path)
        self.assertIn("This product packages Redis Open Source as", redis["content"]["LongDescription"])

    def test_title_trademark_override_only_changes_product_title(self):
        path = self.special_cases_file({"odoo": {"titleTrademark": "Document DB"}})
        value = offer.render(
            self.item, "20.0", self.vendor_path, special_cases_path=path
        )
        self.assertEqual(
            value["content"]["ProductTitle"],
            "Document DB on AWS - Preconfigured, Hardened, Ready in Minutes",
        )
        self.assertEqual(value["titleTrademark"], "Document DB")
        self.assertIn("This product packages Odoo as a hardened AMI", value["content"]["LongDescription"])
        self.assertNotIn("Document DB", value["content"]["LongDescription"])

    def test_title_trademark_is_used_for_offer_code_labels(self):
        path = self.special_cases_file({"odoo": {"titleTrademark": "Document DB"}})
        value = offer.render(
            self.item, "20.0", self.vendor_path, special_cases_path=path
        )
        variant = offer.with_variant(value, "Multisite")
        self.assertEqual(
            variant["content"]["ProductTitle"],
            "Document DB Multisite on AWS - Preconfigured, Hardened, Ready in Minutes",
        )

    def test_mongodb_uses_its_title_trademark_override(self):
        mongodb = offer.render(
            self.special_item("mongodb", "MongoDB"), "8.0", self.vendor_path
        )
        self.assertEqual(
            mongodb["content"]["ProductTitle"],
            "Document DB on AWS - Preconfigured, Hardened, Ready in Minutes",
        )
        self.assertIn(
            "This product packages MongoDB Community Server as a hardened AMI",
            mongodb["content"]["LongDescription"],
        )

    def test_empty_title_trademark_is_rejected(self):
        path = self.special_cases_file({"odoo": {"titleTrademark": ""}})
        with self.assertRaisesRegex(ValueError, "titleTrademark must not be empty"):
            offer.render(
                self.item, "20.0", self.vendor_path, special_cases_path=path
            )

    def test_empty_edition_label_omits_the_suffix(self):
        path = self.special_cases_file({"postgresql": {"editionLabel": ""}})
        postgresql = offer.render(
            self.special_item("postgresql", "PostgreSQL"), "17", self.vendor_path,
            special_cases_path=path,
        )
        self.assertIn(
            "This product packages PostgreSQL as a hardened AMI",
            postgresql["content"]["LongDescription"],
        )
        self.assertTrue(
            postgresql["content"]["LongDescription"].count("PostgreSQL Edition") == 0
        )
        self.assertIn(
            "No separate license fee is charged for the application.",
            postgresql["content"]["LongDescription"],
        )

    def special_cases_file(self, apps):
        path = self.directory / "special-cases.json"
        path.write_text(
            json.dumps({"schemaVersion": 1, "apps": apps}), encoding="utf-8"
        )
        return path

    def test_special_case_pricing_lead_override(self):
        path = self.special_cases_file({"odoo": {"pricingLead": "Custom pricing sentence."}})
        value = offer.render(self.item, "20.0", self.vendor_path, special_cases_path=path)
        self.assertIn(
            "Custom pricing sentence. The Websoft9 software fee",
            value["content"]["LongDescription"],
        )

    def test_special_case_rejects_unknown_slots_and_schema(self):
        unknown = self.special_cases_file({"odoo": {"community": "yes"}})
        with self.assertRaisesRegex(ValueError, "unsupported key"):
            offer.render(self.item, "20.0", self.vendor_path, special_cases_path=unknown)
        path = self.directory / "bad-schema.json"
        path.write_text(json.dumps({"schemaVersion": 2, "apps": {}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schemaVersion"):
            offer.render(self.item, "20.0", self.vendor_path, special_cases_path=path)

    def test_special_case_change_invalidates_the_source_hash(self):
        baseline = self.render()["sourceHash"]
        path = self.special_cases_file({"odoo": {"editionLabel": ""}})
        changed = offer.render(self.item, "20.0", self.vendor_path, special_cases_path=path)
        self.assertNotEqual(baseline, changed["sourceHash"])

    def test_consecutive_spaces_are_rejected(self):
        content = self.render()["content"]
        content["ShortDescription"] = "Two  spaces"
        with self.assertRaisesRegex(
            ValueError, r"content\.ShortDescription contains consecutive spaces"
        ):
            offer.validate_content(content)

    def test_offer_labels_split_operating_systems_from_other_labels(self):
        self.assertTrue(offer.is_os_label("Ubuntu 26.04"))
        self.assertTrue(offer.is_os_label("Debian 13"))
        self.assertTrue(offer.is_os_label("Red Hat Enterprise Linux 9"))
        self.assertFalse(offer.is_os_label("Multisite"))
        self.assertFalse(offer.is_os_label("Enterprise"))
        self.assertEqual(
            offer.split_offer_label("Ubuntu 26.04; Multisite"),
            ("Ubuntu 26.04", ["Multisite"]),
        )
        self.assertEqual(offer.split_offer_label("Multisite"), ("", ["Multisite"]))
        self.assertEqual(offer.split_offer_label(""), ("", []))

    def test_variant_title_replaces_the_platform_phrase_for_an_os(self):
        self.assertEqual(
            offer.variant_title(
                "Odoo on AWS - Preconfigured, Hardened, Ready in Minutes",
                "Odoo",
                "Ubuntu 26.04",
            ),
            "Odoo on Ubuntu 26.04 - Preconfigured, Hardened, Ready in Minutes",
        )

    def test_variant_title_inserts_a_non_os_label_after_the_trademark(self):
        self.assertEqual(
            offer.variant_title(
                "WordPress on AWS - Preconfigured, Hardened, Ready in Minutes",
                "WordPress",
                "Multisite",
            ),
            "WordPress Multisite on AWS - Preconfigured, Hardened, Ready in Minutes",
        )

    def test_variant_title_combines_os_and_non_os_labels(self):
        self.assertEqual(
            offer.variant_title(
                "WordPress on AWS - Preconfigured, Hardened, Ready in Minutes",
                "WordPress",
                "Ubuntu 26.04; Multisite",
            ),
            "WordPress Multisite on Ubuntu 26.04 - Preconfigured, Hardened, Ready in Minutes",
        )

    def test_with_variant_updates_content_preview_and_source_hash(self):
        value = self.render()
        variant = offer.with_variant(value, "Ubuntu 26.04")
        self.assertEqual(
            variant["content"]["ProductTitle"], "Odoo on Ubuntu 26.04 - Preconfigured, Hardened, Ready in Minutes"
        )
        self.assertEqual(
            variant["preview"]["content"]["ProductTitle"], variant["content"]["ProductTitle"]
        )
        self.assertNotEqual(value["sourceHash"], variant["sourceHash"])
        self.assertIs(value, offer.with_variant(value, ""))

    def test_with_variant_rejects_a_title_over_the_aws_limit(self):
        value = self.render()
        value["trademark"] = "Odoo"
        with self.assertRaisesRegex(ValueError, "maximum 72"):
            offer.with_variant(value, "Ubuntu 26.04" * 8)

    def test_multi_plan_uses_each_codes_label(self):
        self.item["hasenterprise"] = True
        self.write()
        value = self.render()
        def entity_for(entity_id):
            return {
                "EntityIdentifier": f"{entity_id}@1",
                "EntityType": "AmiProduct@1.0",
                "DetailsDocument": aws_marketplace.to_read_document(value["content"]),
            }
        entities = [
            {"code": "code-a", "entityId": "prod-a", "entity": entity_for("prod-a")},
            {"code": "code-b", "entityId": "prod-b", "entity": entity_for("prod-b")},
        ]
        planned = offer.multi_plan(
            value, entities, {"code-a": "Ubuntu 26.04", "code-b": "Debian 13"}
        )
        titles = [
            target["plan"]["request"]["ChangeSet"][0]["DetailsDocument"]["ProductTitle"]
            for target in planned["targets"]
        ]
        self.assertEqual(
            titles,
            [
                "Odoo on Ubuntu 26.04 - Preconfigured, Hardened, Ready in Minutes",
                "Odoo on Debian 13 - Preconfigured, Hardened, Ready in Minutes",
            ],
        )

    def test_build_warns_and_lists_over_limit_titles(self):
        self.item["trademark"] = "Test Application Name"
        self.item["offerCodes"] = {"aws": ["code-a"]}
        self.write()
        with mock.patch.object(
            offer_commands, "labels_for",
            return_value={"code-a": "Ubuntu 26.04; Enterprise"},
        ):
            runner = separated_runner()
            result = runner.invoke(
                cli, self.args("build") + ["--cloud", "aws", "--output", "-"]
            )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("WARNING odoo code-a", result.stderr)
        self.assertIn("maximum 72", result.stderr)
        document = json.loads(result.stdout)
        warnings = document["titleWarnings"]
        self.assertEqual(warnings[0]["code"], "code-a")
        self.assertEqual(warnings[0]["field"], "ProductTitle")
        self.assertGreater(warnings[0]["length"], 72)

    def test_build_warns_when_search_keywords_are_truncated(self):
        self.item["keywords"] = [f"keyword-{index}" for index in range(17)]
        self.write()
        runner = separated_runner()
        result = runner.invoke(
            cli, self.args("build") + ["--cloud", "aws", "--output", "-"]
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn(
            "WARNING odoo: SearchKeywords truncated 17 -> 15", result.stderr
        )
        document = json.loads(result.stdout)
        self.assertEqual(document["keywordTruncation"]["original"], 17)
        self.assertEqual(len(document["content"]["SearchKeywords"]), 15)

    def test_resolve_records_offer_targets_in_the_catalog(self):
        product = offer_resolve.Product(
            code="code-u",
            entity_id="prod-example12345",
            title="Odoo on Ubuntu 26.04",
            visibility="Public",
        )
        with mock.patch.object(
            aws_inventory, "list_aws_products", return_value=[product]
        ), mock.patch.object(offer_resolve, "load_identities", return_value={"odoo": ["Odoo"]}):
            result = self.runner.invoke(
                cli,
                self.args("resolve")
                + ["--cloud", "aws", "--output", "-"],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        document = json.loads(result.stdout)
        self.assertEqual(document["targets"][0]["offer"], "Ubuntu")
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        item = catalog["items"][0]
        self.assertEqual(item["entityId"], "prod-example12345")
        self.assertEqual(
            item["targets"],
            [
                {
                    "code": "code-u",
                    "entityId": "prod-example12345",
                    "offer": "Ubuntu",
                }
            ],
        )

    def store_inventory(self, products):
        with mock.patch.object(store, "collect_products", return_value=products):
            return store.query_products(["aws"])

    def test_resolve_reuses_a_public_store_snapshot_without_inventory_network(self):
        facts = self.store_inventory([
            offer_resolve.Product("code-u", "prod-u", "Odoo on Ubuntu", visibility="Public",
                                  alternate_code="odoo_ubuntu"),
        ])
        snapshot = self.directory / "inventory.json"
        snapshot.write_text(json.dumps(facts))
        original_facts = snapshot.read_bytes()
        with mock.patch.object(store, "query_platform", side_effect=AssertionError("no inventory query")), \
                mock.patch.object(offer_resolve, "list_aws_products", side_effect=AssertionError("no bypass")), \
                mock.patch.object(offer_resolve, "load_identities", return_value={"odoo": ["Odoo"]}):
            result = self.runner.invoke(cli, self.args("resolve") + [
                "--cloud", "aws", "--inventory", str(snapshot), "--visibility", "public", "--output", "-",
                "--code", "odoo_ubuntu",
            ])
        self.assertEqual(result.exit_code, 0, result.output)
        document = json.loads(result.stdout)
        self.assertEqual(document["inventory"]["source"], "snapshot")
        self.assertEqual(document["inventory"]["scope"], {"productType": "image", "visibility": "public"})
        self.assertEqual(document["inventory"]["queriedAt"], facts["results"][0]["queriedAt"])
        self.assertEqual(document["targets"][0]["entityId"], "prod-u")
        self.assertEqual(document["targets"][0]["appMatch"]["appId"], "odoo")
        self.assertEqual(snapshot.read_bytes(), original_facts)

    def test_resolve_warns_about_ffmpeg_null_metadata_and_still_resolves_the_target(self):
        identities = self.directory / "products.json"
        identities.write_text(json.dumps([
            {"key": "odoo", "trademark": "Odoo"}, {"key": "ffmpeg", "trademark": None},
        ]))
        facts = self.store_inventory([offer_resolve.Product("code", "prod", "Odoo", visibility="Public")])
        snapshot = self.directory / "inventory.json"
        snapshot.write_text(json.dumps(facts))
        result = separated_runner().invoke(cli, self.args("resolve") + [
            "--cloud", "aws", "--inventory", str(snapshot), "--product-catalog", str(identities), "--output", "-",
        ])
        self.assertEqual(result.exit_code, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["targets"][0]["code"], "code")
        self.assertTrue(report["matching"]["degraded"])
        self.assertEqual(report["matching"]["warnings"][0]["appId"], "ffmpeg")
        self.assertIn("WARNING identity catalog [ffmpeg]", result.stderr)

    def test_resolve_unsafe_missing_identity_preserves_temporary_catalog(self):
        identities = self.directory / "products.json"
        identities.write_text(json.dumps([
            {"key": "odoo", "trademark": "Odoo"}, {"key": "php", "trademark": None},
        ]))
        facts = self.store_inventory([offer_resolve.Product("code", "prod", "Odoo", visibility="Public")])
        snapshot = self.directory / "inventory.json"
        snapshot.write_text(json.dumps(facts))
        before = self.catalog_path.read_bytes()
        result = separated_runner().invoke(cli, self.args("resolve") + [
            "--cloud", "aws", "--inventory", str(snapshot), "--product-catalog", str(identities), "--code", "code",
        ])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("unsafe for automatic fallback", result.stderr)
        self.assertEqual(self.catalog_path.read_bytes(), before)

    def test_resolve_public_query_ignores_restricted_duplicate_offer_and_code_cannot_expand_scope(self):
        runner = separated_runner()
        client = mock.Mock()
        client.list_entities.return_value = {"EntitySummaryList": [
            {"EntityId": "prod-public", "AmiProductSummary": {"Visibility": "Public"}},
            {"EntityId": "prod-hidden", "AmiProductSummary": {"Visibility": "Restricted"}},
        ]}
        client.batch_describe_entities.return_value = {"EntityDetails": {
            "prod-public": {"DetailsDocument": {"Description": {
                "ProductCode": "public-code", "ProductTitle": "Odoo on Ubuntu", "Visibility": "Public",
            }}},
        }}
        with mock.patch.object(aws_inventory, "aws_client", return_value=client), \
                mock.patch.object(offer_resolve, "load_identities", return_value={"odoo": ["Odoo"]}):
            result = runner.invoke(cli, self.args("resolve") + ["--cloud", "aws", "--output", "-"])
            self.assertEqual(result.exit_code, 0, result.output)
            report = json.loads(result.stdout)
            self.assertEqual(report["inventory"]["source"], "live")
            self.assertEqual(report["targets"][0]["entityId"], "prod-public")
            self.assertEqual(client.list_entities.call_args.kwargs["EntityTypeFilters"], {
                "AmiProductFilters": {"Visibility": {"ValueList": ["Public"]}},
            })
            requested = client.batch_describe_entities.call_args.kwargs["EntityRequestList"]
            self.assertEqual([r["EntityId"] for r in requested], ["prod-public"])
            before = self.catalog_path.read_bytes()
            result = runner.invoke(cli, self.args("resolve") + ["--cloud", "aws", "--code", "hidden-code"])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("not found", result.stdout + result.stderr)
            self.assertEqual(self.catalog_path.read_bytes(), before)

    def test_resolve_rejects_failed_nonpublic_or_wrong_scope_facts_before_catalog_mutation(self):
        document = self.store_inventory([
            offer_resolve.Product("code-u", "prod-u", "Odoo", visibility="Public"),
        ])
        original = self.catalog_path.read_bytes()
        for mutate in (
            lambda d: d.update(complete=False),
            lambda d: d.update(errors=[{"code": "Error"}]),
            lambda d: d.update(scope={"productType": "image", "visibility": "all"}),
            lambda d: d["products"][0].update(visibility="Restricted", public=False),
        ):
            facts = copy.deepcopy(document["results"][0])
            mutate(facts)
            with mock.patch.object(store, "get_inventory", return_value=facts), \
                    mock.patch.object(offer_resolve, "load_identities") as identities:
                result = self.runner.invoke(cli, self.args("resolve") + ["--cloud", "aws"])
            self.assertNotEqual(result.exit_code, 0)
            self.assertEqual(self.catalog_path.read_bytes(), original)
            identities.assert_not_called()

    def test_resolve_rejects_inventory_profile_conflict_and_unsupported_visibility_before_query(self):
        snapshot = self.directory / "inventory.json"
        snapshot.write_text("{}")
        for flags in (["--inventory", str(snapshot), "--profile", "seller"],
                      ["--visibility", "all"], ["--visibility", "restricted", "--code", "hidden-code"]):
            with mock.patch.object(store, "get_inventory") as query:
                result = self.runner.invoke(cli, self.args("resolve") + ["--cloud", "aws", *flags])
            self.assertEqual(result.exit_code, 2, result.output)
            query.assert_not_called()

    def test_offer_commands_reject_application_lists_before_io(self):
        for app in ("odoo,wordpress", "odoo，wordpress", "odoo wordpress"):
            for command in ("resolve", "build", "plan", "apply", "preflight", "targets"):
                result = self.runner.invoke(cli, ["offer", command, "--app", app])
                self.assertNotEqual(result.exit_code, 0)
                self.assertIn("exactly one application", result.output)

    def test_build_filters_resolved_targets_and_rejects_bad_combinations(self):
        self.item.update({
            "entityId": "prod-u",
            "targets": [
                {"code": "u", "entityId": "prod-u", "offer": "Ubuntu"},
                {"code": "d", "entityId": "prod-d", "offer": "Debian"},
            ],
        })
        self.write()
        result = self.runner.invoke(cli, self.args("build") + [
            "--cloud", "aws", "--offer-codes", "u", "--output", "-",
        ])
        self.assertEqual(result.exit_code, 0, result.output)
        targets = json.loads(result.stdout)["targets"]
        self.assertEqual([t["code"] for t in targets], ["u"])
        self.assertIn("on Ubuntu", targets[0]["preview"]["content"]["ProductTitle"])
        for flags in (["--offer-codes", "unknown"], ["--offer-codes", ","],
                      ["--offer-codes", "u", "--allow-unlisted-codes"]):
            result = self.runner.invoke(cli, self.args("build") + ["--cloud", "aws", *flags])
            self.assertNotEqual(result.exit_code, 0)

    def test_single_variant_apply_verifies_the_variant_not_base(self):
        for variant in ("Ubuntu", "Multisite", "Ubuntu; Multisite"):
            with self.subTest(variant=variant):
                self.set_entity_id()
                self.item["targets"] = [{"code": "u", "entityId": self.item["entityId"], "offer": variant}]
                self.write()
                value = offer.with_variant(self.render(), variant)
                entity = self.entity(value)
                entity["DetailsDocument"]["Description"]["ProductTitle"] = "Old title"
                planned = offer.plan(value, entity)
                self.plan_path.write_text(json.dumps(planned))
                client = mock.Mock()
                client.describe_entity.side_effect = [entity, self.entity(value)]
                client.start_change_set.return_value = {"ChangeSetId": "cs-u"}
                client.describe_change_set.return_value = {"Status": "SUCCEEDED"}
                with mock.patch.object(offer, "aws_client", return_value=client):
                    result = self.runner.invoke(cli, self.args("apply") + [
                        "--plan", str(self.plan_path), "--wait-seconds", "1",
                    ])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn('"readBackVerified": true', result.output)

    def test_bad_offer_choice_fails_before_aws_inventory(self):
        with mock.patch.object(store, "get_inventory") as fetch:
            result = self.runner.invoke(cli, self.args("resolve") + [
                "--cloud", "aws", "--code", "u", "--offer", "Multisite",
            ])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not declared", result.output)
        fetch.assert_not_called()

    def test_resolve_build_plan_apply_round_trip_with_two_os_offers(self):
        identities = self.directory / "products.json"
        identities.write_text(json.dumps([
            {"key": "odoo", "trademark": "Odoo"},
            {"key": "drupal", "trademark": "Drupal"},
        ]))
        products = [
            offer_resolve.Product("u", "prod-u", "Odoo on Ubuntu", visibility="Public"),
            offer_resolve.Product("d", "prod-d", "Odoo on Debian", visibility="Public"),
            offer_resolve.Product("other", "prod-other", "Drupal", "Integrates with Odoo", visibility="Public"),
        ]
        with mock.patch.object(aws_inventory, "list_aws_products", return_value=products):
            resolved = self.runner.invoke(cli, self.args("resolve") + [
                "--cloud", "aws", "--product-catalog", str(identities),
            ])
        self.assertEqual(resolved.exit_code, 0, resolved.output)
        built = self.runner.invoke(cli, self.args("build") + ["--cloud", "aws", "--output", "-"])
        self.assertEqual(built.exit_code, 0, built.output)
        previews = json.loads(built.stdout)["targets"]
        self.assertEqual({p["code"] for p in previews}, {"u", "d"})
        self.item = json.loads(self.catalog_path.read_text())["items"][0]
        value = self.render()
        old, new = {}, {}
        for target in self.item["targets"]:
            desired = offer.with_variant({**value, "entityId": target["entityId"]}, target["offer"])
            entity = {
                "EntityIdentifier": target["entityId"] + "@1", "EntityType": "AmiProduct@1.0",
                "DetailsDocument": aws_marketplace.to_read_document(desired["content"]),
            }
            new[target["entityId"]] = entity
            old[target["entityId"]] = copy.deepcopy(entity)
            old[target["entityId"]]["DetailsDocument"]["Description"]["ProductTitle"] = "Old title"
        client = mock.Mock()
        client.describe_entity.side_effect = lambda **kw: old[kw["EntityId"]]
        with mock.patch.object(offer, "aws_client", return_value=client):
            planned = self.runner.invoke(cli, self.args("plan") + ["--all-codes", "--output", str(self.plan_path)])
        self.assertEqual(planned.exit_code, 0, planned.output)
        def submit(**kwargs):
            entity_id = kwargs["ChangeSet"][0]["Entity"]["Identifier"].split("@")[0]
            old[entity_id] = new[entity_id]
            return {"ChangeSetId": "cs-" + entity_id}
        client.start_change_set.side_effect = submit
        client.describe_change_set.return_value = {"Status": "SUCCEEDED"}
        with mock.patch.object(offer, "aws_client", return_value=client):
            applied = self.runner.invoke(cli, self.args("apply") + [
                "--plan", str(self.plan_path), "--wait-seconds", "1",
            ])
        self.assertEqual(applied.exit_code, 0, applied.output)
        self.assertEqual(applied.output.count('"readBackVerified": true'), 2)
        self.assertEqual(client.start_change_set.call_count, 2)

    def test_titles_command_lists_applications_over_the_limit(self):
        self.item["trademark"] = "Test Application Name"
        self.item["offerCodes"] = {"aws": ["code-a"]}
        self.write()
        with mock.patch.object(
            offer, "offer_variants_by_app",
            return_value={"odoo": {"aws": {"code-a": "Ubuntu 26.04; Enterprise"}}},
        ):
            result = self.runner.invoke(
                cli,
                [
                    "offer", "titles", "--cloud", "aws",
                    "--catalog", str(self.catalog_path),
                    "--versions", str(self.versions_path),
                    "--vendor", str(self.vendor_path),
                    "--json",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        document = json.loads(result.output)
        self.assertEqual(document["max"], 72)
        self.assertEqual(
            document["over"],
            [{"app": "odoo", "code": "code-a", "length": document["titles"][0]["length"]}],
        )
        self.assertGreater(document["titles"][0]["length"], 72)

    def test_titles_command_strict_fails_when_over_the_limit(self):
        self.item["trademark"] = "Test Application Name"
        self.item["offerCodes"] = {"aws": ["code-a"]}
        self.write()
        with mock.patch.object(
            offer, "offer_variants_by_app",
            return_value={"odoo": {"aws": {"code-a": "Ubuntu 26.04; Enterprise"}}},
        ):
            result = self.runner.invoke(
                cli,
                [
                    "offer", "titles", "--cloud", "aws",
                    "--catalog", str(self.catalog_path),
                    "--versions", str(self.versions_path),
                    "--vendor", str(self.vendor_path),
                    "--strict",
                ],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("exceed 72 characters", result.output)

    def test_build_writes_one_preview_per_offer_code_with_labels(self):
        self.item["offerCodes"] = {"aws": ["code-a", "code-b"]}
        self.write()
        with mock.patch.object(
            offer_commands, "labels_for",
            return_value={"code-a": "Ubuntu 26.04", "code-b": "Debian 13"},
        ):
            result = separated_runner().invoke(
                cli, self.args("build") + ["--cloud", "aws", "--output", "-"]
            )
        self.assertEqual(result.exit_code, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual([item["code"] for item in document["targets"]], ["code-a", "code-b"])
        self.assertEqual(
            [item["preview"]["content"]["ProductTitle"] for item in document["targets"]],
            [
                "Odoo on Ubuntu 26.04 - Preconfigured, Hardened, Ready in Minutes",
                "Odoo on Debian 13 - Preconfigured, Hardened, Ready in Minutes",
            ],
        )

    def test_build_rejects_unlisted_codes_without_the_flag(self):
        result = self.runner.invoke(
            cli,
            self.args("build") + ["--cloud", "aws", "--offer-codes", "other-code", "--output", "-"],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not recorded for 'odoo' on aws", result.output)

    def test_build_previews_unlisted_codes_when_forced(self):
        self.item["offerCodes"] = {"aws": []}
        self.write()
        with mock.patch.object(offer_commands, "labels_for", return_value={}):
            result = separated_runner().invoke(
                cli,
                self.args("build") + [
                    "--cloud", "aws", "--offer-codes", "other-code",
                    "--allow-unlisted-codes", "--output", "-",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["targets"][0]["code"], "other-code")
        self.assertEqual(document["targets"][0]["label"], "")
        self.assertEqual(
            document["targets"][0]["preview"]["content"]["ProductTitle"],
            self.render()["preview"]["content"]["ProductTitle"],
        )

    def test_build_previews_only_the_explicit_codes(self):
        self.item["offerCodes"] = {"aws": ["code-a", "code-b"]}
        self.write()
        with mock.patch.object(
            offer_commands, "labels_for", return_value={"code-b": "Debian 13"}
        ):
            result = separated_runner().invoke(
                cli,
                self.args("build") + [
                    "--cloud", "aws", "--offer-codes", "code-b", "--output", "-",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual([item["code"] for item in document["targets"]], ["code-b"])
        self.assertEqual(
            document["targets"][0]["preview"]["content"]["ProductTitle"],
            "Odoo on Debian 13 - Preconfigured, Hardened, Ready in Minutes",
        )

    def test_short_description_does_not_use_hasenterprise_wording(self):
        descriptions = set()
        for value in (False, True, None, "missing"):
            item = copy.deepcopy(self.item)
            if value == "missing":
                item.pop("hasenterprise", None)
            else:
                item["hasenterprise"] = value
            descriptions.add(offer.render(item, "20.0")["content"]["ShortDescription"])
        self.assertEqual(len(descriptions), 1)
        self.assertNotIn("Community Edition", descriptions.pop())

    def test_support_description_uses_line_separated_docs_email_and_sla(self):
        value = self.render()
        self.assertEqual(
            value["content"]["SupportDescription"],
            "Docs: https://support.websoft9.com/en/docs/odoo\n"
            "Email: help@websoft9.com\n"
            "SLA: 24-hour response on business days",
        )

    def test_publish_ready_does_not_require_separate_verification_records(self):
        self.set_entity_id()
        output = self.directory / "publish-ready.json"
        result = self.runner.invoke(
            cli,
            self.args("build") + ["--cloud", "aws", "--publish-ready", "--output", str(output)],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        preview = json.loads(output.read_text(encoding="utf-8"))
        self.assertNotIn("verification", preview)

    def test_build_writes_template_shaped_preview(self):
        output = self.directory / "aws" / "odoo.json"
        result = self.runner.invoke(cli, self.args("build") + ["--cloud", "aws", "--output", str(output)])
        self.assertEqual(result.exit_code, 0, result.output)
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(document["templateId"], "aws-ami-v1")
        self.assertEqual(document["target"]["entityType"], "AmiProduct@1.0")
        self.assertEqual(document["content"]["SearchKeywords"], self.item["keywords"])
        self.assertEqual(document["content"]["Highlights"], self.vendor_data["highlights"])
        self.assertNotIn("Categories", document["content"])
        self.assertNotIn("LogoUrl", document["content"])
        self.assertNotIn("VideoUrls", document["content"])
        self.assertNotIn("LongDescription", document["content"])
        self.assertEqual(document["AdditionalResources"], [
            {"Text": "Odoo Image Guide by Websoft9", "Url": "https://support.websoft9.com/en/docs/odoo"},
            {"Text": "Install Websoft9 on AWS", "Url": "https://support.websoft9.com/en/docs/install-aws"},
        ])
        self.assertEqual(
            [section["text"] for section in document["longDescriptionSections"] if section["text"].startswith("No separate license fee")],
            ["No separate license fee is charged for the application. The Websoft9 software fee covers the Console, operations toolset, and business-day support with a 24-hour response SLA. AWS infrastructure charges are billed separately by AWS."],
        )

    def test_long_description_headings_use_markdown_bold(self):
        value = self.render()
        self.assertIn("**What is included?**\n\n", value["content"]["LongDescription"])
        self.assertNotIn("**What is included?**\nOdoo", value["content"]["LongDescription"])
        headings = [
            section["heading"]
            for section in value["preview"]["longDescriptionSections"]
            if section["heading"]
        ]
        self.assertTrue(all("**" not in heading for heading in headings))

    def test_preview_only_sections_are_not_submitted(self):
        value = self.render()
        reference = {item["id"]: item for item in value["preview"]["previewOnlySections"]}
        self.assertEqual(set(reference), {"usage", "refunds"})
        self.assertIn("Launch the AMI", reference["usage"]["text"])
        self.assertIn("No refunds are issued", reference["refunds"]["text"])
        self.assertNotIn("Launch the AMI", str(value["content"]))
        self.assertNotIn("No refunds are issued", str(value["content"]))

    def test_template_fields_are_declared_in_aws_field_contract(self):
        template = offer.read_json(offer.DATA / "templates" / "aws-ami.json")
        fields = set(template["content"]) | {"LongDescription"}
        if template.get("AdditionalResources"):
            fields.add("AdditionalResources")
        self.assertLessEqual(fields, set(aws_marketplace.FIELD_SPECS))
        self.assertNotIn("Categories", fields)
        self.assertNotIn("LogoUrl", fields)
        self.assertNotIn("VideoUrls", fields)

    def test_build_infers_markdown_from_output_suffix(self):
        output = self.directory / "odoo-preview.md"
        result = self.runner.invoke(cli, self.args("build") + ["--cloud", "aws", "--output", str(output)])
        self.assertEqual(result.exit_code, 0, result.output)
        text = output.read_text(encoding="utf-8")
        self.assertIn("# aws-ami-v1", text)
        self.assertIn("Odoo on AWS - Preconfigured", text)
        self.assertIn("## Long description", text)
        self.assertIn("What you pay for", text)
        self.assertIn("## Reference sections (not submitted)", text)
        self.assertIn("### Refunds", text)

    def test_plan_writes_complete_plan_to_output(self):
        output = self.directory / "plan.json"
        current = self.directory / "current.json"
        self.set_entity_id()
        current.write_text(json.dumps(self.entity(self.render())), encoding="utf-8")
        result = self.runner.invoke(cli, self.args("plan") + [
            "--current", str(current), "--output", str(output),
        ])
        self.assertEqual(result.exit_code, 0, result.output)
        plan = json.loads(output.read_text(encoding="utf-8"))
        self.assertIn("planHash", plan)
        self.assertIn("request", plan)
        self.assertIn("diff", plan)

    def test_vendor_highlights_are_used_in_rendered_content(self):
        value = self.render()
        self.assertEqual(value["content"]["Highlights"], self.vendor_data["highlights"])

    def test_template_supplies_search_keywords_and_learning_resources(self):
        self.item["additionalResources"] = []
        self.write()
        value = self.render()
        self.assertEqual(value["content"]["SearchKeywords"], self.item["keywords"])
        self.assertEqual(value["content"]["AdditionalResources"], [
            {"Text": "Odoo Image Guide by Websoft9", "Url": "https://support.websoft9.com/en/docs/odoo"},
            {"Text": "Install Websoft9 on AWS", "Url": "https://support.websoft9.com/en/docs/install-aws"},
        ])

    def test_listing_additional_resources_do_not_override_template_defaults(self):
        value = self.render()
        self.assertEqual(value["content"]["AdditionalResources"], [
            {"Text": "Odoo Image Guide by Websoft9", "Url": "https://support.websoft9.com/en/docs/odoo"},
            {"Text": "Install Websoft9 on AWS", "Url": "https://support.websoft9.com/en/docs/install-aws"},
        ])

    def test_search_keywords_over_the_count_limit_keep_the_leading_entries(self):
        self.item["keywords"] = [f"keyword-{index}" for index in range(18)]
        self.write()
        value = self.render()
        self.assertEqual(value["content"]["SearchKeywords"], self.item["keywords"][:15])
        truncation = value["preview"]["keywordTruncation"]
        self.assertEqual(truncation["original"], 18)
        self.assertEqual(truncation["kept"], 15)
        self.assertEqual(truncation["dropped"], self.item["keywords"][15:])

    def test_search_keywords_over_the_combined_limit_keep_the_leading_entries(self):
        self.item["keywords"] = [str(index) + "x" * 49 for index in range(6)]
        self.write()
        value = self.render()
        self.assertEqual(value["content"]["SearchKeywords"], self.item["keywords"][:5])
        truncation = value["preview"]["keywordTruncation"]
        self.assertEqual(truncation["originalCharacters"], 300)
        self.assertEqual(truncation["combinedCharacters"], 250)

    def test_search_keywords_within_the_limits_are_not_truncated(self):
        self.item["keywords"] = ["erp", "crm"]
        self.write()
        value = self.render()
        self.assertEqual(value["content"]["SearchKeywords"], ["erp", "crm"])
        self.assertNotIn("keywordTruncation", value["preview"])

    def test_single_over_limit_keyword_still_fails_validation(self):
        self.item["keywords"] = ["x" * 51]
        self.write()
        with self.assertRaisesRegex(ValueError, "SearchKeywords"):
            self.render()

    def test_search_keywords_from_content_overrides_are_truncated(self):
        self.item["contentOverrides"] = {
            "SearchKeywords": [f"override-{index}" for index in range(17)]
        }
        self.write()
        value = self.render()
        self.assertEqual(len(value["content"]["SearchKeywords"]), 15)

    def test_plan_uses_truncated_search_keywords(self):
        self.set_entity_id()
        self.item["keywords"] = [f"keyword-{index}" for index in range(18)]
        self.write()
        value = self.render()
        entity = self.entity(value)
        entity["DetailsDocument"]["Description"]["SearchKeywords"] = self.item["keywords"]
        planned = offer.plan(value, entity)
        self.assertEqual(len(planned["diff"]["SearchKeywords"]["after"]), 15)
        self.assertEqual(
            planned["request"]["ChangeSet"][0]["DetailsDocument"]["SearchKeywords"],
            self.item["keywords"][:15],
        )

    def test_unknown_content_override_is_rejected(self):
        self.item["keywords"] = ["ERP"]
        self.item["contentOverrides"]["RefundPolicy"] = "foo"
        self.write()
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.render()

    def test_plan_noop_and_minimal_update_preserve_optional_fields(self):
        self.set_entity_id()
        value = self.render()
        entity = self.entity(value)
        entity["DetailsDocument"]["Description"]["Categories"] = ["Business Applications"]
        entity["DetailsDocument"]["PromotionalResources"]["LogoUrl"] = "https://example.com/old.png"
        self.assertIsNone(offer.plan(value, entity)["request"])
        entity["DetailsDocument"]["Description"]["ShortDescription"] = "Old description"
        planned = offer.plan(value, entity)
        change = planned["request"]["ChangeSet"][0]
        self.assertEqual(change["Entity"]["Identifier"], "prod-example12345@4")
        self.assertEqual(set(change["DetailsDocument"]), {"ShortDescription"})
        self.assertNotIn("UsageInstructions", json.dumps(planned))

    def test_snapshot_wrong_identity_is_rejected(self):
        self.set_entity_id()
        value = self.render()
        entity = self.entity(value)
        entity["EntityIdentifier"] = "prod-another@4"
        with self.assertRaisesRegex(ValueError, "does not match"):
            offer.plan(value, entity)

    def prepare_plan(self):
        self.set_entity_id()
        value = self.render()
        entity = self.entity(value)
        entity["DetailsDocument"]["Description"]["ProductTitle"] = "Old title"
        planned = offer.plan(value, entity)
        self.plan_path.write_text(json.dumps(planned), encoding="utf-8")
        return value, entity, planned

    def test_apply_submits_reviewed_delta_and_verifies_readback(self):
        value, entity, planned = self.prepare_plan()
        client = mock.Mock()
        client.describe_entity.side_effect = [entity, self.entity(value)]
        client.start_change_set.return_value = {"ChangeSetId": "cs-example"}
        client.describe_change_set.return_value = {"ChangeSetId": "cs-example", "Status": "SUCCEEDED"}
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(cli, self.args("apply") + ["--plan", str(self.plan_path), "--wait-seconds", "1"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('"readBackVerified": true', result.output)
        self.assertEqual(client.start_change_set.call_args.kwargs["ClientRequestToken"], planned["planHash"])
        self.assertEqual(client.start_change_set.call_args.kwargs["ChangeSet"], planned["request"]["ChangeSet"])

    def test_apply_rejects_modified_stale_and_changed_sources(self):
        for kind in ("modified", "revision", "source"):
            with self.subTest(kind=kind):
                _, entity, planned = self.prepare_plan()
                if kind == "modified":
                    planned["request"]["ChangeSet"][0]["ChangeType"] = "UpdateVisibility"
                    self.plan_path.write_text(json.dumps(planned), encoding="utf-8")
                elif kind == "revision":
                    entity["EntityIdentifier"] = "prod-example12345@5"
                else:
                    self.item["summary"] = "A different catalog claim."
                    self.write()
                client = mock.Mock()
                client.describe_entity.return_value = entity
                with mock.patch.object(offer, "aws_client", return_value=client):
                    result = self.runner.invoke(cli, self.args("apply") + ["--plan", str(self.plan_path)])
                self.assertNotEqual(result.exit_code, 0)
                client.start_change_set.assert_not_called()

    def test_missing_entity_apply_never_contacts_aws(self):
        self.plan_path.write_text("{}", encoding="utf-8")
        with mock.patch.object(offer, "aws_client") as client:
            result = self.runner.invoke(cli, self.args("apply") + ["--plan", str(self.plan_path)])
        self.assertNotEqual(result.exit_code, 0)
        client.assert_not_called()

    def test_sdk_errors_do_not_expose_sensitive_message(self):
        client = mock.Mock()
        error = Exception("secret-sensitive-response")
        error.response = {"Error": {"Code": "AccessDeniedException", "Message": "secret"}}
        client.describe_entity.side_effect = error
        with self.assertRaisesRegex(ValueError, "AccessDeniedException") as caught:
            offer.aws_call(client, "describe_entity", EntityId="prod-test")
        self.assertNotIn("secret", str(caught.exception))

    def test_apply_noop_does_not_submit(self):
        self.set_entity_id()
        value = self.render()
        entity = self.entity(value)
        self.plan_path.write_text(json.dumps(offer.plan(value, entity)), encoding="utf-8")
        client = mock.Mock()
        client.describe_entity.return_value = entity
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(cli, self.args("apply") + ["--plan", str(self.plan_path)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("NO_CHANGES", result.output)
        client.start_change_set.assert_not_called()

    def test_read_shape_normalizes_resources_and_legacy_details(self):
        self.set_entity_id()
        value = self.render()
        entity = self.entity(value)
        detail = entity.pop("DetailsDocument")
        detail["Description"].pop("AdditionalResources", None)
        detail["PromotionalResources"]["AdditionalResources"] = [
            {"Type": "Link", **item} for item in value["content"]["AdditionalResources"]
        ]
        entity["Details"] = json.dumps(detail)
        self.assertIsNone(offer.plan(value, entity)["request"])

    def test_resolve_entities_uses_a_known_map_without_listing(self):
        client = mock.Mock()
        client.describe_entity.return_value = {
            "DetailsDocument": {"Description": {"Sku": "code-a", "ProductCode": "pc-a"}}
        }
        with mock.patch.object(offer, "aws_client", return_value=client):
            matches = offer_commands._resolve_aws_entities(
                ["code-a"], None, known={"code-a": "prod-a"}
            )
        self.assertEqual(matches["code-a"][0]["entityId"], "prod-a")
        client.list_entities.assert_not_called()

    def test_resolve_entity_paginates_and_matches_offer_code(self):
        client = mock.Mock()
        client.list_entities.side_effect = [
            {"NextToken": "next", "EntitySummaryList": [{"EntityId": "prod-first", "AmiProductSummary": {"ProductTitle": "First", "Visibility": "Public"}, "private": "secret"}]},
            {"EntitySummaryList": [{"EntityId": "prod-second", "Name": "Second"}]},
        ]
        client.describe_entity.side_effect = lambda **kwargs: {
            "DetailsDocument": {
                "Description": {
                    "Sku": "sku-first" if kwargs["EntityId"] == "prod-first" else "sku-second",
                    "ProductCode": "code-second",
                    "ProductTitle": "Second",
                }
            }
        }
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(
                cli,
                ["offer", "resolve-entity", "--cloud", "aws", "--code", "sku-second"],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('"entityId": "prod-second"', result.output)
        self.assertIn('"sku": "sku-second"', result.output)
        self.assertNotIn("secret", result.output)
        self.assertEqual(client.list_entities.call_args_list[1].kwargs["NextToken"], "next")

    def test_resolve_entity_accepts_multiple_codes_in_one_scan(self):
        client = mock.Mock()
        client.list_entities.return_value = {
            "EntitySummaryList": [
                {"EntityId": "prod-first", "AmiProductSummary": {"ProductTitle": "First", "Visibility": "Public"}},
                {"EntityId": "prod-second", "AmiProductSummary": {"ProductTitle": "Second", "Visibility": "Public"}},
            ]
        }
        client.describe_entity.side_effect = lambda **kwargs: {
            "DetailsDocument": {
                "Description": {
                    "Sku": "sku-first" if kwargs["EntityId"] == "prod-first" else "sku-second",
                    "ProductCode": "code-first" if kwargs["EntityId"] == "prod-first" else "code-second",
                }
            }
        }
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(
                cli,
                [
                    "offer", "resolve-entity", "--cloud", "aws",
                    "--code", "sku-first", "--code", "code-second",
                ],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        document = json.loads(result.output)
        self.assertEqual([item["entityId"] for item in document["results"]], ["prod-first", "prod-second"])
        self.assertEqual(document["notFound"], [])
        self.assertEqual(client.list_entities.call_count, 1)

    def test_control_characters_in_list_and_signed_media_are_rejected(self):
        content = self.render()["content"]
        content["Highlights"] = ["bad\x01text"]
        with self.assertRaisesRegex(ValueError, "control characters"):
            offer.validate_content(content)
        content["Highlights"] = ["Valid"]
        content["LogoUrl"] = "https://example.com/image?X-Amz-Signature=secret"
        with self.assertRaisesRegex(ValueError, "unsigned HTTPS"):
            offer.validate_content(content)

    def test_terminal_failure_and_timeout(self):
        client = mock.Mock()
        client.describe_change_set.return_value = {"ChangeSetId": "cs-test", "Status": "FAILED", "ChangeSet": [{"ErrorDetailList": [{"ErrorCode": "INVALID_INPUT", "ErrorMessage": "secret"}]}]}
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(cli, ["offer", "status", "cs-test"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("INVALID_INPUT", result.output)
        self.assertNotIn("secret", result.output)
        client.describe_change_set.return_value = {"ChangeSetId": "cs-test", "Status": "APPLYING"}
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(cli, ["offer", "status", "cs-test"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("pollingTimedOut", result.output)


class PreflightAndMultiTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.app_catalog = self.directory / "product_en.json"
        self.offer_source = self.directory / "offer.json"
        self.catalog_path = self.directory / "catalog.json"
        self.versions_path = self.directory / "versions.json"
        self.vendor_path = self.directory / "vendor.json"
        self.plan_path = self.directory / "plan.json"
        self.app_catalog.write_text(
            json.dumps([{"key": "odoo"}, {"key": "wordpress"}]),
            encoding="utf-8",
        )
        self.offer_source.write_text(
            json.dumps([
                {"type": "app", "id": "odoo", "productID": {"aws": ["code-a", "code-b"]}},
                {"type": "app", "id": "wordpress", "productID": {"aws": []}},
            ]),
            encoding="utf-8",
        )
        self.item = odoo_item()
        labels_patch = mock.patch.object(offer_commands, "labels_for", return_value={})
        labels_patch.start()
        self.addCleanup(labels_patch.stop)
        self.item.update({
            "hasenterprise": False,
            "offerCodes": {"aws": ["code-a", "code-b"]},
            "targets": [
                {"code": "code-a", "entityId": "prod-a"},
                {"code": "code-b", "entityId": "prod-b"},
            ],
            "entityId": "prod-a",
        })
        self.vendor_data = offer.read_json(offer.DATA / "vendor.json")
        self.write()
        self.runner = CliRunner()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        self.catalog_path.write_text(json.dumps({"items": [self.item]}), encoding="utf-8")
        self.versions_path.write_text(json.dumps([{"id": "odoo", "version": "20.0"}]), encoding="utf-8")
        self.vendor_path.write_text(json.dumps(self.vendor_data), encoding="utf-8")

    def args(self, command):
        return [
            "offer", command, "--app", "odoo",
            "--catalog", str(self.catalog_path),
            "--versions", str(self.versions_path),
            "--vendor", str(self.vendor_path),
        ]

    def targets_args(self):
        return [
            "offer", "targets", "--app", "odoo", "--cloud", "aws",
            "--catalog", str(self.catalog_path),
        ]

    def render(self):
        return offer.render(self.item, "20.0", self.vendor_path)

    def entity(self, value, entity_id="prod-a", title=None):
        content = dict(value["content"])
        if title:
            content["ProductTitle"] = title
        return {
            "EntityIdentifier": f"{entity_id}@1",
            "EntityType": "AmiProduct@1.0",
            "DetailsDocument": aws_marketplace.to_read_document(content),
        }

    def plan_all(self):
        value = self.render()
        entities = [
            {"code": target["code"], "entityId": target["entityId"],
             "entity": self.entity(value, target["entityId"], "Old title")}
            for target in value["targets"]
        ]
        planned = offer.multi_plan(value, entities)
        self.plan_path.write_text(json.dumps(planned), encoding="utf-8")
        return value, planned

    def test_preflight_validates_ids_and_codes_without_aws(self):
        value = offer.preflight("odoo", "aws", catalog_source=str(self.app_catalog),
                                offer_source=str(self.offer_source))
        self.assertEqual(value["codes"], ["code-a", "code-b"])
        self.assertEqual(value["codeSource"], "catalog")
        explicit = offer.preflight("odoo", "aws", ["code-b"], str(self.app_catalog), str(self.offer_source))
        self.assertEqual(explicit["codes"], ["code-b"])
        self.assertEqual(explicit["codeSource"], "explicit")

    def test_preflight_suggests_case_and_close_matches(self):
        with self.assertRaisesRegex(ValueError, "use 'odoo'"):
            offer.preflight("Odoo", "aws", catalog_source=str(self.app_catalog),
                            offer_source=str(self.offer_source))
        with self.assertRaisesRegex(ValueError, "did you mean 'odoo'"):
            offer.preflight("odo", "aws", catalog_source=str(self.app_catalog),
                            offer_source=str(self.offer_source))
        with self.assertRaisesRegex(ValueError, "not in the application catalog"):
            offer.preflight("not-an-app", "aws", catalog_source=str(self.app_catalog),
                            offer_source=str(self.offer_source))

    def test_preflight_rejects_codes_from_other_applications(self):
        with self.assertRaisesRegex(ValueError, "do not belong to 'odoo'"):
            offer.preflight("odoo", "aws", ["other-code"], str(self.app_catalog), str(self.offer_source))
        with self.assertRaisesRegex(ValueError, "has no aws offer code"):
            offer.preflight("wordpress", "aws", catalog_source=str(self.app_catalog),
                            offer_source=str(self.offer_source))

    def test_preflight_allows_unlisted_codes_when_forced(self):
        value = offer.preflight(
            "odoo", "aws", ["code-a", "other-code"],
            str(self.app_catalog), str(self.offer_source), allow_unlisted=True,
        )
        self.assertEqual(value["codes"], ["code-a", "other-code"])
        self.assertEqual(value["codeSource"], "explicit")
        self.assertEqual(value["unlistedCodes"], ["other-code"])
        listed = offer.preflight(
            "odoo", "aws", ["code-a"], str(self.app_catalog), str(self.offer_source),
            allow_unlisted=True,
        )
        self.assertNotIn("unlistedCodes", listed)

    def test_preflight_allows_unlisted_codes_from_the_cli(self):
        output = self.directory / "forced.json"
        result = self.runner.invoke(cli, [
            "offer", "preflight", "--app", "odoo", "--cloud", "aws",
            "--catalog-source", str(self.app_catalog), "--offer-source", str(self.offer_source),
            "--offer-codes", "other-code", "--allow-unlisted-codes",
            "--output", str(output),
        ])
        self.assertEqual(result.exit_code, 0, result.output)
        value = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(value["codes"], ["other-code"])
        self.assertEqual(value["unlistedCodes"], ["other-code"])

    def test_preflight_command_fails_early_and_writes_targets(self):
        output = self.directory / "preflight.json"
        result = self.runner.invoke(cli, [
            "offer", "preflight", "--app", "odoo", "--cloud", "aws",
            "--catalog-source", str(self.app_catalog), "--offer-source", str(self.offer_source),
            "--output", str(output),
        ])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["codes"], ["code-a", "code-b"])
        early = self.runner.invoke(cli, [
            "offer", "preflight", "--app", "Odoo", "--cloud", "aws",
            "--catalog-source", str(self.app_catalog), "--offer-source", str(self.offer_source),
        ])
        self.assertNotEqual(early.exit_code, 0)
        self.assertIn("use 'odoo'", early.output)

    def test_targets_records_every_code_in_the_catalog(self):
        matches = {"code-a": [{"entityId": "prod-a"}], "code-b": [{"entityId": "prod-b"}]}
        with mock.patch("cli.commands.offer._resolve_aws_entities", return_value=matches):
            result = self.runner.invoke(cli, self.targets_args())
        self.assertEqual(result.exit_code, 0, result.output)
        document = json.loads(result.output)
        self.assertEqual(document["targets"], [
            {"code": "code-a", "entityId": "prod-a"},
            {"code": "code-b", "entityId": "prod-b"},
        ])
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(catalog["items"][0]["targets"], document["targets"])
        self.assertEqual(catalog["items"][0]["entityId"], "prod-a")

    def test_targets_collapses_codes_that_share_one_entity(self):
        matches = {"code-a": [{"entityId": "prod-x"}], "code-b": [{"entityId": "prod-x"}]}
        with mock.patch("cli.commands.offer._resolve_aws_entities", return_value=matches):
            result = self.runner.invoke(cli, self.targets_args())
        self.assertEqual(result.exit_code, 0, result.output)
        document = json.loads(result.output)
        self.assertEqual(document["targets"], [{"code": "code-a", "entityId": "prod-x"}])
        self.assertEqual(document["duplicates"], {"prod-x": ["code-a", "code-b"]})
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(catalog["items"][0]["targets"], document["targets"])

    def test_targets_writes_and_reuses_the_entity_map(self):
        entity_map = self.directory / "entity-map.json"
        matches = {"code-a": [{"entityId": "prod-a"}], "code-b": [{"entityId": "prod-b"}]}
        with mock.patch("cli.commands.offer._resolve_aws_entities", return_value=matches) as resolve:
            first = self.runner.invoke(
                cli, self.targets_args() + ["--entity-map", str(entity_map)]
            )
            self.assertEqual(first.exit_code, 0, first.output)
            cached = json.loads(entity_map.read_text(encoding="utf-8"))
            self.assertEqual(cached, {"code-a": "prod-a", "code-b": "prod-b"})
            second = self.runner.invoke(
                cli, self.targets_args() + ["--entity-map", str(entity_map)]
            )
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertEqual(resolve.call_args.kwargs["known"], cached)

    def test_targets_rejects_codes_that_do_not_belong_to_the_application(self):
        with mock.patch("cli.commands.offer._resolve_aws_entities") as resolve:
            result = self.runner.invoke(cli, self.targets_args() + ["--offer-codes", "other-code"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("do not belong to 'odoo'", result.output)
        resolve.assert_not_called()

    def test_targets_records_unlisted_codes_when_forced(self):
        matches = {"other-code": [{"entityId": "prod-other"}]}
        with mock.patch("cli.commands.offer._resolve_aws_entities", return_value=matches):
            result = self.runner.invoke(
                cli,
                self.targets_args() + ["--offer-codes", "other-code", "--allow-unlisted-codes"],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        document = json.loads(result.output)
        self.assertEqual(document["targets"], [{"code": "other-code", "entityId": "prod-other"}])
        self.assertEqual(document["unlistedCodes"], ["other-code"])
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(catalog["items"][0]["targets"], document["targets"])

    def test_targets_still_requires_forced_codes_to_resolve(self):
        with mock.patch("cli.commands.offer._resolve_aws_entities", return_value={}):
            result = self.runner.invoke(
                cli,
                self.targets_args() + ["--offer-codes", "other-code", "--allow-unlisted-codes"],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("no Catalog entity found for: other-code", result.output)

    def test_targets_rejects_ambiguous_codes_and_leaves_the_catalog_unchanged(self):
        self.item["entityId"] = "prod-initial"
        self.write()
        matches = {"code-a": [{"entityId": "prod-a"}, {"entityId": "prod-x"}], "code-b": [{"entityId": "prod-b"}]}
        with mock.patch("cli.commands.offer._resolve_aws_entities", return_value=matches):
            result = self.runner.invoke(cli, self.targets_args())
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("ambiguous", result.output)
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(catalog["items"][0]["entityId"], "prod-initial")

    def test_multi_plan_binds_each_entity_and_revision(self):
        value = self.render()
        entities = [
            {"code": target["code"], "entityId": target["entityId"],
             "entity": self.entity(value, target["entityId"], "Old title")}
            for target in value["targets"]
        ]
        planned = offer.multi_plan(value, entities)
        self.assertEqual([target["code"] for target in planned["targets"]], ["code-a", "code-b"])
        for target in planned["targets"]:
            change = target["plan"]["request"]["ChangeSet"][0]
            self.assertEqual(change["Entity"]["Identifier"], f"{target['entityId']}@1")
            self.assertIn("planHash", target["plan"])
        self.assertIn("planHash", planned)

    def test_multi_plan_rejects_duplicate_entities(self):
        value = self.render()
        entity = self.entity(value, "prod-x", "Old title")
        with self.assertRaisesRegex(ValueError, "duplicate Catalog entity"):
            offer.multi_plan(value, [
                {"code": "code-a", "entityId": "prod-x", "entity": entity},
                {"code": "code-b", "entityId": "prod-x", "entity": entity},
            ])

    def test_plan_requires_explicit_selection_for_multiple_targets(self):
        result = self.runner.invoke(cli, self.args("plan"))
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--all-codes", result.output)
        self.assertIn("--offer-code", result.output)

    def test_plan_command_plans_every_recorded_code(self):
        output = self.directory / "multi-plan.json"
        value = self.render()
        client = mock.Mock()
        client.describe_entity.side_effect = lambda **kwargs: self.entity(
            value, kwargs["EntityId"], "Old title"
        )
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(cli, self.args("plan") + [
                "--all-codes", "--output", str(output),
            ])
        self.assertEqual(result.exit_code, 0, result.output)
        planned = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(planned["targets"]), 2)
        self.assertEqual(planned["targets"][1]["entityId"], "prod-b")
        self.assertEqual(client.describe_entity.call_count, 2)

    def test_apply_command_multi_with_wait_keeps_stdout_a_single_json_document(self):
        _, planned = self.plan_all()
        value = self.render()
        client = mock.Mock()
        client.describe_entity.side_effect = [
            self.entity(value, target["entityId"], "Old title") for target in value["targets"]
        ] + [
            self.entity(value, target["entityId"]) for target in value["targets"]
        ]
        client.start_change_set.side_effect = [{"ChangeSetId": "cs-a"}, {"ChangeSetId": "cs-b"}]
        client.describe_change_set.return_value = {"Status": "SUCCEEDED"}
        runner = separated_runner()
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = runner.invoke(cli, self.args("apply") + ["--plan", str(self.plan_path), "--wait-seconds", "1"])
        self.assertEqual(result.exit_code, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["status"], "SUBMITTED")
        self.assertIn("readBackVerified", result.stderr)
        self.assertNotIn("readBackVerified", result.stdout)

    def test_apply_command_probes_the_reviewed_entity_when_waiting(self):
        value = self.render()
        entity_b = self.entity(value, "prod-b", "Old title")
        self.plan_path.write_text(
            json.dumps(offer.plan({**value, "entityId": "prod-b"}, entity_b)), encoding="utf-8"
        )
        client = mock.Mock()
        client.describe_entity.side_effect = [entity_b, self.entity(value, "prod-b")]
        client.start_change_set.return_value = {"ChangeSetId": "cs-b"}
        client.describe_change_set.return_value = {"Status": "SUCCEEDED"}
        runner = separated_runner()
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = runner.invoke(cli, self.args("apply") + ["--plan", str(self.plan_path), "--wait-seconds", "1"])
        self.assertEqual(result.exit_code, 0, result.stderr)
        probed = {call.kwargs["EntityId"] for call in client.describe_entity.call_args_list}
        self.assertEqual(probed, {"prod-b"})

    def test_apply_command_submits_each_target_with_its_own_plan_hash(self):
        _, planned = self.plan_all()
        value = self.render()
        client = mock.Mock()
        client.describe_entity.side_effect = lambda **kwargs: self.entity(
            value, kwargs["EntityId"], "Old title"
        )
        client.start_change_set.side_effect = [{"ChangeSetId": "cs-a"}, {"ChangeSetId": "cs-b"}]
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(cli, self.args("apply") + ["--plan", str(self.plan_path)])
        self.assertEqual(result.exit_code, 0, result.output)
        document = json.loads(result.output)
        self.assertEqual(document["status"], "SUBMITTED")
        self.assertEqual([target["changeSetId"] for target in document["targets"]], ["cs-a", "cs-b"])
        tokens = [call.kwargs["ClientRequestToken"] for call in client.start_change_set.call_args_list]
        self.assertEqual(tokens, [target["plan"]["planHash"] for target in planned["targets"]])

    def test_apply_command_rejects_stale_multi_plan_before_submitting(self):
        _, planned = self.plan_all()
        value = self.render()
        client = mock.Mock()
        client.describe_entity.side_effect = lambda **kwargs: self.entity(
            value, kwargs["EntityId"], "Old title"
        )
        client.describe_entity.side_effect = lambda **kwargs: (
            {**self.entity(value, kwargs["EntityId"], "Old title"), "EntityIdentifier": f"{kwargs['EntityId']}@2"}
            if kwargs["EntityId"] == "prod-b"
            else self.entity(value, kwargs["EntityId"], "Old title")
        )
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(cli, self.args("apply") + ["--plan", str(self.plan_path)])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("stale or modified", result.output)
        client.start_change_set.assert_not_called()

    def test_apply_command_reports_partial_progress(self):
        _, planned = self.plan_all()
        value = self.render()
        client = mock.Mock()
        client.describe_entity.side_effect = lambda **kwargs: self.entity(
            value, kwargs["EntityId"], "Old title"
        )
        error = Exception("denied")
        error.response = {"Error": {"Code": "AccessDeniedException"}}
        client.start_change_set.side_effect = [{"ChangeSetId": "cs-a"}, error]
        with mock.patch.object(offer, "aws_client", return_value=client):
            result = self.runner.invoke(cli, self.args("apply") + ["--plan", str(self.plan_path)])
        self.assertNotEqual(result.exit_code, 0)
        document = json.loads(result.output)
        self.assertEqual(document["status"], "PARTIAL")
        self.assertEqual([target["status"] for target in document["targets"]], ["SUBMITTED", "FAILED"])
        self.assertEqual(document["skipped"], [])
        self.assertNotIn("denied", result.output)


if __name__ == "__main__":
    unittest.main()
