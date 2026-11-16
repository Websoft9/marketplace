from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from cli import offer_codes, offer_generate, offer_resolve
from cli.cli import cli


def entry(app, aws=(), azure=()):
    result = {
        "id": app, "type": "app", "applications": [app],
        "productID": {p: [] for p in offer_codes.REQUIRED_PLATFORMS},
    }
    result["productID"].update(aws=list(aws), azure=list(azure))
    return result


class GenerateTests(unittest.TestCase):
    identities = {"odoo": ["Odoo"], "wordpress": ["WordPress"], "drupal": ["Drupal"]}

    def test_platform_replaces_only_aws_and_does_not_mutate_input(self):
        old = [entry("odoo", ["stale"], ["pub.odoo"])]
        before = copy.deepcopy(old)
        result, report = offer_generate.generate([
            offer_resolve.Product("new", "prod-new", "Odoo on AWS"),
        ], self.identities, old)
        self.assertEqual(old, before)
        self.assertEqual(result[0]["productID"]["aws"], ["new"])
        self.assertEqual(result[0]["productID"]["azure"], ["pub.odoo"])
        self.assertEqual(set(result[0]), {"id", "type", "applications", "productID"})
        self.assertEqual(report["unresolved"], [])
        self.assertEqual(offer_codes.verify([("matrix", result)])["errors"], 0)

    def test_one_code_moves_owner_without_losing_other_codes(self):
        old = [entry("odoo", ["move", "keep"])]
        result, _ = offer_generate.generate([
            offer_resolve.Product("move", "prod", "WordPress on AWS"),
            offer_resolve.Product("other", "prod-other", "Drupal on AWS"),
        ], self.identities, old, codes=["move"])
        by_id = {e["id"]: e for e in result}
        self.assertEqual(by_id["odoo"]["productID"]["aws"], ["keep"])
        self.assertEqual(by_id["wordpress"]["productID"]["aws"], ["move"])
        self.assertNotIn("drupal", by_id)

    def test_ambiguous_and_unknown_listings_are_skipped(self):
        for title in ("Odoo and Drupal", "Unidentified product"):
            with self.subTest(title=title):
                result, report = offer_generate.generate([
                    offer_resolve.Product("code", "prod", title),
                    offer_resolve.Product("good", "prod-good", "WordPress"),
                ], self.identities, [])
                self.assertEqual(result[0]["productID"]["aws"], ["good"])
                self.assertEqual(len(report["unresolved"]), 1)
                self.assertEqual(report["unresolved"][0]["entityId"], "prod")
                self.assertEqual(report["matched"], 1)

    def test_empty_inventory_cannot_clear_matrix(self):
        with self.assertRaisesRegex(ValueError, "empty AWS inventory"):
            offer_generate.generate([], self.identities, [entry("odoo", ["keep"])])

    def test_duplicate_cloud_codes_exclude_every_owner_regardless_of_order(self):
        for title in ("WordPress", "Odoo", "Unknown"):
            products = [
                offer_resolve.Product("same", "prod-a", "Odoo"),
                offer_resolve.Product("same", "prod-b", title),
                offer_resolve.Product("good", "prod-c", "Drupal"),
            ]
            for ordered in (products, list(reversed(products))):
                result, report = offer_generate.generate(ordered, self.identities, [])
                self.assertEqual(result, [entry("drupal", ["good"])])
                self.assertEqual(len(report["unresolved"]), 2)
                self.assertTrue(all(i["reason"] == "duplicate cloud code"
                                    for i in report["unresolved"]))

    def test_missing_selected_code_does_not_block_other_codes(self):
        result, report = offer_generate.generate([
            offer_resolve.Product("good", "prod", "Odoo"),
        ], self.identities, [], codes=["missing", "good"])
        self.assertEqual(result, [entry("odoo", ["good"])])
        self.assertEqual(report["unresolved"][0]["reason"], "selected code not found")

    def test_targeted_ambiguous_alias_is_skipped(self):
        products = [
            offer_resolve.Product("a", "prod-a", "Odoo", alternate_code="shared"),
            offer_resolve.Product("b", "prod-b", "Drupal", alternate_code="shared"),
            offer_resolve.Product("good", "prod-c", "WordPress"),
        ]
        old = [entry("odoo", ["shared", "a"])]
        result, report = offer_generate.generate(
            products, self.identities, old, codes=["shared", "good"],
        )
        self.assertEqual(result, [entry("odoo", ["a", "shared"]),
                                  entry("wordpress", ["good"])])
        self.assertEqual(report["matched"], 1)
        self.assertTrue(report["unresolved"])

    def test_full_run_removes_old_unresolved_mappings(self):
        result, report = offer_generate.generate([
            offer_resolve.Product("bad", "prod-a", "Unknown"),
            offer_resolve.Product("good", "prod-b", "Drupal"),
        ], self.identities, [entry("odoo", ["bad"])])
        self.assertEqual(result, [entry("drupal", ["good"]), entry("odoo")])
        self.assertEqual(len(report["unresolved"]), 1)

    def test_missing_code_is_reported_without_blocking_valid_product(self):
        result, report = offer_generate.generate([
            offer_resolve.Product("", "prod-a", "Odoo"),
            offer_resolve.Product("good", "prod-b", "Drupal"),
        ], self.identities, [])
        self.assertEqual(result, [entry("drupal", ["good"])])
        self.assertEqual(report["unresolved"][0]["reason"],
                         "missing product code (SKU aliases are not codes)")

    def test_same_sku_does_not_conflict_when_public_product_codes_differ(self):
        codes = ["qy0c6rqyf152lwjyxlpjf51q", "3z367nfu807mz1rxjd8mhsd64"]
        products = [offer_resolve._product(f"prod-{i}", {"DetailsDocument": {
            "Description": {"Sku": "odoo_ubuntu", "ProductCode": code,
                            "ProductTitle": "Odoo", "Visibility": "Public"},
        }}, {}) for i, code in enumerate(codes)]
        result, report = offer_generate.generate(products, self.identities, [])
        self.assertEqual(result, [entry("odoo", sorted(codes))])
        self.assertEqual(report["matched"], 2)
        self.assertEqual(report["unresolved"], [])

    def test_selected_product_code_removes_old_alias_mapping(self):
        p = offer_resolve.Product("product-code", "prod", "WordPress", alternate_code="sku")
        result, _ = offer_generate.generate(
            [p], self.identities, [entry("odoo", ["sku", "keep"])],
            codes=["sku"],
        )
        by_id = {e["id"]: e for e in result}
        self.assertEqual(by_id["odoo"]["productID"]["aws"], ["keep"])
        self.assertEqual(by_id["wordpress"]["productID"]["aws"], ["product-code"])


class GenerateCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "matrix.json"
        self.catalog = self.root / "products.json"
        self.catalog.write_text(json.dumps([
            {"key": "odoo", "trademark": "Odoo"},
            {"key": "drupal", "trademark": "Drupal"},
        ]))
        self.products = [offer_resolve.Product("new", "prod", "Odoo")]
        patcher = mock.patch.object(offer_resolve, "list_aws_products", side_effect=lambda *a, **kw: self.products)
        self.fetch = patcher.start()
        self.addCleanup(patcher.stop)
        self.runner = CliRunner(mix_stderr=False)

    def invoke(self, *args):
        return self.runner.invoke(cli, [
            "offer-codes", "gen", "--platform", "aws", "--output", str(self.path),
            "--product-catalog", str(self.catalog), *args,
        ])

    def test_first_generation_and_loader_compatibility(self):
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        docs = offer_codes.load_source(str(self.path))
        self.assertEqual(docs[0][1][0]["productID"]["aws"], ["new"])
        self.assertEqual(offer_codes.verify(docs)["errors"], 0)
        self.assertTrue(self.fetch.call_args.kwargs["public_only"])

    def test_regeneration_backups_are_not_loaded_as_json(self):
        self.assertEqual(self.invoke().exit_code, 0)
        self.assertEqual(self.invoke().exit_code, 0)
        self.assertTrue(list(self.root.glob(".*.bak")))
        # Test in a data-only directory (the catalog is not part of the source).
        data_files = list(self.root.glob("matrix*.json"))
        self.assertEqual(data_files, [self.path])

    def test_dry_run_writes_nothing(self):
        result = self.invoke("--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("productID", result.stdout)
        self.assertFalse(self.path.exists())

    def test_ambiguous_inventory_leaves_original_bytes(self):
        self.assertEqual(self.invoke().exit_code, 0)
        before = self.path.read_bytes()
        self.products = [offer_resolve.Product("new", "prod", "Odoo and Drupal")]
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("ambiguous application", result.stdout)
        self.assertEqual(self.path.read_bytes(), before)

    def test_partial_generation_writes_valid_entries_and_reports_problems(self):
        self.products += [
            offer_resolve.Product("bad", "prod-bad", "Odoo and Drupal"),
            offer_resolve.Product("duplicate", "prod-a", "Odoo"),
            offer_resolve.Product("duplicate", "prod-b", "Drupal"),
        ]
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(self.path.read_text()), [entry("odoo", ["new"])])
        self.assertIn("| Product code | Title | Reason |", result.stdout)
        self.assertIn("| bad | Odoo and Drupal | ambiguous application |", result.stdout)
        self.assertIn("| duplicate | Odoo | duplicate cloud code |", result.stdout)

    def test_json_result_is_parseable_with_progress_on_stderr(self):
        self.products += [offer_resolve.Product("bad", "prod-bad", "Unidentified product")]

        def fetch(*args, **kwargs):
            kwargs["progress"]("AWS inventory listed: 2 Public AMI products")
            return self.products

        self.fetch.side_effect = fetch
        result = self.invoke("--json")
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertTrue(report["written"])
        self.assertEqual(report["matched"], 1)
        self.assertEqual(report["unresolved"], [{
            "code": "bad", "title": "Unidentified product", "entityId": "prod-bad",
            "candidates": [], "reason": "no application match",
        }])
        self.assertIn("AWS inventory listed", result.stderr)

    def test_json_dry_run_embeds_diff_without_writing(self):
        result = self.invoke("--json", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertFalse(report["written"])
        self.assertIn("productID", report["diff"])
        self.assertFalse(self.path.exists())

    def test_json_all_unresolved_reports_details_and_nonzero_exit(self):
        self.products = [offer_resolve.Product("bad", "prod-bad", "Unknown")]
        result = self.invoke("--json")
        self.assertNotEqual(result.exit_code, 0)
        report = json.loads(result.stdout)
        self.assertFalse(report["written"])
        self.assertEqual(report["unresolved"][0]["code"], "bad")
        self.assertEqual(report["unresolved"][0]["title"], "Unknown")
        self.assertFalse(self.path.exists())

    def test_targeted_unresolved_mapping_is_preserved(self):
        self.path.write_text(json.dumps([entry("odoo", ["bad", "keep"])]))
        self.products += [offer_resolve.Product("bad", "prod-bad", "Unknown")]
        result = self.invoke("--code", "bad", "--code", "new")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(self.path.read_text()),
                         [entry("odoo", ["bad", "keep", "new"])])

    def test_unsupported_cloud_exits_before_fetching(self):
        result = self.invoke("--platform", "azure")
        self.assertNotEqual(result.exit_code, 0)
        self.fetch.assert_not_called()

    def test_stale_snapshot_is_not_overwritten(self):
        self.path.write_text("[]\n")
        before = self.path.read_bytes()
        self.path.write_text('[{"changed":true}]\n')
        with self.assertRaisesRegex(ValueError, "changed during generation"):
            offer_generate.write_matrix(self.path, [], before)
        self.assertIn("changed", self.path.read_text())

    def test_selected_code_updates_only_that_mapping(self):
        self.path.write_text(json.dumps([entry("odoo", ["old", "keep"])]))
        self.products = [offer_resolve.Product("old", "prod", "Drupal")]
        result = self.invoke("--code", "old")
        self.assertEqual(result.exit_code, 0, result.output)
        by_id = {e["id"]: e for e in json.loads(self.path.read_text())}
        self.assertEqual(by_id["odoo"]["productID"]["aws"], ["keep"])
        self.assertEqual(by_id["drupal"]["productID"]["aws"], ["old"])

    def test_api_error_does_not_write(self):
        self.fetch.side_effect = ValueError("AWS list_entities failed (AccessDenied)")
        result = self.invoke()
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
