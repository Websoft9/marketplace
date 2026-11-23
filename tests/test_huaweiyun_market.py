from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from cli import huaweiyun_market as market, offer_codes, store
from cli.cli import cli
from cli.store_models import InventoryError
from tests.click_support import separated_runner


CODE = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"


def item(code=CODE, **changes):
    return {"id": code, "seller_id": market.STORE_ID,
            "corporation_name": market.SUPPLIER_NAME, "corporation_url": "/seller/" + market.STORE_ID,
            "delivery_code": "vm.image", "content_status": 1, "hidden": 0,
            "title": "Odoo 企业应用", "hcontent": "<em>Odoo</em> &amp; ERP 简短描述",
            "url": "/contents/" + code, **changes}


def page(rows, total=None):
    return {"error_code": "00000000", "total": len(rows) if total is None else total,
            "pagination": {"items": rows}}


def international_item(code=CODE, **changes):
    store_id, name = market.supplier("huaweicloud")
    result = item(code, seller_id=store_id, corporation_name=name,
                  corporation_url="/intl/seller/" + store_id, url="/intl/contents/" + code,
                  title="Odoo on Huawei Cloud", content_abstract="Odoo ERP public description")
    result.pop("hcontent")
    result.update(changes)
    return result


class HuaweiStoreAdapterTests(unittest.TestCase):
    def test_reads_all_pages_and_retains_public_images_only(self):
        rows = [item(), item(OTHER, delivery_code="manual.service")]
        with mock.patch.object(market, "PAGE_SIZE", 1), mock.patch.object(market, "_request_page", side_effect=[
            page(rows[:1], 2), page(rows[1:], 2),
        ]) as request:
            products = market.list_products()
        self.assertEqual(request.call_args_list, [mock.call(0, 30), mock.call(1, 30)])
        self.assertEqual(len(products), 1)
        p = products[0]
        self.assertEqual((p.code, p.entity_id, p.title, p.summary),
                         (CODE, CODE, "Odoo 企业应用", "Odoo & ERP 简短描述"))
        self.assertEqual(p.visibility, "StoreVisible")
        self.assertEqual(p.url, "https://marketplace.huaweicloud.com/contents/" + CODE)

    def test_owner_code_and_url_are_strict_even_on_nonimage_rows(self):
        for key, value in (("seller_id", "wrong"), ("corporation_name", "Other"),
                           ("corporation_url", "/intl/seller/" + market.STORE_ID),
                           ("url", "https://evil.example/contents/" + CODE),
                           ("url", "/hidden/contents/" + CODE),
                           ("id", "OFFI123"), ("id", "../../escape")):
            for delivery in ("vm.image", "manual.service"):
                with self.subTest(key=key, delivery=delivery), \
                        mock.patch.object(market, "_request_page", return_value=page([
                            item(**{key: value, "delivery_code": delivery}),
                        ])):
                    with self.assertRaisesRegex(InventoryError, "identity"):
                        market.list_products()

    def test_visibility_missing_unknown_and_bad_field_types_fail_closed(self):
        for key, value in (("hidden", None), ("hidden", False), ("hidden", "0"),
                           ("content_status", None), ("content_status", True),
                           ("content_status", 2), ("content_status", "1"),
                           ("title", None), ("hcontent", None), ("delivery_code", None)):
            with self.subTest(key=key, value=value), \
                    mock.patch.object(market, "_request_page", return_value=page([item(**{key: value})])):
                with self.assertRaises(InventoryError):
                    market.list_products()

    def test_hidden_and_nonimage_products_are_not_exposed(self):
        with mock.patch.object(market, "_request_page", return_value=page([
            item(hidden=1), item(OTHER, delivery_code="manual.service"),
        ])):
            self.assertEqual(market.list_products(), [])

    def test_missing_supplier_fails_before_network(self):
        with mock.patch.object(market, "STORE_ID", ""), mock.patch.object(market, "_request_page") as request:
            with self.assertRaisesRegex(InventoryError, "huaweiyun.supplier"):
                market.list_products()
        request.assert_not_called()

    def test_malformed_incomplete_duplicate_and_changing_pagination_fails(self):
        cases = [
            [{"total": True, "pagination": {"items": []}}],
            [{"total": -1, "pagination": {"items": []}}],
            [{"total": 1, "pagination": {"items": [None]}}],
            [{"total": 1, "pagination": None}],
            [page([], 1)], [page([item()], 0)],
            [page([item()], 2), page([item()], 2)],
            [page([item()], 2), page([item(OTHER)], 3)],
            [page([item()], 2), page([], 2)],
        ]
        for responses in cases:
            with self.subTest(responses=responses), mock.patch.object(market, "PAGE_SIZE", 1), \
                    mock.patch.object(market, "_request_page", side_effect=responses):
                with self.assertRaises(InventoryError):
                    market.list_products()

    def test_empty_store_and_pagination_limit(self):
        with mock.patch.object(market, "_request_page", return_value=page([])):
            self.assertEqual(market.list_products(), [])
        with mock.patch.object(market, "MAX_PAGES", 1), mock.patch.object(market, "PAGE_SIZE", 1), \
                mock.patch.object(market, "_request_page", return_value=page([item()], 2)):
            with self.assertRaisesRegex(InventoryError, "pagination limit"):
                market.list_products()

    def test_later_page_failure_discards_earlier_products(self):
        with mock.patch.object(market, "PAGE_SIZE", 1), mock.patch.object(market, "_request_page", side_effect=[
            page([item()], 2), InventoryError("HTTP 503"),
        ]):
            result = store.query_platform("huaweiyun")
        self.assertFalse(result["complete"])
        self.assertEqual(result["products"], [])
        self.assertEqual(result["count"], 0)


