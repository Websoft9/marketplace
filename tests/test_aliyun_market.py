from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from cli import aliyun_market


def store_product(code="cmjj001", **updates):
    return {
        "productcode": code, "shop_name": aliyun_market.SUPPLIER_NAME,
        "supplierid": 228399, "delivery_method": "镜像", "hiddenForCloudMarket": 0,
        **updates,
    }


def api_product(code="cmjj001", **updates):
    return {
        "Code": code, "Name": "Odoo 企业 ERP", "ShortDescription": "Odoo Community, ERP",
        "SupplierName": aliyun_market.SUPPLIER_NAME, "SupplierId": 228399,
        "DeliveryWay": "镜像", **updates,
    }


def store_page(rows, page=1, total=None):
    total = len(rows) if total is None else total
    return {"success": True, "result": {
        "products": rows, "totalRecords": total,
        "totalPage": (total + 49) // 50, "curPage": page,
    }}


def api_page(rows, page=1, total=None):
    return {
        "PageNumber": page, "PageSize": 50,
        "TotalCount": len(rows) if total is None else total,
        "ProductItems": {"ProductItem": rows},
    }


class AliyunInventoryTests(unittest.TestCase):
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
        patcher = mock.patch.object(aliyun_market, "_client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(aliyun_market.urllib.request, "urlopen")
        self.opener = patcher.start()
        self.addCleanup(patcher.stop)

    def responses(self, stores, apis):
        self.opener.side_effect = [io.BytesIO(json.dumps(p).encode()) for p in stores]
        self.client.do_action_with_exception.side_effect = [json.dumps(p).encode() for p in apis]

    def test_only_visible_supplier_images_are_returned_without_detail_requests(self):
        stores = [
            store_product(), store_product("hidden", hiddenForCloudMarket=1),
            store_product("service", delivery_method="人工服务"),
            store_product("foreign", shop_name="其他公司"),
            store_product("wrong-store", supplierid=123),
        ]
        apis = [api_product(row["productcode"]) for row in stores]
        apis += [api_product("not-in-store")]
        self.responses([store_page(stores)], [api_page(apis)])
        progress = mock.Mock()
        products = aliyun_market.list_aliyun_products(progress=progress)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].code, "cmjj001")
        self.assertEqual(products[0].entity_id, "cmjj001")
        self.assertEqual(products[0].title, "Odoo 企业 ERP")
        self.assertEqual(products[0].summary, "Odoo Community, ERP")
        self.assertEqual(products[0].visibility, "StoreVisible")
        self.requests[0].set_Filters.assert_called_once_with([
            {"Key": "productType", "Value": "MIRROR"},
            {"Key": "aliyunUid", "Value": aliyun_market.SUPPLIER_UID},
        ])
        self.requests[0].set_accept_format.assert_called_once_with("json")
        self.client.do_action_with_exception.assert_called_once()
        self.assertEqual(progress.call_count, 3)

    def test_every_store_and_api_page_is_read(self):
        rows = [store_product(f"cmjj{i:03}") for i in range(51)]
        apis = [api_product(row["productcode"]) for row in rows]
        self.responses(
            [store_page(rows[:50], total=51), store_page(rows[50:], page=2, total=51)],
            [api_page(apis[:50], total=51), api_page(apis[50:], page=2, total=51)],
        )
        products = aliyun_market.list_aliyun_products()
        self.assertEqual(len(products), 51)
        self.assertIn("pageIndex=2", self.opener.call_args_list[1].args[0].full_url)
        self.requests[1].set_PageNumber.assert_called_once_with(2)
        self.assertTrue(all(r.set_PageSize.call_args.args == (50,) for r in self.requests))

    def test_singleton_and_result_wrapped_response(self):
        response = api_page([])
        response.update(TotalCount=1, ProductItems={"ProductItem": api_product()})
        self.responses([store_page([store_product()])], [{"Result": response}])
        self.assertEqual(aliyun_market.list_aliyun_products()[0].code, "cmjj001")

    def test_store_network_errors_are_sanitized_after_api_inventory_is_read(self):
        self.responses([], [api_page([api_product()])])
        self.opener.side_effect = RuntimeError("signed request SECRET")
        with self.assertRaisesRegex(ValueError, "visibility unverified") as error:
            aliyun_market.list_aliyun_products()
        self.assertNotIn("SECRET", str(error.exception))
        self.client.do_action_with_exception.assert_called_once()

    def test_store_malformed_data_fails_closed(self):
        good = store_page([store_product()])
        cases = [
            {"success": False}, {"success": True, "result": []},
            {**good, "result": {**good["result"], "totalPage": 3}},
            {**good, "result": {**good["result"], "curPage": 2}},
            {**good, "result": {**good["result"], "totalRecords": 2}},
            {**good, "result": {**good["result"], "products": [None]}},
            store_page([store_product(hiddenForCloudMarket=None)]),
            store_page([store_product(hiddenForCloudMarket=False)]),
            store_page([store_product("")]),
            store_page([store_product(), store_product()]),
        ]
        for response in cases:
            with self.subTest(response=response):
                self.responses([response], [api_page([api_product()])])
                with self.assertRaises(ValueError):
                    aliyun_market.list_aliyun_products()

    def test_store_total_changes_block_generation(self):
        rows = [store_product(f"cmjj{i}") for i in range(50)]
        self.responses([store_page(rows, total=51),
                        store_page([store_product("last")], page=2, total=52)], [api_page([])])
        with self.assertRaises(ValueError):
            aliyun_market.list_aliyun_products()

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
                    aliyun_market.list_aliyun_products()
                self.assertNotIn("SECRET", str(error.exception))

    def test_api_error_does_not_expose_signed_details(self):
        self.responses([store_page([store_product()])], [])
        self.client.do_action_with_exception.side_effect = RuntimeError("signature SECRET")
        with self.assertRaisesRegex(ValueError, "DescribeProducts failed") as error:
            aliyun_market.list_aliyun_products()
        self.assertNotIn("SECRET", str(error.exception))

    def test_visible_products_missing_from_supplier_api_block_write(self):
        for updates in ({"SupplierName": "other"}, {"SupplierId": 123},
                        {"DeliveryWay": "人工服务"}):
            self.responses([store_page([store_product()])], [api_page([api_product(**updates)])])
            with self.assertRaisesRegex(ValueError, "missing from the supplier API"):
                aliyun_market.list_aliyun_products()

    def test_api_repeated_page_blocks_partial_results(self):
        rows = [api_product(f"cmjj{i}") for i in range(50)]
        self.responses([store_page([])], [
            api_page(rows, total=51), api_page([api_product("last")], page=2, total=51),
        ])
        # A stable total with a repeated page must not be accepted as completion.
        repeated = api_page([api_product("cmjj0")], page=2, total=51)
        self.client.do_action_with_exception.side_effect = [
            json.dumps(api_page(rows, total=51)), json.dumps(repeated),
        ]
        with self.assertRaisesRegex(ValueError, "repeated product code"):
            aliyun_market.list_aliyun_products()

    def test_api_total_changes_block_partial_results(self):
        rows = [api_product(f"cmjj{i}") for i in range(50)]
        self.responses([store_page([])], [
            api_page(rows, total=51), api_page([api_product("last")], page=2, total=52),
        ])
        with self.assertRaisesRegex(ValueError, "changed during pagination"):
            aliyun_market.list_aliyun_products()

    def test_invalid_json_responses_are_sanitized(self):
        self.responses([], [api_page([])])
        self.opener.side_effect = [io.BytesIO(b"invalid SECRET")]
        with self.assertRaisesRegex(ValueError, "invalid JSON") as error:
            aliyun_market.list_aliyun_products()
        self.assertNotIn("SECRET", str(error.exception))

    def test_empty_complete_inventories_return_empty(self):
        self.responses([store_page([])], [api_page([])])
        self.assertEqual(aliyun_market.list_aliyun_products(), [])

    def test_api_is_read_first_and_store_never_overrides_title_or_summary(self):
        events = []
        row = api_product(Name="API title", ShortDescription="API description")

        def api(request):
            events.append("api")
            return json.dumps(api_page([row])).encode()

        def frontend(*args, **kwargs):
            events.append("store")
            return io.BytesIO(json.dumps(store_page([
                store_product(name="Different store title", short_description="Different summary"),
            ])).encode())

        self.client.do_action_with_exception.side_effect = api
        self.opener.side_effect = frontend
        result = aliyun_market.list_aliyun_products()
        self.assertEqual(events, ["api", "store"])
        self.assertEqual(result[0].title, "API title")
        self.assertEqual(result[0].summary, "API description")


