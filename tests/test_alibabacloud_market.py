from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from cli import alibabacloud_market
from cli.store_models import InventoryError


def api_product(code="sgcmjj001", **updates):
    return {
        "Code": code, "Name": "Odoo 企业 ERP", "ShortDescription": "Odoo Community, ERP",
        "SupplierName": alibabacloud_market.SUPPLIER_NAME, "SupplierId": 2116499,
        "DeliveryWay": "Image", **updates,
    }


def api_page(rows, page=1, total=None):
    return {
        "PageNumber": page, "PageSize": alibabacloud_market.PAGE_SIZE,
        "TotalCount": len(rows) if total is None else total,
        "ProductItems": {"ProductItem": rows},
    }


def store_product(code="sgcmjj001", **updates):
    return {"productInfo": {
        "code": code, "title": "Store title", "describe": "Store summary",
        "companyName": "Websoft9", "deliveryMethod": "Image",
        "shopUrl": "/store/2116499.html",
        "detailUrl": f"/products/56718003/{code}.html", **updates,
    }}


def store_page(rows, page=1, total=None):
    total = len(rows) if total is None else total
    return {"success": True, "result": {
        "product": rows, "totalRecords": total, "curPage": page,
        "totalPage": (total + 19) // 20,
    }}


class InternationalInventoryTests(unittest.TestCase):
    def setUp(self):
        self.client = mock.Mock()
        self.requests = []

        def request():
            value = mock.Mock()
            self.requests.append(value)
            return value

        module = "aliyunsdkmarket.request.v20151101.DescribeProductsRequest"
        patcher = mock.patch.dict("sys.modules", {
            module: SimpleNamespace(DescribeProductsRequest=request),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(alibabacloud_market, "_client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(alibabacloud_market.urllib.request, "urlopen")
        self.opener = patcher.start()
        self.addCleanup(patcher.stop)

    def responses(self, stores, apis):
        self.opener.side_effect = [io.BytesIO(json.dumps(p).encode()) for p in stores]
        self.client.do_action_with_exception.side_effect = [
            json.dumps(p).encode() for p in apis
        ]

    def test_api_facts_are_authoritative_and_store_visibility_is_required(self):
        apis = [
            api_product("sgcmjj001", Name="API title", ShortDescription="API description"),
            api_product("hidden"),
            api_product("service", DeliveryWay="Service"),
            api_product("foreign", SupplierName="Other"),
        ]
        stores = [
            store_product("sgcmjj001", title="Store title", describe="Store summary"),
            store_product("foreign-store", companyName="Other"),
            store_product("service-store", deliveryMethod="Service"),
        ]
        self.responses([store_page(stores)], [api_page(apis)])
        result = alibabacloud_market.list_products()
        self.assertEqual([product.code for product in result], ["sgcmjj001"])
        self.assertEqual(result[0].title, "API title")
        self.assertEqual(result[0].summary, "API description")
        self.assertEqual(result[0].visibility, "StoreVisible")
        self.assertIn("/56718003/sgcmjj001.html", result[0].url)
        self.requests[0].set_SearchTerm.assert_called_once_with(alibabacloud_market.SUPPLIER_NAME)
        self.requests[0].set_Filters.assert_called_once_with([
            {"Key": "productType", "Value": "MIRROR"},
        ])
        self.requests[0].set_PageSize.assert_called_once_with(50)
        self.assertTrue(all("publisherId=2116499" in call.args[0].full_url
                            for call in self.opener.call_args_list))

    def test_every_api_and_store_page_is_read(self):
        api_rows = [api_product(f"sgcmjj{i:03}") for i in range(51)]
        store_rows = [store_product(f"sgcmjj{i:03}") for i in range(21)]
        self.responses(
            [store_page(store_rows[:20], total=21), store_page(store_rows[20:], page=2, total=21)],
            [api_page(api_rows[:50], total=51), api_page(api_rows[50:], page=2, total=51)],
        )
        result = alibabacloud_market.list_products()
        self.assertEqual(len(result), 21)
        self.requests[1].set_PageNumber.assert_called_once_with(2)
        self.assertIn("curPage=2", self.opener.call_args_list[1].args[0].full_url)

    def test_visible_store_image_missing_from_api_blocks_write(self):
        self.responses([store_page([store_product("only-in-store")])],
                       [api_page([api_product("other")])])
        with self.assertRaisesRegex(ValueError, "missing from the supplier API"):
            alibabacloud_market.list_products()

    def test_api_total_change_restarts_inventory(self):
        rows = [api_product("sgcmjj001"), api_product("sgcmjj002"), api_product("sgcmjj003")]
        with mock.patch.object(alibabacloud_market, "PAGE_SIZE", 2):
            self.responses(
                [store_page([store_product(product["Code"]) for product in rows])],
                [
                    api_page(rows[:2], total=3),
                    api_page(rows[2:], page=2, total=4),
                    api_page(rows[:2], total=3),
                    api_page(rows[2:], page=2, total=3),
                ],
            )
            result = alibabacloud_market.list_products()
        self.assertEqual([product.code for product in result],
                         ["sgcmjj001", "sgcmjj002", "sgcmjj003"])
        self.assertEqual(self.client.do_action_with_exception.call_count, 4)

    def test_api_malformed_data_or_pagination_fails_closed(self):
        good = api_page([api_product()])
        cases = [
            [], {"Code": "error-with-SECRET"}, {"Success": False}, {"Result": []},
            {**good, "PageNumber": 2}, {**good, "PageSize": 10},
            {**good, "TotalCount": 2}, {**good, "TotalCount": True},
            {**good, "TotalCount": -1}, {**good, "ProductItems": []},
            {**good, "ProductItems": {"ProductItem": [None]}},
            api_page([api_product("")]), api_page([api_product(), api_product()]),
        ]
        for response in cases:
            with self.subTest(response=response):
                self.responses([store_page([store_product()])], [response])
                with self.assertRaises(ValueError) as error:
                    alibabacloud_market.list_products()
                self.assertNotIn("SECRET", str(error.exception))

    def test_store_malformed_data_fails_closed(self):
        good = store_page([store_product()])
        cases = [
            [], {"success": False}, {"success": True, "result": []},
            {**good, "result": {**good["result"], "totalRecords": True}},
            {**good, "result": {**good["result"], "totalRecords": 2}},
            {**good, "result": {**good["result"], "curPage": 2}},
            {**good, "result": {**good["result"], "totalPage": 8}},
            store_page([None]), store_page([{"productInfo": None}]),
            store_page([store_product("")]), store_page([store_product(), store_product()]),
            store_page([store_product(shopUrl="/store/other.html")]),
            store_page([store_product(detailUrl="https://untrusted.example/signed-url")]),
        ]
        for response in cases:
            with self.subTest(response=response), mock.patch.object(
                alibabacloud_market.urllib.request, "urlopen",
                side_effect=[io.BytesIO(json.dumps(response).encode())],
            ):
                with self.assertRaises(InventoryError):
                    alibabacloud_market.list_products()

    def test_complete_empty_inventories_return_empty(self):
        self.responses([store_page([])], [api_page([])])
        self.assertEqual(alibabacloud_market.list_products(), [])
        self.responses(
            [store_page([])],
            [{"PageNumber": 1, "PageSize": 50, "TotalCount": 0}],
        )
        self.assertEqual(alibabacloud_market.list_products(), [])

    def test_store_network_and_json_errors_are_sanitized(self):
        for effect in (RuntimeError("signed request SECRET"), io.BytesIO(b"invalid SECRET")):
            self.client.do_action_with_exception.side_effect = [
                json.dumps(api_page([api_product()])).encode(),
            ]
            with mock.patch.object(alibabacloud_market.urllib.request, "urlopen",
                                   side_effect=[effect]):
                with self.assertRaises(InventoryError) as error:
                    alibabacloud_market.list_products()
                self.assertNotIn("SECRET", str(error.exception))

    def test_api_sdk_errors_are_sanitized(self):
        class ServerError(Exception):
            def get_error_code(self):
                return "Forbidden.RAM"

            def get_http_status(self):
                return 403

        self.client.do_action_with_exception.side_effect = ServerError("signature SECRET")
        with self.assertRaisesRegex(ValueError, "ServerError, HTTP 403, Forbidden.RAM") as error:
            alibabacloud_market.list_products()
        self.assertNotIn("SECRET", str(error.exception))

    def test_transient_api_http_errors_are_retried(self):
        class ClientException(Exception):
            def get_error_code(self):
                return "SDK.HttpError"

        self.responses([store_page([store_product()])], [])
        self.client.do_action_with_exception.side_effect = [
            ClientException("temporary"),
            json.dumps(api_page([api_product()])).encode(),
        ]
        with mock.patch.object(alibabacloud_market.time, "sleep"):
            result = alibabacloud_market.list_products()
        self.assertEqual([product.code for product in result], ["sgcmjj001"])
        self.assertEqual(self.client.do_action_with_exception.call_count, 2)


class InternationalCredentialTests(unittest.TestCase):
    def test_missing_credentials_do_not_make_network_calls(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "ALIBABA_CLOUD_ACCESS_KEY_ID"):
                alibabacloud_market._client()


if __name__ == "__main__":
    unittest.main()
