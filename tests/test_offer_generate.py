from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from cli import alibabacloud_market, aliyun_market, aws_inventory, azure_market, offer_codes, offer_generate, offer_resolve, store
from cli.cli import cli
from tests.click_support import separated_runner


def entry(app, aws=(), azure=()):
    result = {
        "id": app, "type": "app", "applications": [app],
        "productID": {p: [] for p in offer_codes.REQUIRED_PLATFORMS},
    }
    result["productID"].update(aws=list(aws), azure=list(azure))
    return result


def public_products(products, visibility="Public"):
    return [replace(p, visibility=visibility) for p in products]


ALIBABA_PLATFORMS = (
    ("aliyun", aliyun_market, "list_aliyun_products", "cmjj"),
    ("alibabacloud", alibabacloud_market, "list_products", "sgcmjj"),
)


class GenerateTests(unittest.TestCase):
    identities = {"odoo": ["Odoo"], "wordpress": ["WordPress"], "drupal": ["Drupal"]}

    def test_both_azure_publishers_share_one_column_and_regenerate_independent_scopes(self):
        old = [entry("odoo", ["aws-keep"], ["websoft9inc.old", "vmlabinc1613642184700.old", "other.keep"])]
        before = copy.deepcopy(old)
        products = {platform: [offer_resolve.Product(publisher + ".odoo",
                      "product/00000000-0000-0000-0000-000000000001", "Odoo")]
                    for platform, publisher in (("azure", "websoft9inc"),
                                                ("azure-vmlab", "vmlabinc1613642184700"))}
        for order in (("azure", "azure-vmlab"), ("azure-vmlab", "azure")):
            result = old
            for platform in order:
                result, report = offer_generate.generate(products[platform], self.identities, result, platform=platform)
                self.assertEqual(report["platform"], platform)
            self.assertEqual(result[0]["productID"]["azure"],
                             ["other.keep", "vmlabinc1613642184700.odoo", "websoft9inc.odoo"])
            self.assertNotIn("azure-vmlab", result[0]["productID"])
            self.assertEqual(result[0]["productID"]["aws"], ["aws-keep"])
        self.assertEqual(old, before)

    def test_vmlab_targeted_generation_moves_only_selected_publisher_code(self):
        previous = [entry("odoo", azure=["vmlabinc1613642184700.move", "vmlabinc1613642184700.keep", "websoft9inc.keep"])]
        result, _ = offer_generate.generate([
            offer_resolve.Product("vmlabinc1613642184700.move", "product/00000000-0000-0000-0000-000000000001", "Drupal"),
        ], self.identities, previous, platform="azure-vmlab", codes=["vmlabinc1613642184700.move"])
        by_id = {r["id"]: r for r in result}
        self.assertEqual(by_id["odoo"]["productID"]["azure"], ["vmlabinc1613642184700.keep", "websoft9inc.keep"])
        self.assertEqual(by_id["drupal"]["productID"]["azure"], ["vmlabinc1613642184700.move"])

    def test_vmlab_generation_rejects_default_publisher_identity(self):
        with self.assertRaisesRegex(ValueError, "configured-publisher"):
            offer_generate.generate([offer_resolve.Product("websoft9inc.odoo",
                "product/00000000-0000-0000-0000-000000000001", "Odoo")],
                self.identities, [], platform="azure-vmlab")

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
        identities = {**self.identities, "odoo": ["Shared application"], "drupal": ["Shared application"]}
        for title in ("Shared application", "Unidentified product"):
            with self.subTest(title=title):
                result, report = offer_generate.generate([
                    offer_resolve.Product("code", "prod", title),
                    offer_resolve.Product("good", "prod-good", "WordPress"),
                ], identities, [])
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

    def test_aliyun_full_generation_preserves_other_clouds_and_metadata(self):
        old = entry("odoo", ["aws-keep"], ["pub.odoo"])
        old["productID"]["aliyun"] = ["old"]
        old["applications"] = ["odoo", "custom-alias"]
        old["type"] = "custom"
        before = copy.deepcopy(old)
        result, report = offer_generate.generate([
            offer_resolve.Product("cmjj001", "cmjj001", "Odoo 企业 ERP"),
            offer_resolve.Product("cmjj002", "cmjj002", "WordPress 建站平台"),
            offer_resolve.Product("cmjj003", "cmjj003", "Unidentified"),
        ], self.identities, [old], platform="aliyun")
        self.assertEqual(old, before)
        self.assertEqual(result[0]["productID"]["aliyun"], ["cmjj001"])
        self.assertEqual(result[0]["productID"]["aws"], ["aws-keep"])
        self.assertEqual(result[0]["productID"]["azure"], ["pub.odoo"])
        self.assertEqual(result[0]["applications"], ["odoo", "custom-alias"])
        self.assertEqual(result[0]["type"], "custom")
        self.assertEqual(result[1]["productID"]["aliyun"], ["cmjj002"])
        self.assertEqual(report["platform"], "aliyun")
        self.assertEqual(report["matched"], 2)
        self.assertEqual(report["unresolved"][0]["code"], "cmjj003")

    def test_aliyun_targeted_generation_moves_only_selected_code(self):
        old = entry("odoo", ["aws-keep"])
        old["productID"]["aliyun"] = ["cmjj001", "cmjj002"]
        result, _ = offer_generate.generate([
            offer_resolve.Product("cmjj001", "cmjj001", "WordPress"),
            offer_resolve.Product("cmjj002", "cmjj002", "Drupal"),
        ], self.identities, [old], platform="aliyun", codes=["cmjj001"])
        self.assertEqual(result[0]["productID"]["aliyun"], ["cmjj002"])
        self.assertEqual(result[0]["productID"]["aws"], ["aws-keep"])
        self.assertEqual(result[1]["productID"]["aliyun"], ["cmjj001"])

    def test_empty_aliyun_inventory_does_not_clear_existing_mappings(self):
        with self.assertRaisesRegex(ValueError, "empty Aliyun inventory"):
            offer_generate.generate([], self.identities, [], platform="aliyun")

    def test_every_selected_product_has_identity_evidence_and_matrix_decision(self):
        products = [
            offer_resolve.Product("a", "prod-a", "Odoo", "Drupal integration"),
            offer_resolve.Product("b", "prod-b", "Business suite", "Drupal Community, integrates with Odoo"),
            offer_resolve.Product("c", "prod-c", "Odoo and Drupal", "Odoo"),
            offer_resolve.Product("d", "prod-d", "Unknown", "Generic platform, integrates with Odoo"),
        ]
        _, report = offer_generate.generate(products, self.identities, [])
        rows = report["products"]
        self.assertEqual(len(rows), report["checked"])
        core = lambda row: {k: row["appMatch"][k] for k in ("status", "appId", "candidates", "method")}
        self.assertEqual(core(rows[0]), {
            "status": "matched", "appId": "odoo", "candidates": [], "method": "title-trademark",
        })
        self.assertEqual(rows[1]["appMatch"]["appId"], "drupal")
        self.assertEqual(rows[1]["appMatch"]["method"], "summary-lead-trademark")
        self.assertEqual(core(rows[2]), {
            "status": "matched", "appId": "odoo", "candidates": [],
            "method": "title-trademark",
        })
        self.assertEqual(core(rows[3]), {
            "status": "unmatched", "appId": None, "candidates": [], "method": None,
        })
        self.assertEqual([r["matrixDecision"]["included"] for r in rows], [True, True, True, False])
        self.assertEqual([e["appId"] for e in rows[2]["appMatch"]["evidence"]], ["odoo", "drupal"])

    def test_identity_match_does_not_mean_duplicate_or_missing_code_is_included(self):
        _, report = offer_generate.generate([
            offer_resolve.Product("same", "prod-a", "Odoo"),
            offer_resolve.Product("same", "prod-b", "Odoo"),
            offer_resolve.Product("", "prod-c", "Odoo"),
            offer_resolve.Product("valid", "prod-d", "Drupal"),
        ], self.identities, [])
        self.assertTrue(all(r["appMatch"]["status"] == "matched" for r in report["products"]))
        self.assertEqual([r["matrixDecision"]["included"] for r in report["products"]],
                         [False, False, False, True])
        self.assertEqual(report["products"][0]["matrixDecision"]["reason"], "duplicate cloud code")
        self.assertEqual(report["matched"], 1)

    def test_catalog_failure_marks_products_unverified_without_guessing_from_old_matrix(self):
        result, report = offer_generate.generate([
            offer_resolve.Product("old", "prod-a", "Odoo"),
        ], None, [entry("odoo", ["old"])])
        self.assertIsNone(result)
        self.assertEqual(report["products"][0]["appMatch"]["status"], "unverified")
        self.assertIsNone(report["products"][0]["appMatch"]["appId"])
        self.assertFalse(report["products"][0]["matrixDecision"]["included"])


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
        patcher = mock.patch.object(aws_inventory, "list_aws_products",
                                    side_effect=lambda *a, **kw: public_products(self.products))
        self.fetch = patcher.start()
        self.addCleanup(patcher.stop)
        self.runner = separated_runner()

    def invoke(self, *args):
        return self.runner.invoke(cli, [
            "offer-codes", "gen", "--platform", "aws", "--output", str(self.path),
            "--product-catalog", str(self.catalog), *args,
        ])

    def snapshot(self, products=None, platform="aws"):
        with mock.patch.object(store, "collect_products", return_value=public_products(
            self.products if products is None else products,
            "Public" if platform == "aws" else "StoreVisible",
        )):
            document = store.query_products([platform])
        path = self.root / "inventory.json"
        path.write_text(json.dumps(document))
        return path, document

    def test_first_generation_and_loader_compatibility(self):
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        docs = offer_codes.load_source(str(self.path))
        self.assertEqual(docs[0][1][0]["productID"]["aws"], ["new"])
        self.assertEqual(offer_codes.verify(docs)["errors"], 0)
        self.assertTrue(self.fetch.call_args.kwargs["public_only"])
        self.assertIn("| new | Odoo | matched | odoo | yes |", result.stdout)

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
        self.catalog.write_text(json.dumps([
            {"key": "odoo", "trademark": "Shared application"},
            {"key": "drupal", "trademark": "Shared application"},
        ]))
        self.products = [offer_resolve.Product("new", "prod", "Shared application")]
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
        self.assertEqual(json.loads(self.path.read_text()), [entry("odoo", ["bad", "new"])])
        self.assertIn("| Product code | Title | Reason |", result.stdout)
        self.assertIn("| bad | Odoo and Drupal | matched | odoo | yes |", result.stdout)
        self.assertIn("| duplicate | Odoo | duplicate cloud code |", result.stdout)

    def test_json_result_is_parseable_with_progress_on_stderr(self):
        self.products += [offer_resolve.Product("bad", "prod-bad", "Unidentified product")]

        def fetch(*args, **kwargs):
            kwargs["progress"]("AWS inventory listed: 2 Public AMI products")
            return public_products(self.products)

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
        result = self.invoke("--platform", "googlecloud")
        self.assertNotEqual(result.exit_code, 0)
        self.fetch.assert_not_called()

    def test_azure_live_and_snapshot_generation_preserve_vmlab_and_other_clouds(self):
        p = offer_resolve.Product("websoft9inc.odoo", "product/00000000-0000-0000-0000-000000000001",
                                  "Odoo", "Live description", "StoreVisible")
        old = [entry("odoo", ["aws-keep"], ["websoft9inc.stale", "vmlabinc1613642184700.odoo"])]
        self.path.write_text(json.dumps(old))
        with mock.patch.object(azure_market, "list_products", return_value=[p]):
            result = self.invoke("--platform", "azure", "--json", "--dry-run")
            inventory = store.query_products(["azure"])
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertEqual(report["products"][0]["entityId"], p.entity_id)
        self.assertEqual(report["products"][0]["shortDescription"], p.summary)
        self.assertFalse(report["written"])
        self.assertEqual(json.loads(self.path.read_text()), old)
        snapshot = self.root / "azure-inventory.json"
        snapshot.write_text(json.dumps(inventory))
        with mock.patch.object(azure_market, "list_products", side_effect=AssertionError("offline")):
            result = self.invoke("--platform", "azure", "--inventory", str(snapshot), "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(self.path.read_text())
        self.assertEqual(data[0]["productID"]["azure"], ["vmlabinc1613642184700.odoo", "websoft9inc.odoo"])
        self.assertEqual(data[0]["productID"]["aws"], ["aws-keep"])
        self.assertEqual(offer_codes.verify([("matrix", data)])["errors"], 0)

    def test_azure_targeted_generation_moves_only_selected_code(self):
        old = [entry("odoo", azure=["websoft9inc.move", "websoft9inc.keep", "vmlabinc1613642184700.odoo"])]
        self.path.write_text(json.dumps(old))
        p = offer_resolve.Product("websoft9inc.move", "product/00000000-0000-0000-0000-000000000001",
                                  "Drupal", visibility="StoreVisible")
        with mock.patch.object(azure_market, "list_products", return_value=[p]):
            result = self.invoke("--platform", "azure", "--code", p.code, "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        data = {r["id"]: r for r in json.loads(self.path.read_text())}
        self.assertEqual(data["odoo"]["productID"]["azure"], ["vmlabinc1613642184700.odoo", "websoft9inc.keep"])
        self.assertEqual(data["drupal"]["productID"]["azure"], [p.code])

    def test_azure_empty_failed_or_unmatched_inventory_cannot_overwrite(self):
        old = json.dumps([entry("odoo", azure=["websoft9inc.keep"])])
        self.path.write_text(old)
        for products in ([], [offer_resolve.Product(
                "websoft9inc.unknown", "product/00000000-0000-0000-0000-000000000001",
                "Unknown", visibility="StoreVisible")]):
            with mock.patch.object(azure_market, "list_products", return_value=products):
                result = self.invoke("--platform", "azure", "--json")
            self.assertNotEqual(result.exit_code, 0)
            self.assertFalse(json.loads(result.stdout)["written"])
            self.assertEqual(self.path.read_text(), old)
        with mock.patch.object(azure_market, "list_products", side_effect=store.InventoryError("HTTP 403")):
            result = self.invoke("--platform", "azure", "--json")
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(json.loads(result.stdout)["inventory"]["complete"])
        self.assertEqual(self.path.read_text(), old)

    def test_azure_rejects_profile_before_network(self):
        with mock.patch.object(azure_market, "list_products") as fetch:
            result = self.invoke("--platform", "azure", "--profile", "aws-seller")
        self.assertNotEqual(result.exit_code, 0)
        fetch.assert_not_called()

    def test_vmlab_snapshot_cli_preserves_default_azure_and_uses_shared_matrix_column(self):
        products = [offer_resolve.Product("vmlabinc1613642184700.odoo",
                    "product/00000000-0000-0000-0000-000000000001", "Odoo", "VMLab summary", "StoreVisible")]
        path, _ = self.snapshot(products, platform="azure-vmlab")
        self.path.write_text(json.dumps([entry("odoo", ["aws-keep"], ["websoft9inc.keep", "vmlabinc1613642184700.stale"])]))
        with mock.patch.object(azure_market, "list_products", side_effect=AssertionError("offline")):
            result = self.invoke("--platform", "azure-vmlab", "--inventory", str(path), "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertEqual(report["platform"], "azure-vmlab")
        self.assertEqual(report["products"][0]["platform"], "azure-vmlab")
        self.assertEqual(report["inventory"]["store"]["id"], "vmlabinc1613642184700")
        matrix = json.loads(self.path.read_text())
        self.assertEqual(matrix[0]["productID"]["azure"], ["vmlabinc1613642184700.odoo", "websoft9inc.keep"])
        self.assertNotIn("azure-vmlab", matrix[0]["productID"])

    def test_vmlab_failed_inventory_cannot_modify_either_publisher(self):
        old = json.dumps([entry("odoo", azure=["websoft9inc.keep", "vmlabinc1613642184700.keep"])])
        self.path.write_text(old)
        with mock.patch.object(azure_market, "list_products", side_effect=store.InventoryError("HTTP 403")) as fetch:
            result = self.invoke("--platform", "azure-vmlab", "--json")
        self.assertNotEqual(result.exit_code, 0)
        fetch.assert_called_once_with(platform="azure-vmlab", progress=mock.ANY)
        self.assertFalse(json.loads(result.stdout)["written"])
        self.assertEqual(self.path.read_text(), old)

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

    def test_aliyun_cli_writes_only_aliyun_and_reports_unresolved(self):
        old = entry("odoo", ["aws-keep"])
        old["productID"]["aliyun"] = ["old"]
        self.path.write_text(json.dumps([old]))
        products = [
            offer_resolve.Product("cmjj001", "cmjj001", "Odoo 企业 ERP"),
            offer_resolve.Product("cmjj002", "cmjj002", "Unknown"),
        ]
        with mock.patch.object(aliyun_market, "list_aliyun_products",
                               return_value=public_products(products, "StoreVisible")):
            result = self.invoke("--platform", "aliyun", "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        self.fetch.assert_not_called()
        data = json.loads(self.path.read_text())
        self.assertEqual(data[0]["productID"]["aws"], ["aws-keep"])
        self.assertEqual(data[0]["productID"]["aliyun"], ["cmjj001"])
        report = json.loads(result.stdout)
        self.assertEqual(report["platform"], "aliyun")
        self.assertEqual(report["visibility"], "StoreVisible")
        self.assertEqual(report["unresolved"][0]["code"], "cmjj002")
        self.assertEqual(report["unresolved"][0]["title"], "Unknown")

    def test_aliyun_cli_dry_run_does_not_write_and_outputs_json(self):
        with mock.patch.object(aliyun_market, "list_aliyun_products",
                               return_value=public_products(self.products, "StoreVisible")):
            result = self.invoke("--platform", "aliyun", "--json", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("productID", json.loads(result.stdout)["diff"])
        self.assertFalse(self.path.exists())

    def test_aliyun_api_or_credential_failure_does_not_change_matrix(self):
        self.path.write_text("[]\n")
        for error in (ValueError("API inventory incomplete"), RuntimeError("credentials missing")):
            with mock.patch.object(aliyun_market, "list_aliyun_products", side_effect=error):
                result = self.invoke("--platform", "aliyun", "--json")
            self.assertNotEqual(result.exit_code, 0)
            report = json.loads(result.stdout)
            self.assertFalse(report["inventory"]["complete"])
            self.assertFalse(report["matching"]["complete"])
            self.assertFalse(report["written"])
            self.assertEqual(self.path.read_text(), "[]\n")

    def test_aliyun_rejects_aws_profile_before_network(self):
        with mock.patch.object(aliyun_market, "list_aliyun_products") as fetch:
            result = self.invoke("--platform", "aliyun", "--profile", "aws-profile")
        self.assertNotEqual(result.exit_code, 0)
        fetch.assert_not_called()

    def test_snapshot_reuses_exact_store_facts_without_cloud_calls(self):
        path, document = self.snapshot([
            offer_resolve.Product("new", "prod", "Odoo", "Product summary"),
            offer_resolve.Product("unknown", "prod-other", "Unknown", "Another summary"),
        ])
        original = path.read_bytes()
        with mock.patch.object(store, "query_platform", side_effect=AssertionError("no cloud calls")):
            result = self.invoke("--inventory", str(path), "--json", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.fetch.assert_not_called()
        report = json.loads(result.stdout)
        self.assertEqual(report["inventory"]["source"], "snapshot")
        self.assertEqual(report["inventory"]["queriedAt"], document["results"][0]["queriedAt"])
        self.assertEqual(report["inventory"]["sourcePath"], str(path))
        self.assertTrue(report["matching"]["complete"])
        self.assertEqual(len(report["matching"]["catalogIdentitySha256"]), 64)
        self.assertEqual(report["matching"]["rulesVersion"], "deterministic-first-appearance-v2")
        self.assertEqual(report["matching"]["catalog"], str(self.catalog))
        self.assertEqual([r["appMatch"]["status"] for r in report["products"]], ["matched", "unmatched"])
        for facts, row in zip(document["results"][0]["products"], report["products"]):
            for key, value in facts.items():
                self.assertEqual(row[key], value)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(self.path.exists())

    def test_live_query_uses_the_same_normalized_fact_result_as_store_command(self):
        path, document = self.snapshot()
        facts = document["results"][0]
        with mock.patch.object(store, "query_platform", return_value=facts) as query:
            result = self.invoke("--json", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        query.assert_called_once()
        self.fetch.assert_not_called()
        report = json.loads(result.stdout)
        self.assertEqual(report["products"][0]["code"], facts["products"][0]["code"])
        self.assertEqual(report["inventory"]["source"], "live")
        self.assertIsNone(report["inventory"]["sourcePath"])
        self.assertEqual(report["inventory"]["count"], 1)

    def test_snapshot_preserves_sku_lookup_but_only_writes_real_product_code(self):
        path, _ = self.snapshot([
            offer_resolve.Product("real-code", "prod", "Drupal", alternate_code="odoo_ubuntu"),
        ])
        self.path.write_text(json.dumps([entry("odoo", ["odoo_ubuntu", "keep"])]))
        result = self.invoke("--inventory", str(path), "--code", "odoo_ubuntu", "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        data = {r["id"]: r for r in json.loads(self.path.read_text())}
        self.assertEqual(data["odoo"]["productID"]["aws"], ["keep"])
        self.assertEqual(data["drupal"]["productID"]["aws"], ["real-code"])
        row = json.loads(result.stdout)["products"][0]
        self.assertEqual(row["sellerSku"], "odoo_ubuntu")
        self.assertEqual(row["code"], "real-code")

    def test_selected_complete_platform_can_be_used_if_another_snapshot_platform_failed(self):
        path, document = self.snapshot()
        document["complete"] = False
        document["results"].append({"platform": "aliyun", "complete": False, "errors": [{}]})
        path.write_text(json.dumps(document))
        result = self.invoke("--inventory", str(path), "--json", "--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(json.loads(result.stdout)["inventory"]["complete"])

    def test_bad_inventory_snapshots_never_change_matrix_or_query_cloud(self):
        path, document = self.snapshot()
        cases = []
        for mutate in (
            lambda d: d.update(schemaVersion=2),
            lambda d: d.update(schemaVersion=True),
            lambda d: d.update(results=[]),
            lambda d: d["results"].append(copy.deepcopy(d["results"][0])),
            lambda d: d["results"][0].update(complete=False),
            lambda d: d["results"][0].update(errors=[{"code": "Error"}]),
            lambda d: d["results"][0].update(count=3),
            lambda d: d["results"][0].update(queriedAt="2026-11-17"),
            lambda d: d["results"][0].update(scope={"productType": "image", "visibility": "some"}),
            lambda d: d["results"][0]["store"].update(id="wrong-store"),
            lambda d: d["results"][0]["products"][0].update(public=False),
            lambda d: d["results"][0]["products"][0].update(visibility="Restricted"),
            lambda d: d["results"][0]["products"][0].update(platform="aliyun"),
            lambda d: d["results"][0]["products"][0].update(sellerSku=False),
            lambda d: d["results"][0]["products"][0].update(title=None),
        ):
            bad = copy.deepcopy(document)
            mutate(bad)
            cases.append(bad)
        self.path.write_text("[]\n")
        for bad in cases:
            with self.subTest(bad=bad):
                path.write_text(json.dumps(bad))
                result = self.invoke("--inventory", str(path), "--json")
                self.assertNotEqual(result.exit_code, 0)
                self.assertEqual(self.path.read_text(), "[]\n")
        self.fetch.assert_not_called()

    def test_catalog_failure_reports_unverified_products_and_preserves_original_matrix(self):
        path, _ = self.snapshot()
        self.path.write_text("[]\n")
        with mock.patch.object(offer_resolve, "load_identities", side_effect=RuntimeError("SECRET signed request")):
            result = self.invoke("--inventory", str(path), "--json")
        self.assertNotEqual(result.exit_code, 0)
        report = json.loads(result.stdout)
        self.assertTrue(report["inventory"]["complete"])
        self.assertFalse(report["matching"]["complete"])
        self.assertEqual(report["products"][0]["appMatch"]["status"], "unverified")
        self.assertFalse(report["written"])
        self.assertNotIn("SECRET", result.stdout + result.stderr)
        self.assertEqual(self.path.read_text(), "[]\n")

    def test_matching_hash_changes_with_effective_catalog_rules(self):
        path, _ = self.snapshot()
        first = json.loads(self.invoke("--inventory", str(path), "--json", "--dry-run").stdout)
        self.catalog.write_text(json.dumps([
            {"key": "odoo", "trademark": "Odoo"}, {"key": "drupal", "trademark": "Drupal CMS"},
        ]))
        second = json.loads(self.invoke("--inventory", str(path), "--json", "--dry-run").stdout)
        self.assertNotEqual(first["matching"]["catalogIdentitySha256"], second["matching"]["catalogIdentitySha256"])

    def test_missing_ffmpeg_trademark_is_retained_and_warned_in_generation(self):
        self.catalog.write_text(json.dumps([
            {"key": "odoo", "trademark": "Odoo"}, {"key": "ffmpeg", "trademark": None},
        ]))
        path, _ = self.snapshot([
            offer_resolve.Product("odoo-code", "prod-odoo", "Odoo"),
            offer_resolve.Product("ffmpeg-code", "prod-ffmpeg", "FFmpeg for Odoo"),
        ])
        result = self.invoke("--inventory", str(path), "--dry-run", "--json")
        self.assertEqual(result.exit_code, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["matched"], 2)
        self.assertEqual(report["products"][1]["appMatch"]["appId"], "ffmpeg")
        self.assertEqual(report["products"][1]["appMatch"]["method"], "title-app-id")
        self.assertTrue(report["matching"]["complete"])
        self.assertTrue(report["matching"]["degraded"])
        self.assertEqual(report["matching"]["warnings"][0]["appId"], "ffmpeg")
        self.assertIn("WARNING identity catalog [ffmpeg]", result.stderr)
        self.assertFalse(self.path.exists())

    def test_unsafe_id_fallback_still_prevents_matrix_write_and_explains_why(self):
        self.catalog.write_text(json.dumps([
            {"key": "odoo", "trademark": "Odoo"}, {"key": "php", "trademark": None},
        ]))
        path, _ = self.snapshot()
        self.path.write_text("[]\n")
        result = self.invoke("--inventory", str(path), "--json")
        self.assertNotEqual(result.exit_code, 0)
        report = json.loads(result.stdout)
        self.assertFalse(report["written"])
        self.assertEqual(report["products"][0]["appMatch"]["status"], "unverified")
        self.assertIn("unsafe for automatic fallback", report["matching"]["errors"][0]["message"])
        self.assertEqual(self.path.read_text(), "[]\n")

    def test_snapshot_rejects_profile_and_invalid_json_before_cloud_calls(self):
        path, _ = self.snapshot()
        result = self.invoke("--inventory", str(path), "--profile", "seller")
        self.assertEqual(result.exit_code, 2)
        path.write_text("not JSON")
        result = self.invoke("--inventory", str(path), "--json")
        self.assertNotEqual(result.exit_code, 0)
        self.fetch.assert_not_called()

    def test_catalog_provenance_does_not_expose_signed_query_or_url_credentials(self):
        path, _ = self.snapshot()
        source = "https://user:password@example.com/catalog.json?token=SECRET#fragment"
        with mock.patch.object(offer_resolve, "load_identities", return_value={"odoo": ["Odoo"]}):
            result = self.invoke("--inventory", str(path), "--product-catalog", source, "--dry-run", "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.stdout)["matching"]["catalog"], "https://example.com/catalog.json")
        self.assertNotIn("SECRET", result.stdout)
        self.assertNotIn("password", result.stdout)

    def test_both_alibaba_platforms_share_live_matching_report_and_isolated_write(self):
        for platform, adapter, method, prefix in ALIBABA_PLATFORMS:
            with self.subTest(platform=platform):
                old = entry("odoo", ["aws-keep"], ["pub.odoo"])
                old["productID"].update(aliyun=["cmjj999"], alibabacloud=["sgcmjj999"])
                old["applications"] = ["odoo", "custom-alias"]
                self.path.write_text(json.dumps([old]))
                products = public_products([
                    offer_resolve.Product(prefix + "001", prefix + "001", "Odoo", "API summary"),
                    offer_resolve.Product(prefix + "002", prefix + "002", "Unidentified runtime"),
                    offer_resolve.Product(prefix + "003", prefix + "003", "Odoo and Drupal"),
                ], "StoreVisible")
                with mock.patch.object(adapter, method, return_value=products) as fetch, \
                        mock.patch.object(store, "query_platform", wraps=store.query_platform) as query:
                    result = self.invoke("--platform", platform, "--json")
                self.assertEqual(result.exit_code, 0, result.output)
                fetch.assert_called_once()
                query.assert_called_once()
                self.assertEqual(query.call_args.args[0], platform)
                data = json.loads(self.path.read_text())
                expected = copy.deepcopy(old)
                expected["productID"][platform] = [prefix + "001", prefix + "003"]
                self.assertEqual(data, [expected])
                report = json.loads(result.stdout)
                self.assertEqual(report["platform"], platform)
                self.assertEqual(report["inventory"]["source"], "live")
                self.assertEqual(report["inventory"]["store"]["id"],
                                 "228399" if platform == "aliyun" else "2116499")
                self.assertTrue(report["matching"]["complete"])
                self.assertEqual([p["appMatch"]["status"] for p in report["products"]],
                                 ["matched", "unmatched", "matched"])
                self.assertEqual(report["products"][0]["shortDescription"], "API summary")
                self.assertEqual(report["products"][2]["appMatch"]["appId"], "odoo")
                self.assertEqual([e["appId"] for e in report["products"][2]["appMatch"]["evidence"]],
                                 ["odoo", "drupal"])
                self.assertEqual(report["matched"], 2)
                self.assertEqual(len(report["unresolved"]), 1)
                self.assertTrue(report["written"])
        self.fetch.assert_not_called()

    def test_both_alibaba_platforms_consume_store_snapshots_without_requerying(self):
        for platform, _, _, prefix in ALIBABA_PLATFORMS:
            with self.subTest(platform=platform):
                path, document = self.snapshot([
                    offer_resolve.Product(prefix + "001", prefix + "001", "Odoo", "Original summary"),
                ], platform=platform)
                snapshot_bytes = path.read_bytes()
                self.path.write_text("[]\n")
                with mock.patch.object(store, "query_platform", side_effect=AssertionError("no cloud calls")):
                    result = self.invoke("--platform", platform, "--inventory", str(path), "--dry-run", "--json")
                self.assertEqual(result.exit_code, 0, result.output)
                report = json.loads(result.stdout)
                self.assertEqual(report["inventory"]["source"], "snapshot")
                self.assertEqual(report["inventory"]["queriedAt"], document["results"][0]["queriedAt"])
                for key, value in document["results"][0]["products"][0].items():
                    self.assertEqual(report["products"][0][key], value)
                self.assertEqual(report["products"][0]["appMatch"]["appId"], "odoo")
                self.assertFalse(report["written"])
                self.assertIn(f'"{platform}"', report["diff"])
                self.assertEqual(self.path.read_text(), "[]\n")
                self.assertEqual(path.read_bytes(), snapshot_bytes)

    def test_both_alibaba_platforms_target_only_requested_codes(self):
        for platform, _, _, prefix in ALIBABA_PLATFORMS:
            with self.subTest(platform=platform):
                path, _ = self.snapshot([
                    offer_resolve.Product(prefix + "001", prefix + "001", "Drupal"),
                    offer_resolve.Product(prefix + "002", prefix + "002", "Odoo"),
                ], platform=platform)
                old = entry("odoo", ["aws-keep"])
                old["productID"].update(aliyun=["cmjj001", "cmjj002"], alibabacloud=["sgcmjj001", "sgcmjj002"])
                self.path.write_text(json.dumps([old]))
                result = self.invoke("--platform", platform, "--inventory", str(path),
                                     "--code", prefix + "001", "--json")
                self.assertEqual(result.exit_code, 0, result.output)
                data = {e["id"]: e for e in json.loads(self.path.read_text())}
                self.assertEqual(data["odoo"]["productID"][platform], [prefix + "002"])
                self.assertEqual(data["drupal"]["productID"][platform], [prefix + "001"])
                for other in offer_codes.REQUIRED_PLATFORMS:
                    if other != platform:
                        self.assertEqual(data["odoo"]["productID"][other], old["productID"][other])
                self.assertEqual(json.loads(result.stdout)["checked"], 1)

    def test_both_alibaba_platforms_preserve_matrix_on_empty_failed_or_unverified_inventory(self):
        for platform, adapter, method, prefix in ALIBABA_PLATFORMS:
            with self.subTest(platform=platform):
                self.path.write_text("[]\n")
                with mock.patch.object(adapter, method, return_value=[]):
                    result = self.invoke("--platform", platform, "--json")
                self.assertNotEqual(result.exit_code, 0)
                report = json.loads(result.stdout)
                self.assertIn(f"empty {offer_generate.PLATFORMS[platform]} inventory", report["errors"][0]["message"])
                with mock.patch.object(adapter, method, side_effect=RuntimeError("SECRET signed request")):
                    result = self.invoke("--platform", platform, "--json")
                self.assertNotEqual(result.exit_code, 0)
                self.assertFalse(json.loads(result.stdout)["inventory"]["complete"])
                self.assertNotIn("SECRET", result.stdout + result.stderr)
                path, _ = self.snapshot([
                    offer_resolve.Product(prefix + "001", prefix + "001", "Odoo"),
                ], platform=platform)
                with mock.patch.object(offer_resolve, "load_identities", side_effect=ValueError("bad catalog")):
                    result = self.invoke("--platform", platform, "--inventory", str(path), "--json")
                self.assertNotEqual(result.exit_code, 0)
                self.assertEqual(json.loads(result.stdout)["products"][0]["appMatch"]["status"], "unverified")
                self.assertEqual(self.path.read_text(), "[]\n")

    def test_alibaba_platforms_do_not_relabel_each_others_snapshots_or_accept_aws_profile(self):
        for platform, adapter, method, _ in ALIBABA_PLATFORMS:
            with self.subTest(platform=platform):
                other = "alibabacloud" if platform == "aliyun" else "aliyun"
                path, _ = self.snapshot(platform=other)
                self.path.write_text("[]\n")
                with mock.patch.object(adapter, method) as fetch:
                    result = self.invoke("--platform", platform, "--inventory", str(path), "--json")
                    self.assertNotEqual(result.exit_code, 0)
                    result = self.invoke("--platform", platform, "--profile", "aws-seller")
                    self.assertEqual(result.exit_code, 2)
                fetch.assert_not_called()
                self.assertEqual(self.path.read_text(), "[]\n")


if __name__ == "__main__":
    unittest.main()