class HuaweiHttpTests(unittest.TestCase):
    def test_uses_exact_guest_query_without_credentials_cookies_or_redirects(self):
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(json.dumps(page([item()])).encode())
        with mock.patch.dict("os.environ", {"HUAWEICLOUD_ACCESS_KEY_ID": "SECRET", "HUAWEICLOUD_SECURITY_TOKEN": "TOKEN"}), \
                mock.patch("urllib.request.build_opener", return_value=opener) as build:
            data = market._request_page(10, 15)
        self.assertEqual(data["total"], 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, market.STORE_API)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {
            "flow_id": "101000", "filter": {"seller_id": market.STORE_ID},
            "sort": "onshelfDesc", "offset": 10, "limit": 10,
        })
        self.assertNotIn("SECRET", repr(request.header_items()))
        self.assertNotIn("TOKEN", repr(request.header_items()))
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))
        self.assertIsInstance(build.call_args.args[0], market._NoRedirect)
        self.assertIsNone(market._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_http_and_json_errors_never_expose_response_or_request(self):
        for value in (RuntimeError("SECRET signed request"),
                      urllib.error.HTTPError("https://SECRET", 403, "SECRET", {}, io.BytesIO(b"SECRET")),
                      b"SECRET invalid JSON", b'[]', b'{"error_code":"failed","error_msg":"SECRET"}',
                      b'{"total":0}'):
            opener = mock.Mock()
            if isinstance(value, Exception):
                opener.open.side_effect = value
            else:
                opener.open.return_value = io.BytesIO(value)
            with mock.patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(InventoryError) as caught:
                    market._request_page(0, 30)
            self.assertNotIn("SECRET", str(caught.exception))


class HuaweiCliTests(unittest.TestCase):
    def setUp(self):
        self.runner = separated_runner()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps([{"key": "odoo", "trademark": "Odoo"},
                                            {"key": "drupal", "trademark": "Drupal"}]))
        self.matrix = self.root / "matrix.json"
        self.previous = [{"id": "odoo", "type": "app", "applications": ["odoo"],
                          "productID": {p: [] for p in offer_codes.REQUIRED_PLATFORMS}}]
        self.previous[0]["productID"].update(huaweiyun=[OTHER], huaweicloud=["international-keep"], aws=["aws-keep"])
        self.matrix.write_text(json.dumps(self.previous))

    def generate(self, *args):
        return self.runner.invoke(cli, ["offer-codes", "gen", "--platform", "huaweiyun",
            "--output", str(self.matrix), "--product-catalog", str(self.catalog), "--json", *args])

    def test_store_and_live_generation_use_identical_public_facts(self):
        with mock.patch.object(market, "_request_page", return_value=page([item()])):
            result = self.runner.invoke(cli, ["store", "products", "--platform", "huaweiyun", "--json"])
            generated = self.generate()
        self.assertEqual(result.exit_code, 0, result.output)
        inventory = json.loads(result.stdout)
        facts = inventory["results"][0]
        self.assertEqual(facts["store"]["id"], market.STORE_ID)
        self.assertIn("not credential ownership", facts["store"]["ownershipEvidence"])
        self.assertEqual(generated.exit_code, 0, generated.output)
        report = json.loads(generated.stdout)
        for key, value in facts["products"][0].items():
            self.assertEqual(report["products"][0][key], value)
        matrix = json.loads(self.matrix.read_text())
        self.assertEqual(matrix[0]["productID"]["huaweiyun"], [CODE])
        self.assertEqual(matrix[0]["productID"]["huaweicloud"], ["international-keep"])
        self.assertEqual(matrix[0]["productID"]["aws"], ["aws-keep"])

    def test_snapshot_dry_run_and_targeted_generation_do_not_touch_unselected_codes(self):
        before = self.matrix.read_bytes()
        with mock.patch.object(market, "_request_page", return_value=page([item()])):
            document = store.query_products(["huaweiyun"])
        path = self.root / "inventory.json"
        path.write_text(json.dumps(document))
        with mock.patch.object(market, "list_products", side_effect=AssertionError("offline")):
            dry = self.generate("--inventory", str(path), "--dry-run")
            self.assertEqual(dry.exit_code, 0, dry.output)
            self.assertFalse(json.loads(dry.stdout)["written"])
            self.assertEqual(self.matrix.read_bytes(), before)
            result = self.generate("--inventory", str(path), "--code", CODE)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(self.matrix.read_text())[0]["productID"]["huaweiyun"], [CODE, OTHER])

    def test_failed_empty_and_unmatched_inventories_cannot_overwrite(self):
        before = self.matrix.read_bytes()
        for response in (page([]), page([item(title="Unknown", hcontent="Unknown")]), InventoryError("HTTP 403")):
            with mock.patch.object(market, "_request_page", side_effect=response if isinstance(response, Exception) else None,
                                   return_value=response):
                result = self.generate()
            self.assertNotEqual(result.exit_code, 0)
            self.assertFalse(json.loads(result.stdout)["written"])
            self.assertEqual(self.matrix.read_bytes(), before)

    def test_wrong_owner_and_offering_id_snapshots_rejected_before_write(self):
        with mock.patch.object(market, "_request_page", return_value=page([item()])):
            document = store.query_products(["huaweiyun"])
        for mutate in (lambda r: r["store"].update(id="foreign"),
                       lambda r: r["products"][0].update(code="OFFI123"),
                       lambda r: r["products"][0].update(entityId="OFFI123"),
                       lambda r: r["products"][0].update(public=False)):
            data = copy.deepcopy(document)
            mutate(data["results"][0])
            path = self.root / "bad.json"
            path.write_text(json.dumps(data))
            before = self.matrix.read_bytes()
            with mock.patch.object(market, "list_products") as network:
                result = self.generate("--inventory", str(path))
            self.assertNotEqual(result.exit_code, 0)
            self.assertEqual(self.matrix.read_bytes(), before)
            network.assert_not_called()

    def test_partial_match_retains_unmatched_products_in_report(self):
        with mock.patch.object(market, "_request_page", return_value=page([
            item(), item(OTHER, title="Unknown", hcontent="Unknown"),
        ])):
            result = self.generate()
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["matched"], 1)
        self.assertEqual(len(report["products"]), 2)
        self.assertEqual(report["unresolved"][0]["code"], OTHER)

    def test_profile_and_unsupported_platform_fail_before_network(self):
        with mock.patch.object(market, "list_products") as network:
            for args in (("--profile", "aws"), ("--platform", "googlecloud")):
                self.assertNotEqual(self.generate(*args).exit_code, 0)
        network.assert_not_called()

    def test_workflow_includes_china_without_credential_requirement(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/offer-codes-matrix.yml").read_text()
        self.assertIn("- huaweiyun", workflow)
        self.assertIn("platforms=(aws aliyun alibabacloud azure azure-vmlab huaweiyun huaweicloud)", workflow)
        self.assertIn("- huaweicloud", workflow)
        self.assertNotIn("secrets.HUAWEICLOUD_ACCESS_KEY", workflow)


class HuaweiInternationalTests(unittest.TestCase):
    def test_international_request_uses_verified_endpoint_flow_and_no_credentials(self):
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(json.dumps(page([international_item()])).encode())
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("urllib.request.build_opener", return_value=opener):
            products = market.list_products(platform="huaweicloud")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, market.INTERNATIONAL_API)
        self.assertEqual(json.loads(request.data), {
            "flow_id": "101002", "filter": {"seller_id": market.supplier("huaweicloud")[0]},
            "offset": 0, "limit": 10,
        })
        self.assertEqual(request.get_header("X-language"), "zh-cn")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))
        self.assertEqual(products[0].summary, "Odoo ERP public description")
        self.assertEqual(products[0].url, "https://marketplace.huaweicloud.com/intl/contents/" + CODE)

    def test_international_never_accepts_china_owner_or_url_or_summary_fallback(self):
        for row in (item(), international_item(corporation_url="/seller/" + market.supplier("huaweicloud")[0]),
                    international_item(url="/contents/" + CODE),
                    international_item(content_abstract=None, hcontent="Do not use China fallback"),
                    international_item(hidden=False), international_item(content_status=2)):
            with self.subTest(row=row), mock.patch.object(market, "_request_page", return_value=page([row])):
                with self.assertRaises(InventoryError):
                    market.list_products(platform="huaweicloud")

    def test_international_pages_and_hidden_nonimage_filtering(self):
        with mock.patch.object(market, "PAGE_SIZE", 1), mock.patch.object(market, "_request_page", side_effect=[
            page([international_item()], 3), page([international_item(OTHER, hidden=1)], 3),
            page([international_item("00000000-0000-0000-0000-000000000003", delivery_code="manual.service")], 3),
        ]) as request:
            products = market.list_products(platform="huaweicloud")
        self.assertEqual([p.code for p in products], [CODE])
        self.assertEqual(request.call_args_list, [mock.call(i, 30, "huaweicloud") for i in range(3)])

    def test_international_pagination_errors_discard_all_partial_facts(self):
        for responses in ([page([international_item()], 2), page([international_item()], 2)],
                          [page([international_item()], 2), page([], 2)],
                          [page([international_item()], 2), page([international_item(OTHER)], 3)],
                          [page([international_item()], 2), InventoryError("HTTP 503")]):
            with mock.patch.object(market, "PAGE_SIZE", 1), \
                    mock.patch.object(market, "_request_page", side_effect=responses):
                result = store.query_platform("huaweicloud")
            self.assertFalse(result["complete"])
            self.assertEqual(result["products"], [])

    def test_concurrent_sites_use_separate_owner_sources_and_snapshot_platforms(self):
        def respond(offset, timeout, platform="huaweiyun"):
            return page([item() if platform == "huaweiyun" else international_item()])
        with mock.patch.object(market, "_request_page", side_effect=respond):
            report = store.query_products(["huaweiyun", "huaweicloud"])
        self.assertTrue(report["complete"], report)
        china, international = report["results"]
        self.assertNotEqual(china["store"]["id"], international["store"]["id"])
        self.assertIn("flow_id=101002", international["sources"][0])
        self.assertIn("International", international["products"][0]["visibilityEvidence"])
        with self.assertRaises(InventoryError):
            store.inventory_products(international, "huaweiyun")

    def test_missing_international_supplier_fails_before_network(self):
        with mock.patch.dict(market.PLATFORM_SUPPLIERS, {"huaweicloud": {}}), \
                mock.patch.object(market, "_request_page") as network:
            with self.assertRaisesRegex(InventoryError, "huaweicloud.supplier"):
                market.list_products(platform="huaweicloud")
        network.assert_not_called()


