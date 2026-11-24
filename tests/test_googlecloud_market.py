from __future__ import annotations

import copy
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from cli import googlecloud_market as market, offer_codes, store
from cli.cli import cli
from cli.store_models import InventoryError
from tests.click_support import separated_runner


def item(number=1, **changes):
    return {"productId": f"00000000-0000-0000-0000-{number:012d}",
            "productDisplayName": f"websoft9-public/app-{number}", "subtitle": "Websoft9",
            "title": "WordPress", "description": "Public WordPress summary",
            "productType": "Virtual machines", "familyType": "PackagedDeployment", **changes}


def page(rows, **changes):
    return {"shelfs": [{"shelfType": "SEARCH_RESULT", "cards": rows}], "totalCount": len(rows),
            "selectedLabeledFacetValues": [{"facetBinding": {"facetId": "partner", "facetValueId": "Websoft9"},
                                            "displayName": "Websoft9"}], **changes}


class GoogleCloudAdapterTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap = mock.patch.object(market, "_bootstrap", return_value="public-bootstrap-key")
        self.bootstrap.start()
        self.addCleanup(self.bootstrap.stop)

    def test_collection_filters_non_vm_after_verifying_all_pages(self):
        rows = [item(), item(2, productType="SaaS & APIs", familyType="ManagedService")]
        with mock.patch.object(market, "_request_page", return_value=page(rows)):
            products = market.list_products()
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].code, "websoft9-public/app-1")
        self.assertEqual(products[0].entity_id, item()["productId"])
        self.assertEqual(products[0].summary, "Public WordPress summary")
        self.assertEqual(products[0].visibility, "StoreVisible")
        self.assertEqual(products[0].url, "https://console.cloud.google.com/marketplace/product/websoft9-public/app-1")

    def test_complete_token_pagination(self):
        responses = [page([item(n) for n in range(1, 41)], totalCount=41, nextPageToken="next"),
                     page([item(41)], totalCount=41)]
        with mock.patch.object(market, "_request_page", side_effect=responses) as request:
            self.assertEqual(len(market.list_products()), 41)
        self.assertEqual(request.call_args_list, [mock.call("", "public-bootstrap-key", 30),
                                                  mock.call("next", "public-bootstrap-key", 30)])

    def test_malformed_owner_type_fields_facets_and_pagination_fail_closed(self):
        bad = [page([item(subtitle="Foreign")]), page([item(productDisplayName="foreign/app")]),
               page([item(productId="service-name")]), page([item(productType=None)]),
               page([item(familyType="Unknown")]), page([item(title=None)]),
               page([item(description=None)]), page([item()], selectedLabeledFacetValues=[]),
               page([item()], totalCount=True), page([item()], totalCount=2),
               page([item(), item()]), page([item(1), item(2, productId=item()["productId"])]),
               page([item()], nextPageToken="unexpected"), page([item()], shelfs=[]), [],
               page([item()], nextPageToken=None)]
        for response in bad:
            with self.subTest(response=response), mock.patch.object(market, "_request_page", return_value=response):
                with self.assertRaises(InventoryError):
                    market.list_products()

    def test_later_page_failure_and_changing_total_never_return_partial_inventory(self):
        first = page([item(n) for n in range(1, 41)], totalCount=41, nextPageToken="next")
        for second in (InventoryError("HTTP 503"), page([item(41)], totalCount=42),
                       page([item(1)], totalCount=41), page([item(41)], totalCount=41, nextPageToken="next")):
            with mock.patch.object(market, "_request_page", side_effect=[first, second]):
                result = store.query_platform("googlecloud")
            self.assertFalse(result["complete"])
            self.assertEqual(result["products"], [])

    def test_page_limit_and_verified_empty_result(self):
        with mock.patch.object(market, "_request_page", return_value=page([])):
            self.assertEqual(market.list_products(), [])
        with mock.patch.object(market, "MAX_PAGES", 1), mock.patch.object(market, "_request_page", return_value=
                page([item(n) for n in range(1, 41)], totalCount=41, nextPageToken="next")):
            with self.assertRaisesRegex(InventoryError, "pagination limit"):
                market.list_products()

    def test_request_uses_fixed_public_endpoint_no_account_credentials_and_sanitized_errors(self):
        response = [{"successfulResult": {"resultData": {"@type": market.RESPONSE_TYPE,
                                                        "marketplaceBrowseProducts": page([item()])}}}]
        with mock.patch.object(market, "_read", return_value=json.dumps(response)) as read:
            self.assertEqual(market._request_page("continuation", "public-key", 7), page([item()]))
        request, timeout = read.call_args.args
        self.assertEqual(timeout, 7)
        self.assertEqual(request.full_url, market.STORE_API)
        self.assertEqual(request.get_header("X-goog-api-key"), "public-key")
        self.assertFalse(request.has_header("Authorization"))
        self.assertFalse(request.has_header("Cookie"))
        parameters = json.loads(request.data)["entityRequest"]["requestPayload"]["parameters"]
        self.assertFalse(parameters["isPrivateCatalog"])
        self.assertEqual(parameters["selectedFacetValues"], [{"facetId": "partner", "facetValueId": "Websoft9"}])
        for text in ("bad json", "[]", '[{"error": "SECRET"}]', '[{"successfulResult": null}]'):
            with mock.patch.object(market, "_read", return_value=text):
                with self.assertRaises(InventoryError) as error:
                    market._request_page("", "public-key", 30)
                self.assertNotIn("SECRET", str(error.exception))
        opener = mock.Mock()
        for error in (TimeoutError("SECRET"), urllib.error.HTTPError(market.STORE_API, 403, "SECRET", {}, None)):
            opener.open.side_effect = error
            with mock.patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(InventoryError) as caught:
                    market._read(request, 30)
                self.assertNotIn("SECRET", str(caught.exception))
        self.assertIsNone(market._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))

    def test_bootstrap_schema_and_supplier_fail_before_api(self):
        self.bootstrap.stop()
        with mock.patch.object(market, "_read", return_value='<script>{"LjgHrc":"frontend-only"}</script>'):
            self.assertEqual(market._bootstrap(30), "frontend-only")
        with mock.patch.object(market, "_read", return_value="unknown schema"):
            with self.assertRaisesRegex(InventoryError, "bootstrap schema"):
                market._bootstrap(30)
        with mock.patch.dict(market.PLATFORM_SUPPLIERS, {"googlecloud": {}}), mock.patch.object(market, "_bootstrap") as request:
            with self.assertRaisesRegex(InventoryError, "supplier"):
                market.list_products()
            request.assert_not_called()


