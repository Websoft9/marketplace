from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from cli import alibabacloud_market
from cli.store_models import InventoryError


def product(code="sgcmjj000", **updates):
    return {"productInfo": {
        "code": code, "title": "Unknown platform", "describe": "Full short description",
        "companyName": "Websoft9", "deliveryMethod": "Image", "shopUrl": "/store/2116499.html",
        "detailUrl": f"/products/56718003/{code}.html", **updates,
    }}


def page(rows, current=1, total=None):
    total = len(rows) if total is None else total
    return {"success": True, "result": {
        "product": rows, "totalRecords": total, "curPage": current,
        "totalPage": (total + 19) // 20,
    }}


class InternationalStoreTests(unittest.TestCase):
    def fetch(self, responses):
        return mock.patch.object(alibabacloud_market.urllib.request, "urlopen", side_effect=[
            io.BytesIO(json.dumps(r).encode()) for r in responses
        ])

    def test_all_pages_and_only_verified_store_image_products(self):
        rows = [product(f"sgcmjj{i:03}") for i in range(19)]
        rows += [product("service", deliveryMethod="Service"), product("foreign", companyName="Other")]
        with self.fetch([page(rows[:20], total=21), page(rows[20:], current=2, total=21)]) as opener:
            result = alibabacloud_market.list_products()
        self.assertEqual(len(result), 19)
        self.assertEqual(result[0].summary, "Full short description")
        self.assertEqual(result[0].visibility, "StoreVisible")
        self.assertIn("/56718003/sgcmjj000.html", result[0].url)
        self.assertEqual(opener.call_count, 2)
        self.assertIn("curPage=2", opener.call_args_list[1].args[0].full_url)
        self.assertTrue(all("publisherId=2116499" in c.args[0].full_url for c in opener.call_args_list))

    def test_complete_empty_store(self):
        with self.fetch([page([])]):
            self.assertEqual(alibabacloud_market.list_products(), [])

    def test_bad_store_responses_fail_closed(self):
        good = page([product()])
        cases = [
            [], {"success": False}, {"success": True, "result": []},
            {**good, "result": {**good["result"], "totalRecords": True}},
            {**good, "result": {**good["result"], "totalRecords": 2}},
            {**good, "result": {**good["result"], "curPage": 2}},
            {**good, "result": {**good["result"], "totalPage": 8}},
            page([None]), page([{"productInfo": None}]),
            page([product("")]), page([product(), product()]),
            page([product(shopUrl="/store/other.html")]),
            page([product(detailUrl="https://untrusted.example/signed-url")]),
        ]
        for response in cases:
            with self.subTest(response=response), self.fetch([response]):
                with self.assertRaises(InventoryError):
                    alibabacloud_market.list_products()

    def test_no_partial_data_when_total_changes_or_a_page_repeats(self):
        rows = [product(f"sgcmjj{i}") for i in range(20)]
        for second in (page([product("last")], current=2, total=22),
                       page([product("sgcmjj0")], current=2, total=21)):
            with self.fetch([page(rows, total=21), second]):
                with self.assertRaises(InventoryError):
                    alibabacloud_market.list_products()

    def test_network_and_json_errors_never_expose_response_or_url(self):
        for effect in (RuntimeError("SECRET signed response"), io.BytesIO(b"invalid SECRET")):
            with mock.patch.object(alibabacloud_market.urllib.request, "urlopen", side_effect=[effect]):
                with self.assertRaises(InventoryError) as error:
                    alibabacloud_market.list_products()
                self.assertNotIn("SECRET", str(error.exception))


if __name__ == "__main__":
    unittest.main()
