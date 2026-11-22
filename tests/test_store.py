from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from cli import alibabacloud_market, aliyun_market, aws_inventory, azure_market, store
from cli.cli import cli
from cli.store_models import InventoryError, Product
from tests.click_support import separated_runner


class StoreQueryTests(unittest.TestCase):
    def setUp(self):
        self.runner = separated_runner()

    def test_json_contains_unmapped_products_and_never_fetches_catalog_or_matrix(self):
        # This product deliberately cannot be mapped to an application.
        product = Product("cmjj000", "cmjj000", "Unidentified runtime", "Full short description", "StoreVisible")
        with mock.patch.object(aliyun_market, "list_aliyun_products", return_value=[product]), \
                mock.patch("cli.marketplace.load_json", side_effect=AssertionError("no catalog reads")), \
                mock.patch("cli.offer_codes.load_source", side_effect=AssertionError("no matrix reads")):
            result = self.runner.invoke(cli, ["store", "products", "--platform", "aliyun", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertEqual(report["schemaVersion"], 1)
        self.assertTrue(report["complete"])
        self.assertEqual(report["count"], 1)
        row = report["results"][0]
        self.assertEqual(row["store"]["id"], "228399")
        self.assertEqual(row["scope"], {"productType": "image", "visibility": "public"})
        self.assertEqual(row["errors"], [])
        self.assertIn("+00:00", row["queriedAt"])
        item = row["products"][0]
        self.assertEqual(item["code"], "cmjj000")
        self.assertEqual(item["title"], "Unidentified runtime")
        self.assertEqual(item["shortDescription"], "Full short description")
        self.assertEqual(item["url"], "https://market.aliyun.com/detail/cmjj000")
        self.assertEqual(item["visibility"], "StoreVisible")
        self.assertTrue(item["public"])
        self.assertIn("hiddenForCloudMarket", item["visibilityEvidence"])

    def test_all_keeps_successes_and_records_failure_in_parseable_json(self):
        def fetch(platform, **kwargs):
            kwargs["progress"](f"{platform}: progress")
            if platform == "aliyun":
                raise InventoryError("Aliyun store query failed; visibility unverified")
            if platform == "azure":
                return [Product("websoft9inc.odoo", "product/00000000-0000-0000-0000-000000000001",
                                "Unknown app", "summary", "StoreVisible")]
            if platform == "azure-vmlab":
                return [Product("vmlabinc1613642184700.odoo", "product/00000000-0000-0000-0000-000000000002",
                                "Unknown app", "summary", "StoreVisible")]
            return [Product("code", "entity", "Unknown app", "summary",
                            "Public" if platform == "aws" else "StoreVisible")]

        with mock.patch.object(store, "collect_products", side_effect=fetch) as collect:
            result = self.runner.invoke(cli, ["store", "products", "--platform", "all", "--json"])
        self.assertEqual(result.exit_code, 1, result.output)
        report = json.loads(result.stdout)
        self.assertFalse(report["complete"])
        self.assertEqual(report["count"], 4)
        self.assertEqual([r["platform"] for r in report["results"]], list(store.PLATFORMS))
        failed = report["results"][1]
        self.assertFalse(failed["complete"])
        self.assertEqual(failed["products"], [])
        self.assertEqual(failed["errors"][0]["code"], "InventoryError")
        self.assertIn("visibility unverified", failed["errors"][0]["message"])
        self.assertEqual(collect.call_count, 5)
        self.assertIn("progress", result.stderr)

    def test_unknown_sdk_errors_are_not_stringified(self):
        with mock.patch.object(store, "collect_products", side_effect=RuntimeError("SECRET signed request")):
            report = store.query_products(["aws"])
        self.assertFalse(report["complete"])
        self.assertNotIn("SECRET", json.dumps(report))
        self.assertIn("RuntimeError", report["results"][0]["errors"][0]["message"])

    def test_query_service_never_exposes_nonpublic_adapter_rows_as_complete_public_facts(self):
        with mock.patch.object(store, "collect_products", return_value=[
            Product("public", "prod-p", "Odoo", visibility="Public"),
            Product("restricted", "prod-r", "Odoo", visibility="Restricted"),
        ]):
            report = store.query_products(["aws"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["results"][0]["products"], [])
        self.assertEqual(report["count"], 0)
        self.assertEqual(report["results"][0]["errors"][0]["code"], "InventoryError")

    def test_profile_only_applies_to_aws_worker(self):
        with mock.patch.object(aws_inventory, "list_aws_products", return_value=[]) as aws, \
                mock.patch.object(aliyun_market, "list_aliyun_products", return_value=[]) as aliyun:
            report = store.query_products(["aws", "aliyun"], profile="seller")
        self.assertTrue(report["complete"])
        aws.assert_called_once_with("seller", public_only=True, progress=None)
        aliyun.assert_called_once_with(progress=None)
        # Authenticated seller is not assumed to be the configured Websoft9 profile.
        self.assertEqual(report["results"][0]["store"]["id"], "SELF")
        self.assertIsNone(report["results"][0]["store"]["url"])

    def test_multiple_platform_selection_deduplicates_and_preserves_order(self):
        with mock.patch.object(store, "collect_products", return_value=[]) as collect:
            result = self.runner.invoke(cli, ["store", "products", "--platform", "aliyun",
                                             "--platform", "aws", "--platform", "aliyun", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual([r["platform"] for r in json.loads(result.stdout)["results"]], ["aliyun", "aws"])
        self.assertEqual(collect.call_count, 2)

    def test_invalid_selection_fails_before_network(self):
        for args in (["--platform", "huaweicloud"],
                     ["--platform", "all", "--platform", "aws"],
                     ["--platform", "aliyun", "--profile", "seller"]):
            with mock.patch.object(store, "collect_products") as collect:
                result = self.runner.invoke(cli, ["store", "products", *args])
            self.assertEqual(result.exit_code, 2, result.output)
            collect.assert_not_called()

    def test_human_output_and_explicit_json_failure_exit(self):
        with mock.patch.object(store, "collect_products", return_value=[
            Product("code", "entity", "Unknown | title\nline", "summary", "StoreVisible"),
        ]):
            result = self.runner.invoke(cli, ["store", "products", "--platform", "alibabacloud"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("| Product code | Title | Short description |", result.stdout)
        self.assertIn("Unknown \\| title line", result.stdout)

    def test_azure_uses_configured_publisher_and_retains_only_public_facts(self):
        p = Product("websoft9inc.odoo", "product/00000000-0000-0000-0000-000000000001",
                    "Odoo", "Live summary", "StoreVisible")
        with mock.patch.object(azure_market, "list_products", return_value=[p]) as fetch:
            result = self.runner.invoke(cli, ["store", "products", "--platform", "azure", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        fetch.assert_called_once()
        report = json.loads(result.stdout)["results"][0]
        self.assertEqual(report["store"]["id"], "websoft9inc")
        self.assertEqual(report["products"][0]["entityId"], p.entity_id)
        self.assertEqual(report["products"][0]["shortDescription"], "Live summary")
        self.assertIn("isPreview", report["products"][0]["visibilityEvidence"])

    def test_azure_snapshot_rejects_other_publisher_or_invalid_identity(self):
        for code, entity_id in (("vmlabinc1613642184700.odoo", "product/00000000-0000-0000-0000-000000000001"),
                                ("websoft9inc.odoo", "not-a-durable-id")):
            with mock.patch.object(azure_market, "list_products", return_value=[
                Product(code, entity_id, "Odoo", visibility="StoreVisible"),
            ]):
                report = store.query_platform("azure")
            self.assertFalse(report["complete"])
            self.assertEqual(report["products"], [])

    def test_real_product_url_is_used_when_adapter_provides_one(self):
        url = "https://marketplace.alibabacloud.com/products/56718003/sgcmjj000.html"
        with mock.patch.object(alibabacloud_market, "list_products", return_value=[
            Product("sgcmjj000", "sgcmjj000", "Title", url=url, visibility="StoreVisible"),
        ]):
            report = store.query_products(["alibabacloud"])
        self.assertEqual(report["results"][0]["products"][0]["url"], url)

    def test_single_failed_platform_still_emits_json_and_nonzero_exit(self):
        with mock.patch.object(store, "collect_products", side_effect=InventoryError("credentials missing")):
            result = self.runner.invoke(cli, ["store", "products", "--platform", "aws", "--json"])
        self.assertEqual(result.exit_code, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["results"][0]["count"], 0)
        self.assertFalse(report["results"][0]["complete"])

    def test_help_registers_store_command(self):
        self.assertIn("store", self.runner.invoke(cli, ["--help"]).output)
        result = self.runner.invoke(cli, ["store", "products", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("all", result.output)

    def test_public_scope_is_explicit_and_unsupported_scopes_fail_before_network(self):
        with mock.patch.object(store, "collect_products", return_value=[]) as collect:
            result = self.runner.invoke(cli, ["store", "products", "--platform", "aws",
                                             "--visibility", "public", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(collect.call_args.kwargs["visibility"], "public")
        self.assertEqual(json.loads(result.stdout)["results"][0]["scope"]["visibility"], "public")
        with mock.patch.object(store.aws_inventory, "list_aws_products") as backend:
            for scope in ("all", "restricted", "draft"):
                result = self.runner.invoke(cli, ["store", "products", "--platform", "aws", "--visibility", scope])
                self.assertEqual(result.exit_code, 2)
                with self.assertRaises(InventoryError):
                    store.get_inventory("aws", visibility=scope)
            backend.assert_not_called()

    def test_shared_inventory_input_and_provenance(self):
        with mock.patch.object(store, "query_platform", return_value={"complete": False}) as query:
            self.assertEqual(store.get_inventory("aws", profile="seller", visibility="public"),
                             {"complete": False})
        query.assert_called_once_with("aws", profile="seller", visibility="public", progress=None)
        inventory = {"platform": "aws", "scope": {"visibility": "public"},
                     "complete": True, "products": ["not copied into metadata"]}
        with mock.patch.object(store, "load_inventory", return_value=inventory) as load, \
                mock.patch.object(store, "query_platform") as query:
            self.assertIs(store.get_inventory("aws", inventory_path=Path("inventory.json")), inventory)
        load.assert_called_once_with(Path("inventory.json"), "aws")
        query.assert_not_called()
        metadata = store.inventory_provenance(inventory, Path("inventory.json"))
        self.assertNotIn("products", metadata)
        self.assertEqual(metadata["source"], "snapshot")
        self.assertEqual(metadata["sourcePath"], "inventory.json")
        with self.assertRaisesRegex(InventoryError, "cannot be combined"):
            store.get_inventory("aws", inventory_path="inventory.json", profile="seller")

    def test_apply_workflow_snapshots_public_store_facts_before_resolving(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/offer-update.yml").read_text()
        snapshot = workflow.index("- name: Snapshot public store inventory")
        resolve = workflow.index("- name: Resolve public offer targets from store facts")
        build = workflow.index("- name: Build preview")
        self.assertLess(snapshot, resolve)
        self.assertLess(resolve, build)
        self.assertIn("mk store products", workflow[snapshot:resolve])
        self.assertIn("--visibility public", workflow[snapshot:resolve])
        self.assertIn("mk offer resolve", workflow[resolve:build])
        self.assertIn("--visibility public", workflow[resolve:build])
        self.assertIn("--inventory /tmp/opencode/offer-run/inventory.json", workflow[resolve:build])


if __name__ == "__main__":
    unittest.main()