class GoogleCloudCliTests(unittest.TestCase):
    def setUp(self):
        self.runner = separated_runner()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps([{"key": "wordpress", "trademark": "WordPress"}]))
        self.matrix = self.root / "matrix.json"
        self.previous = [{"id": "wordpress", "type": "app", "applications": ["wordpress"],
                          "productID": {p: [] for p in offer_codes.REQUIRED_PLATFORMS}}]
        self.previous[0]["productID"]["aws"] = ["aws-preserved"]
        self.matrix.write_text(json.dumps(self.previous))
        patcher = mock.patch.object(market, "_bootstrap", return_value="public-key")
        patcher.start()
        self.addCleanup(patcher.stop)

    def generate(self, *args):
        return self.runner.invoke(cli, ["offer-codes", "gen", "--platform", "googlecloud", "--json",
            "--output", str(self.matrix), "--product-catalog", str(self.catalog), *args])

    def test_offer_list_store_and_generation_share_facts_without_catalog_reads_for_listing(self):
        with mock.patch.object(market, "_request_page", return_value=page([item()])):
            listed = self.runner.invoke(cli, ["offer", "list", "--platform", "googlecloud", "--json"])
            stored = self.runner.invoke(cli, ["store", "products", "--platform", "googlecloud", "--json"])
            generated = self.generate()
        for result in (listed, stored, generated):
            self.assertEqual(result.exit_code, 0, result.output)
        facts = json.loads(stored.stdout)["results"][0]["products"][0]
        self.assertEqual(json.loads(listed.stdout)["results"][0]["products"][0], facts)
        report = json.loads(generated.stdout)
        for key, value in facts.items():
            self.assertEqual(report["products"][0][key], value)
        matrix = json.loads(self.matrix.read_text())
        self.assertEqual(matrix[0]["productID"]["googlecloud"], ["websoft9-public/app-1"])
        self.assertEqual(matrix[0]["productID"]["aws"], ["aws-preserved"])
        self.assertEqual(offer_codes.verify([("matrix", matrix)])["errors"], 0)
        self.assertFalse(any(i["message"] == "platform key is not part of the standard set"
                             for i in offer_codes.verify([("matrix", matrix)])["issues"]))

    def test_offline_snapshot_dry_run_and_targeted_generation(self):
        self.previous[0]["productID"]["googlecloud"] = ["websoft9-public/keep"]
        self.matrix.write_text(json.dumps(self.previous))
        with mock.patch.object(market, "_request_page", return_value=page([item()])):
            document = store.query_products(["googlecloud"])
        snapshot = self.root / "inventory.json"
        snapshot.write_text(json.dumps(document))
        before = self.matrix.read_bytes()
        with mock.patch.object(market, "list_products", side_effect=AssertionError("offline")):
            result = self.generate("--inventory", str(snapshot), "--dry-run")
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertFalse(json.loads(result.stdout)["written"])
            self.assertEqual(self.matrix.read_bytes(), before)
            result = self.generate("--inventory", str(snapshot), "--code", "websoft9-public/app-1")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(self.matrix.read_text())[0]["productID"]["googlecloud"],
                         ["websoft9-public/app-1", "websoft9-public/keep"])

    def test_failed_empty_unmatched_inventory_never_overwrites(self):
        before = self.matrix.read_bytes()
        for response in (page([]), page([item(title="Unknown", description="Unknown")]), InventoryError("HTTP 403")):
            with mock.patch.object(market, "_request_page", side_effect=response if isinstance(response, Exception) else None,
                                   return_value=response):
                result = self.generate()
            self.assertNotEqual(result.exit_code, 0)
            self.assertFalse(json.loads(result.stdout)["written"])
            self.assertEqual(self.matrix.read_bytes(), before)

    def test_snapshot_identity_owner_and_visibility_are_validated(self):
        with mock.patch.object(market, "_request_page", return_value=page([item()])):
            document = store.query_products(["googlecloud"])
        for mutate in (lambda r: r["store"].update(id="foreign"),
                       lambda r: r["products"][0].update(code="foreign/app"),
                       lambda r: r["products"][0].update(entityId="service-id"),
                       lambda r: r["products"][0].update(public=False)):
            data = copy.deepcopy(document)
            mutate(data["results"][0])
            path = self.root / "bad.json"
            path.write_text(json.dumps(data))
            before = self.matrix.read_bytes()
            with mock.patch.object(market, "list_products") as network:
                result = self.generate("--inventory", str(path))
                network.assert_not_called()
            self.assertNotEqual(result.exit_code, 0)
            self.assertEqual(self.matrix.read_bytes(), before)

    def test_profile_rejected_before_network(self):
        with mock.patch.object(market, "list_products") as network:
            result = self.generate("--profile", "seller")
        self.assertNotEqual(result.exit_code, 0)
        network.assert_not_called()

    def test_existing_matrix_google_column_supports_normal_maintenance(self):
        result = self.runner.invoke(cli, ["offer-codes", "add", "--offer", str(self.matrix),
            "--app", "wordpress", "--platform", "googlecloud", "--code", "websoft9-public/app-1",
            "--product-catalog", str(self.catalog), "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        found = self.runner.invoke(cli, ["offer-codes", "find", "--offer", str(self.matrix),
            "--platform", "googlecloud", "--json"])
        self.assertEqual(found.exit_code, 0, found.output)
        self.assertEqual(json.loads(found.stdout)["codes"][0]["code"], "websoft9-public/app-1")
        removed = self.runner.invoke(cli, ["offer-codes", "remove", "--offer", str(self.matrix),
            "--app", "wordpress", "--platform", "googlecloud", "--code", "websoft9-public/app-1", "--json"])
        self.assertEqual(removed.exit_code, 0, removed.output)
        matrix = json.loads(self.matrix.read_text())
        self.assertEqual(matrix[0]["productID"]["googlecloud"], [])
        self.assertEqual(matrix[0]["productID"]["aws"], ["aws-preserved"])