class HuaweiInternationalCliTests(HuaweiCliTests):
    # Reuse the full CLI/atomic-write/snapshot regression contract for the new site.
    def generate(self, *args):
        return super().generate("--platform", "huaweicloud", *args)

    def test_store_and_live_generation_use_identical_public_facts(self):
        with mock.patch.object(market, "_request_page", return_value=page([international_item()])):
            result = self.generate()
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertEqual(report["platform"], "huaweicloud")
        self.assertEqual(report["products"][0]["shortDescription"], "Odoo ERP public description")
        matrix = json.loads(self.matrix.read_text())
        self.assertEqual(matrix[0]["productID"]["huaweicloud"], [CODE])
        self.assertEqual(matrix[0]["productID"]["huaweiyun"], [OTHER])
        self.assertEqual(matrix[0]["productID"]["aws"], ["aws-keep"])

    def test_snapshot_dry_run_and_targeted_generation_do_not_touch_unselected_codes(self):
        self.previous[0]["productID"]["huaweicloud"] = [OTHER]
        self.matrix.write_text(json.dumps(self.previous))
        with mock.patch.object(market, "_request_page", return_value=page([international_item()])):
            document = store.query_products(["huaweicloud"])
        path = self.root / "international-inventory.json"
        path.write_text(json.dumps(document))
        before = self.matrix.read_bytes()
        with mock.patch.object(market, "list_products", side_effect=AssertionError("offline")):
            dry = self.generate("--inventory", str(path), "--dry-run")
            self.assertEqual(dry.exit_code, 0, dry.output)
            self.assertEqual(self.matrix.read_bytes(), before)
            result = self.generate("--inventory", str(path), "--code", CODE)
        self.assertEqual(result.exit_code, 0, result.output)
        matrix = json.loads(self.matrix.read_text())
        self.assertEqual(matrix[0]["productID"]["huaweicloud"], [CODE, OTHER])
        self.assertEqual(matrix[0]["productID"]["huaweiyun"], [OTHER])

    def test_partial_match_retains_unmatched_products_in_report(self):
        with mock.patch.object(market, "_request_page", return_value=page([
            international_item(), international_item(OTHER, title="Unknown", content_abstract="Unknown"),
        ])):
            result = self.generate()
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["matched"], 1)
        self.assertEqual(len(report["products"]), 2)

    def test_failed_empty_and_unmatched_inventories_cannot_overwrite(self):
        before = self.matrix.read_bytes()
        for response in (page([]), page([international_item(title="Unknown", content_abstract="Unknown")]),
                         InventoryError("HTTP 403")):
            with mock.patch.object(market, "_request_page", side_effect=response if isinstance(response, Exception) else None,
                                   return_value=response):
                result = self.generate()
            self.assertNotEqual(result.exit_code, 0)
            self.assertFalse(json.loads(result.stdout)["written"])
            self.assertEqual(self.matrix.read_bytes(), before)

    def test_wrong_owner_and_offering_id_snapshots_rejected_before_write(self):
        with mock.patch.object(market, "_request_page", return_value=page([international_item()])):
            document = store.query_products(["huaweicloud"])
        for mutate in (lambda r: r["store"].update(id=market.STORE_ID),
                       lambda r: r["products"][0].update(code="OFFI123"),
                       lambda r: r["products"][0].update(entityId="OFFI123"),
                       lambda r: r["products"][0].update(public=False)):
            data = copy.deepcopy(document)
            mutate(data["results"][0])
            path = self.root / "bad-international.json"
            path.write_text(json.dumps(data))
            before = self.matrix.read_bytes()
            with mock.patch.object(market, "list_products") as network:
                result = self.generate("--inventory", str(path))
            self.assertNotEqual(result.exit_code, 0)
            self.assertEqual(self.matrix.read_bytes(), before)
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