class AliyunCredentialTests(unittest.TestCase):
    def test_missing_credentials_do_not_make_network_calls(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "ALIYUN_ACCESS_KEY_ID"):
                aliyun_market._client()

    def test_ak_and_sts_credentials_are_supported(self):
        for token in ("", "test-token"):
            with self.subTest(sts=bool(token)):
                client = mock.Mock()
                credential = mock.Mock()
                modules = {
                    "aliyunsdkcore.client": SimpleNamespace(AcsClient=client),
                    "aliyunsdkcore.auth.credentials": SimpleNamespace(StsTokenCredential=credential),
                }
                with mock.patch.dict("sys.modules", modules), mock.patch.dict("os.environ", {
                    "ALIYUN_ACCESS_KEY_ID": "test-id",
                    "ALIYUN_ACCESS_KEY_SECRET": "test-secret",
                    "ALIYUN_SECURITY_TOKEN": token,
                }, clear=True):
                    aliyun_market._client()
                if token:
                    credential.assert_called_once_with(
                        sts_access_key_id="test-id", sts_access_key_secret="test-secret",
                        sts_token="test-token",
                    )
                    self.assertIs(client.call_args.kwargs["credential"], credential.return_value)
                else:
                    client.assert_called_once_with("test-id", "test-secret", "cn-hangzhou", timeout=30)


if __name__ == "__main__":
    unittest.main()
