from __future__ import annotations

import copy
import io
import json
import socket
import ssl
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import mock

from cli import azure_market as azure
from cli import store
from cli.store_models import InventoryError


ENTITY = "product/00000000-0000-0000-0000-000000000001"
CODE = "websoft9inc.odoo"
SCHEMA = "https://schema.mp.microsoft.com/schema/"


def seller():
    return {"id": ENTITY, "type": "azureVirtualMachine", "identity": {"externalId": "odoo"},
            "alias": "Draft alias, not the listing title"}


def catalog():
    return {"id": CODE, "publisherId": "websoft9inc", "publisherDisplayName": "Websoft9",
            "offerId": "odoo", "offerType": "VirtualMachine", "isPreview": False,
            "isPrivate": False, "isStopSell": False, "isExcludedFromSearch": False,
            "displayName": "Do not use catalog title"}


def resource(resource_type, **values):
    return {"$schema": SCHEMA + resource_type + "/2026-11-01", "product": ENTITY, **values}


def tree():
    return {"root": ENTITY, "target": {"targetType": "live"}, "resources": [
        resource("product", **seller()),
        resource("submission", target={"targetType": "live"}, status="completed", result="succeeded",
                 lifecycleState="generallyAvailable"),
        resource("plan", id="plan/example"),
        resource("price-and-availability-plan", plan="plan/example", audience="public", visibility="visible"),
        resource("listing", kind="azureVM", languageId="en-us", title="Odoo on Azure", shortDescription="Live summary"),
        resource("customer-leads", secret="SENSITIVE CONTACT"),
    ]}


class AzureInventoryTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.public = catalog()
        self.live = tree()
        self.seller_rows = [seller()]
        self.env = mock.patch.dict("os.environ", {"AZURE_TENANT_ID": "tenant.example",
            "AZURE_CLIENT_ID": "client", "AZURE_CLIENT_SECRET": "SECRET &=+"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        patcher = mock.patch.object(azure, "_request_json", side_effect=self.respond)
        self.request = patcher.start()
        self.addCleanup(patcher.stop)

    def respond(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "login.microsoftonline.com" in url:
            return {"access_token": "TOKEN"}
        if url.startswith(azure.CATALOG_API):
            return {"items": [self.public], "nextPageLink": None}
        if "/resource-tree/" in url:
            return self.live
        return {"value": self.seller_rows}

    def test_live_facts_public_identity_and_only_normalized_output(self):
        products = azure.list_products()
        self.assertEqual(len(products), 1)
        p = products[0]
        self.assertEqual((p.code, p.entity_id, p.title, p.summary), (CODE, ENTITY, "Odoo on Azure", "Live summary"))
        self.assertEqual(p.visibility, "StoreVisible")
        self.assertEqual(p.alternate_code, "")
        self.assertNotIn("SENSITIVE", repr(products))
        self.assertNotIn("SECRET", repr(products))
        token_url, token_args = self.calls[0]
        params = urllib.parse.parse_qs(token_args["data"].decode())
        self.assertEqual(params["client_secret"], ["SECRET &=+"])
        self.assertEqual(params["scope"], ["https://graph.microsoft.com/.default"])
        self.assertIn("type=azureVirtualMachine", self.calls[1][0])
        self.assertIn("targetType=live", self.calls[-1][0])
        self.assertIsNone(self.calls[2][1].get("token"))

    def test_missing_credentials_and_supplier_fail_before_network(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(InventoryError, "AZURE_TENANT_ID"):
                azure.list_products()
        self.request.assert_not_called()
        with mock.patch.object(azure, "SUPPLIER_ID", ""):
            with self.assertRaisesRegex(InventoryError, "azure.supplier"):
                azure.list_products()
        self.request.assert_not_called()

    def test_missing_token_fails(self):
        self.request.side_effect = None
        self.request.return_value = {}
        with self.assertRaisesRegex(InventoryError, "no access token"):
            azure.list_products()

    def test_public_catalog_requires_exact_publisher_identity(self):
        for key, value in (("publisherId", "vmlabinc1613642184700"), ("publisherDisplayName", "Other"),
                           ("id", "websoft9inc.other"), ("offerId", "../../escape")):
            self.public = {**catalog(), key: value}
            with self.subTest(key=key), self.assertRaises(InventoryError):
                azure.list_products()

    def test_visibility_flags_are_strict_and_nonpublic_products_are_excluded(self):
        for key in ("isPreview", "isPrivate", "isStopSell", "isExcludedFromSearch"):
            for value in (None, "false", 0):
                self.public = {**catalog(), key: value}
                with self.subTest(key=key, value=value), self.assertRaisesRegex(InventoryError, "flags"):
                    azure.list_products()
            self.public = {**catalog(), key: True}
            self.assertEqual(azure.list_products(), [])
        self.assertFalse(any("/resource-tree/" in url for url, _ in self.calls))

    def test_visible_product_missing_from_seller_blocks_complete_inventory(self):
        self.seller_rows = []
        with self.assertRaisesRegex(InventoryError, "missing from.*seller"):
            azure.list_products()

    def test_seller_identity_duplicates_and_wrong_types_fail(self):
        for rows in ([seller(), seller()], [{**seller(), "type": "softwareAsAService"}],
                     [{**seller(), "id": "../../escape"}], [{**seller(), "identity": None}]):
            self.seller_rows = rows
            with self.assertRaises(InventoryError):
                azure.list_products()

    def test_live_tree_requires_same_identity_live_target_and_english_listing(self):
        changes = [lambda t: t.update(root="other"), lambda t: t.update(target={"targetType": "draft"}),
                   lambda t: t["resources"][0].update(identity={"externalId": "other"}),
                   lambda t: t["resources"][4].update(languageId="zh-cn"),
                   lambda t: t["resources"][4].update(shortDescription=None),
                   lambda t: t["resources"].append(copy.deepcopy(t["resources"][4])),
                   lambda t: t["resources"][1].update(status="running"),
                   lambda t: t["resources"][1].update(lifecycleState="unknown"),
                   lambda t: t["resources"][3].update(visibility="unknown"),
                   lambda t: t["resources"].append(copy.deepcopy(t["resources"][2])),
                   lambda t: t["resources"].append(copy.deepcopy(t["resources"][3])),
                   lambda t: t["resources"][2].update(id=None),
                   lambda t: t["resources"].append(resource("plan", id="plan/missing-availability"))]
        for change in changes:
            self.live = tree()
            change(self.live)
            with self.assertRaises(InventoryError):
                azure.list_products()

    def test_deprecated_or_no_visible_public_plan_is_excluded(self):
        for index, key, value in ((1, "lifecycleState", "deprecated"), (3, "audience", "private"),
                                  (3, "visibility", "hidden"), (2, "lifecycleState", "deprecated")):
            self.live = tree()
            self.live["resources"][index][key] = value
            self.assertEqual(azure.list_products(), [])

    def test_one_detail_failure_blocks_the_inventory(self):
        def respond(url, **kwargs):
            if "/resource-tree/" in url:
                raise InventoryError("Azure inventory request failed (HTTP 403)")
            return self.respond(url, **kwargs)
        self.request.side_effect = respond
        with self.assertRaisesRegex(InventoryError, "HTTP 403"):
            azure.list_products()


class AzurePublisherIsolationTests(unittest.TestCase):
    def test_missing_or_partial_vmlab_credentials_never_use_default_account(self):
        default = {"AZURE_TENANT_ID": "default", "AZURE_CLIENT_ID": "default",
                   "AZURE_CLIENT_SECRET": "DEFAULT SECRET"}
        for vmlab in ({}, {"AZURE_VMLAB_CLIENT_ID": "vmlab"},
                      {"AZURE_VMLAB_TENANT_ID": "vmlab", "AZURE_VMLAB_CLIENT_ID": "vmlab"}):
            with mock.patch.dict("os.environ", {**default, **vmlab}, clear=True), \
                    mock.patch.object(azure, "_request_json") as request:
                with self.assertRaisesRegex(InventoryError, "AZURE_VMLAB"):
                    azure.list_products(platform="azure-vmlab")
                request.assert_not_called()

    def test_parallel_queries_use_independent_tokens_and_publisher_scopes(self):
        environment = {f"{prefix}_{key}": value for prefix, account in
                       (("AZURE", "websoft9"), ("AZURE_VMLAB", "vmlab"))
                       for key, value in (("TENANT_ID", account), ("CLIENT_ID", account),
                                          ("CLIENT_SECRET", account + "-secret"))}

        def respond(url, **kwargs):
            if "login.microsoftonline.com" in url:
                data = urllib.parse.parse_qs(kwargs["data"].decode())
                account = data["client_id"][0]
                self.assertIn("/" + account + "/", url)
                self.assertEqual(data["client_secret"], [account + "-secret"])
                return {"access_token": account}
            if url.startswith(azure.CATALOG_API):
                scope = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["$filter"][0]
                publisher, name = (("websoft9inc", "Websoft9") if "'websoft9inc'" in scope
                                   else ("vmlabinc1613642184700", "VMLAB INC."))
                return {"items": [{**catalog(), "id": publisher + ".odoo",
                                   "publisherId": publisher, "publisherDisplayName": name}]}
            self.assertIn(kwargs["token"], ("websoft9", "vmlab"))
            if "/resource-tree/" in url:
                live = tree()
                live["resources"][4]["shortDescription"] = kwargs["token"]
                return live
            return {"value": [seller()]}

        with mock.patch.dict("os.environ", environment, clear=True), \
                mock.patch.object(azure, "_request_json", side_effect=respond):
            result = store.query_products(["azure", "azure-vmlab"])
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["count"], 2)
        self.assertEqual([r["products"][0]["code"] for r in result["results"]],
                         ["websoft9inc.odoo", "vmlabinc1613642184700.odoo"])
        self.assertEqual([r["products"][0]["shortDescription"] for r in result["results"]],
                         ["websoft9", "vmlab"])
        self.assertEqual(result["results"][1]["store"]["provider"], "VMLAB INC.")
        with self.assertRaises(InventoryError):
            store.inventory_products(result["results"][1], "azure")

    def test_vmlab_query_rejects_default_publisher_catalog(self):
        def respond(url, **kwargs):
            if "login.microsoftonline.com" in url:
                return {"access_token": "vmlab"}
            return {"items": [catalog()]} if url.startswith(azure.CATALOG_API) else {"value": [seller()]}
        with mock.patch.dict("os.environ", {"AZURE_VMLAB_TENANT_ID": "vmlab",
                "AZURE_VMLAB_CLIENT_ID": "vmlab", "AZURE_VMLAB_CLIENT_SECRET": "secret"}, clear=True), \
                mock.patch.object(azure, "_request_json", side_effect=respond):
            with self.assertRaisesRegex(InventoryError, "wrong-publisher"):
                azure.list_products(platform="azure-vmlab")


class AzurePaginationTests(unittest.TestCase):
    def test_seller_and_catalog_follow_all_pages_with_scope_preserved(self):
        for base, params, rows, next_key, token in (
                (azure.API_BASE + "/product", {"type": azure.PRODUCT_TYPE, "$version": azure.API_VERSION}, "value", "@nextLink", "TOKEN"),
                (azure.CATALOG_API, {"api-version": azure.CATALOG_VERSION, "$filter": "publisherId eq 'websoft9inc'"}, "items", "nextPageLink", None)):
            link = base + "?" + urllib.parse.urlencode({**params, "continuationToken": "opaque"})
            with mock.patch.object(azure, "_request_json", side_effect=[
                {rows: [{"id": "first"}], next_key: link}, {rows: [{"id": "second"}]},
            ]) as request:
                result = azure._pages(base, params, rows, next_key, timeout=30, token=token)
            self.assertEqual(len(result), 2)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(request.call_args.kwargs["token"], token)

    def test_malformed_items_and_links_rejected_without_sending_token(self):
        base = azure.API_BASE + "/product"
        for response in ({"value": None}, {"value": [None]},
                         {"value": [], "@nextLink": 123},
                         {"value": [], "@nextLink": "https://evil.example/product?type=azureVirtualMachine"},
                         {"value": [], "@nextLink": base + "?type=softwareAsAService"},
                         {"value": [], "@nextLink": base + "?" + urllib.parse.urlencode({"type": azure.PRODUCT_TYPE})}):
            with mock.patch.object(azure, "_request_json", return_value=response) as request:
                with self.assertRaises(InventoryError):
                    azure._pages(base, {"type": azure.PRODUCT_TYPE, "$version": azure.API_VERSION},
                                 "value", "@nextLink", timeout=30, token="TOKEN")
            self.assertEqual(request.call_count, 1)

    def test_repeated_page_and_later_page_failure_block_completeness(self):
        base = azure.API_BASE + "/product"
        params = {"type": azure.PRODUCT_TYPE}
        link = base + "?" + urllib.parse.urlencode(params)
        with mock.patch.object(azure, "_request_json", return_value={"value": [], "@nextLink": link}):
            with self.assertRaisesRegex(InventoryError, "repeated"):
                azure._pages(base, params, "value", "@nextLink", timeout=30, token="TOKEN")
        with mock.patch.object(azure, "_request_json", side_effect=[
            {"value": [], "@nextLink": link + "&continuationToken=opaque"}, InventoryError("HTTP 429"),
        ]):
            with self.assertRaisesRegex(InventoryError, "429"):
                azure._pages(base, params, "value", "@nextLink", timeout=30, token="TOKEN")


class AzureHttpTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(azure.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_timeout_retry_succeeds_and_logs_safe_stage_and_attempt(self):
        opener = mock.Mock()
        opener.open.side_effect = [TimeoutError("SECRET signed request"),
                                  urllib.error.URLError(TimeoutError("SECRET")),
                                  io.BytesIO(b'{"value": []}')]
        progress = mock.Mock()
        with mock.patch("urllib.request.build_opener", return_value=opener), \
                mock.patch.object(azure.random, "uniform", return_value=0):
            result = azure._request_json("https://graph.microsoft.com?SECRET", timeout=60, token="SECRET",
                                         context="azure-vmlab live detail vmlabinc1613642184700.odoo", progress=progress)
        self.assertEqual(result, {"value": []})
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [mock.call(2), mock.call(4)])
        logs = str(progress.call_args_list)
        self.assertIn("azure-vmlab live detail vmlabinc1613642184700.odoo", logs)
        self.assertIn("retry 2/3", logs)
        self.assertNotIn("SECRET", logs)
        self.assertNotIn("https://", logs)

    def test_all_retryable_http_statuses_retry_then_succeed(self):
        for status in sorted(azure.RETRYABLE_STATUS):
            self.sleep.reset_mock()
            opener = mock.Mock()
            opener.open.side_effect = [urllib.error.HTTPError("https://SECRET", status, "SECRET", {"Retry-After": "7"},
                                                            io.BytesIO(b"SECRET")), io.BytesIO(b'{"value": []}')]
            with self.subTest(status=status), mock.patch("urllib.request.build_opener", return_value=opener):
                azure._request_json("https://graph.microsoft.com", timeout=60)
            self.sleep.assert_called_once_with(7)
            self.assertEqual(opener.open.call_count, 2)

    def test_auth_permission_redirect_and_not_found_errors_are_not_retried(self):
        for status in (301, 302, 307, 400, 401, 403, 404, 409):
            opener = mock.Mock()
            opener.open.side_effect = urllib.error.HTTPError("https://SECRET", status, "SECRET", {}, io.BytesIO(b"SECRET"))
            with self.subTest(status=status), mock.patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaisesRegex(InventoryError, "attempts=1/3"):
                    azure._request_json("https://graph.microsoft.com", timeout=60)
            self.assertEqual(opener.open.call_count, 1)
        self.sleep.assert_not_called()

    def test_retry_exhaustion_remains_fatal_and_sanitized(self):
        opener = mock.Mock()
        opener.open.side_effect = TimeoutError("SECRET")
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaises(InventoryError) as caught:
                azure._request_json("https://graph.microsoft.com?SECRET", timeout=60,
                                    context="azure-vmlab live detail vmlabinc1613642184700.odoo")
        self.assertIn("TimeoutError; attempts=3/3", str(caught.exception))
        self.assertIn("azure-vmlab live detail", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_retry_after_date_invalid_negative_and_oversized_headers_are_bounded(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
        with mock.patch.object(azure.random, "uniform", return_value=0):
            self.assertGreaterEqual(azure._retry_delay(1, future), 18)
            for value in ("invalid SECRET", "-3", "NaN", "inf", "999999999999999999999999"):
                self.assertEqual(azure._retry_delay(1, value), 2)
            self.assertEqual(azure._retry_delay(1, "1000"), 60)

    def test_transient_connection_and_dns_retry_but_tls_and_permanent_dns_do_not(self):
        for error in (ConnectionResetError("SECRET"), socket.gaierror(socket.EAI_AGAIN, "SECRET")):
            opener = mock.Mock()
            opener.open.side_effect = [urllib.error.URLError(error), io.BytesIO(b'{}')]
            with mock.patch("urllib.request.build_opener", return_value=opener):
                self.assertEqual(azure._request_json("https://graph.microsoft.com", timeout=60), {})
            self.assertEqual(opener.open.call_count, 2)
        self.sleep.reset_mock()
        for error in (ssl.SSLCertVerificationError("SECRET"), socket.gaierror(socket.EAI_NONAME, "SECRET")):
            opener = mock.Mock()
            opener.open.side_effect = urllib.error.URLError(error)
            with mock.patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(InventoryError):
                    azure._request_json("https://graph.microsoft.com", timeout=60)
            self.assertEqual(opener.open.call_count, 1)
        self.sleep.assert_not_called()

    def test_token_acquisition_also_retries_transient_errors_without_logging_credentials(self):
        opener = mock.Mock()
        opener.open.side_effect = [TimeoutError("SECRET"), io.BytesIO(b'{"access_token":"PRIVATE TOKEN"}')]
        progress = mock.Mock()
        with mock.patch.dict("os.environ", {"AZURE_VMLAB_TENANT_ID": "tenant", "AZURE_VMLAB_CLIENT_ID": "client",
                                           "AZURE_VMLAB_CLIENT_SECRET": "SECRET"}, clear=True), \
                mock.patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(azure._access_token(60, "azure-vmlab", progress), "PRIVATE TOKEN")
        self.assertIn("azure-vmlab authentication", str(progress.call_args_list))
        self.assertNotIn("SECRET", str(progress.call_args_list))
        self.assertNotIn("PRIVATE TOKEN", str(progress.call_args_list))

    def test_default_timeout_and_live_detail_context(self):
        self.assertEqual(azure.REQUEST_TIMEOUT, 60)
        with mock.patch.object(azure, "_request_json", return_value=tree()) as request:
            azure._live_product(CODE, seller(), "SECRET", 60)
        self.assertEqual(request.call_args.kwargs["context"], "azure live detail " + CODE)

    def test_live_detail_retry_integrates_with_complete_store_inventory_and_fails_closed_on_exhaustion(self):
        for failures, complete in ((1, True), (3, False)):
            opener = mock.Mock()
            opener.open.side_effect = ([TimeoutError("SECRET")] * failures
                                      + [io.BytesIO(json.dumps(tree()).encode())])
            with self.subTest(failures=failures), \
                    mock.patch.object(azure, "_access_token", return_value="SECRET"), \
                    mock.patch.object(azure, "_seller_products", return_value={CODE: seller()}), \
                    mock.patch.object(azure, "_public_codes", return_value={CODE}), \
                    mock.patch("urllib.request.build_opener", return_value=opener):
                result = store.query_platform("azure")
            self.assertEqual(result["complete"], complete, result)
            self.assertEqual(result["count"], 1 if complete else 0)
            self.assertNotIn("SECRET", json.dumps(result))
            if not complete:
                self.assertEqual(result["products"], [])
                self.assertIn("azure live detail " + CODE, result["errors"][0]["message"])

    def test_errors_never_expose_response_request_or_token(self):
        for error in (RuntimeError("SECRET signed request"),
                      urllib.error.HTTPError("https://SECRET", 401, "SECRET", {}, io.BytesIO(b'SECRET')),
                      ValueError("SECRET invalid JSON")):
            opener = mock.Mock()
            opener.open.side_effect = error
            with mock.patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(InventoryError) as caught:
                    azure._request_json("https://graph.microsoft.com", timeout=30, token="SECRET")
            self.assertNotIn("SECRET", str(caught.exception))

    def test_redirects_disabled(self):
        self.assertIsNone(azure._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_invalid_json_envelopes_fail_closed(self):
        for payload in ([], {"error": {"message": "SECRET"}}, {"errors": ["SECRET"]}):
            response = io.BytesIO(json.dumps(payload).encode())
            opener = mock.Mock()
            opener.open.return_value = response
            with mock.patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(InventoryError) as caught:
                    azure._request_json("https://graph.microsoft.com", timeout=30)
            self.assertNotIn("SECRET", str(caught.exception))
            self.assertEqual(opener.open.call_count, 1)
        self.sleep.assert_not_called()

    def test_workflow_includes_both_azure_publishers_and_separate_secrets(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/offer-codes-matrix.yml").read_text()
        self.assertIn("- azure", workflow)
        self.assertIn("platforms=(aws aliyun alibabacloud azure azure-vmlab huaweiyun huaweicloud googlecloud)", workflow)
        self.assertIn("AZURE_CLIENT_SECRET: ${{ secrets.AZURE_CLIENT_SECRET }}", workflow)
        self.assertIn("- azure-vmlab", workflow)
        self.assertIn("AZURE_VMLAB_CLIENT_SECRET: ${{ secrets.AZURE_VMLAB_CLIENT_SECRET }}", workflow)


if __name__ == "__main__":
    unittest.main()
