from __future__ import annotations

import unittest
from unittest import mock

from cli import offer, offer_resolve


def product(code, title, summary="", entity_id=None):
    return offer_resolve.Product(
        code=code,
        entity_id=entity_id or f"entity-{code}",
        title=title,
        summary=summary,
    )


class MatchTests(unittest.TestCase):
    def test_title_owner_blocks_summary_owner(self):
        identities = {"odoo": ["Odoo"], "drupal": ["Drupal"]}
        self.assertEqual(
            offer_resolve.identify("Drupal on AWS", "Integrates with Odoo", identities),
            {"drupal"},
        )

    def test_summary_only_uses_first_comma_clause(self):
        identities = {"odoo": ["Odoo"], "drupal": ["Drupal"]}
        for separator in (",", "，"):
            self.assertEqual(offer_resolve.identify(
                "Business suite", f"Odoo Community{separator} integrates with Drupal", identities
            ), {"odoo"})
            self.assertEqual(offer_resolve.identify(
                "Business suite", f"Business software{separator} integrates with Odoo", identities
            ), set())

    def test_multisite_capability_is_not_a_label(self):
        for summary in ("Supports Multisite", "WordPress, supports Multisite"):
            self.assertEqual(offer_resolve.detect_offer(
                "WordPress on AWS", summary, ["", "Multisite"]
            ), "")

    def test_trademark_matches_title_and_summary(self):
        self.assertTrue(offer_resolve.match_trademark("Odoo on Ubuntu", "", "Odoo"))
        self.assertTrue(
            offer_resolve.match_trademark("Business suite", "Odoo Community", "Odoo")
        )
        self.assertFalse(
            offer_resolve.match_trademark("Odoo clone", "not the vendor", "Drupal")
        )

    def test_detect_os_uses_family_names(self):
        self.assertEqual(
            offer_resolve.detect_os("Odoo on Ubuntu 26.04"), ["Ubuntu"]
        )
        self.assertEqual(
            offer_resolve.detect_os("Deploy on Debian 13"), ["Debian"]
        )
        self.assertEqual(offer_resolve.detect_os("Odoo on AWS"), [])

    def test_detect_offer_composes_os_and_label(self):
        offers = ["", "Ubuntu", "Ubuntu; Multisite"]
        self.assertEqual(
            offer_resolve.detect_offer(
                "WordPress Multisite on Ubuntu 26.04", "", offers
            ),
            "Ubuntu; Multisite",
        )
        self.assertEqual(
            offer_resolve.detect_offer("WordPress Multisite on AWS", "", offers),
            "Multisite",
        )

    def test_variant_key_is_order_insensitive(self):
        self.assertEqual(
            offer_resolve.variant_key("Multisite; Ubuntu"),
            offer_resolve.variant_key("Ubuntu; Multisite"),
        )
        self.assertEqual(offer_resolve.variant_key(""), frozenset())


class ResolveTests(unittest.TestCase):
    def bundle(self, multi_offers):
        return {
            "key": "odoo",
            "trademark": "Odoo",
            "multiOffers": multi_offers,
        }

    def test_app_only_updates_each_os_variant(self):
        products = [
            product("u", "Odoo on Ubuntu 26.04"),
            product("d", "Odoo on Debian 13"),
        ]
        document = offer_resolve.resolve(
            self.bundle(["Ubuntu", "Debian"]), products
        )
        self.assertEqual(
            [(target["code"], target["offer"]) for target in document["targets"]],
            [("u", "Ubuntu"), ("d", "Debian")],
        )

    def test_unrelated_title_is_never_an_implicit_override(self):
        with self.assertRaisesRegex(ValueError, "no AWS product matches"):
            offer_resolve.resolve(
                self.bundle(None),
                [product("d", "Drupal on AWS", "Odoo integration")],
                identities={"odoo": ["Odoo"], "drupal": ["Drupal"]},
            )

    def test_multiple_title_owners_need_explicit_code(self):
        identities = {"odoo": ["Odoo"], "drupal": ["Drupal"]}
        products = [product("bundle", "Odoo and Drupal on AWS")]
        with self.assertRaisesRegex(ValueError, "ambiguous application"):
            offer_resolve.resolve(self.bundle(None), products, identities=identities)
        result = offer_resolve.resolve(
            self.bundle(None), products, code="bundle", identities=identities
        )
        self.assertEqual(result["targets"][0]["match"], "override")

    def test_no_multi_offers_disallows_all_explicit_choices(self):
        for selected in ("Multisite", "Ubuntu", "base"):
            for code in (None, "u"):
                with self.subTest(offer=selected, code=code):
                    with self.assertRaisesRegex(ValueError, "not declared"):
                        offer_resolve.resolve(
                            self.bundle(None), [product("u", "Odoo on AWS")],
                            code=code, offer=selected,
                        )

    def test_duplicate_explicit_code_is_ambiguous(self):
        with self.assertRaisesRegex(ValueError, "cannot distinguish"):
            offer_resolve.resolve(self.bundle(None), [
                product("same", "Odoo on Ubuntu", entity_id="prod-a"),
                product("same", "Odoo on Debian", entity_id="prod-b"),
            ], code="same")

    def test_app_list_is_rejected(self):
        bundle = self.bundle(None)
        bundle["key"] = "odoo,wordpress"
        with self.assertRaisesRegex(ValueError, "exactly one"):
            offer_resolve.resolve(bundle, [])

    def test_same_os_with_two_codes_is_ambiguous(self):
        products = [
            product("u1", "Odoo on Ubuntu 26.04"),
            product("u2", "Odoo on Ubuntu 24.04"),
        ]
        with self.assertRaisesRegex(ValueError, "cannot distinguish"):
            offer_resolve.resolve(self.bundle(["Ubuntu"]), products)

    def test_code_is_targeted_and_mismatch_is_an_override(self):
        products = [
            product("u", "Odoo on Ubuntu 26.04"),
            product("x", "Drupal on Ubuntu 26.04"),
        ]
        matched = offer_resolve.resolve(
            self.bundle(["Ubuntu"]), products, code="u"
        )
        self.assertEqual(matched["targets"][0]["match"], "app")
        overridden = offer_resolve.resolve(
            self.bundle(["Ubuntu"]), products, code="x"
        )
        self.assertEqual(overridden["targets"][0]["match"], "override")

    def test_missing_code_fails(self):
        with self.assertRaisesRegex(ValueError, "was not found"):
            offer_resolve.resolve(self.bundle(["Ubuntu"]), [], code="missing")

    def test_offer_filters_to_one_variant(self):
        products = [
            product("u", "Odoo on Ubuntu 26.04"),
            product("d", "Odoo on Debian 13"),
        ]
        document = offer_resolve.resolve(
            self.bundle(["Ubuntu", "Debian"]), products, offer="Debian"
        )
        self.assertEqual(len(document["targets"]), 1)
        self.assertEqual(document["targets"][0]["code"], "d")

    def test_requested_offer_uses_declared_spelling(self):
        document = offer_resolve.resolve(
            self.bundle(["Ubuntu"]),
            [product("u", "Odoo on Ubuntu 26.04")],
            offer="ubuntu",
        )
        self.assertEqual(document["targets"][0]["offer"], "Ubuntu")

    def test_code_and_offer_force_the_variant(self):
        products = [product("u", "Odoo on Ubuntu 26.04")]
        document = offer_resolve.resolve(
            self.bundle(["Ubuntu", "Debian"]), products, code="u", offer="Debian"
        )
        self.assertEqual(document["targets"][0]["offer"], "Debian")

    def test_undeclared_offer_fails(self):
        with self.assertRaisesRegex(ValueError, "not declared"):
            offer_resolve.resolve(
                self.bundle(["Ubuntu"]),
                [product("u", "Odoo on Ubuntu 26.04")],
                offer="Debian",
            )

    def test_label_offer_updates_labelled_and_base_products(self):
        bundle = {
            "key": "wordpress",
            "trademark": "WordPress",
            "multiOffers": ["", "Multisite"],
        }
        products = [
            product("base", "WordPress on AWS"),
            product("multi", "WordPress Multisite on AWS"),
        ]
        document = offer_resolve.resolve(bundle, products)
        self.assertEqual(
            [(target["code"], target["offer"]) for target in document["targets"]],
            [("base", ""), ("multi", "Multisite")],
        )

    def test_label_offer_not_declared_for_app_fails(self):
        with self.assertRaisesRegex(ValueError, "not declared"):
            offer_resolve.resolve(
                self.bundle(["Ubuntu"]),
                [product("u", "Odoo on Ubuntu 26.04")],
                offer="Multisite",
            )

    def test_no_trademark_match_fails(self):
        with self.assertRaisesRegex(ValueError, "no AWS product matches"):
            offer_resolve.resolve(
                self.bundle(["Ubuntu"]), [product("d", "Drupal on Ubuntu")]
            )

    def test_missing_declared_offer_is_reported(self):
        products = [product("u", "Odoo on Ubuntu 26.04")]
        document = offer_resolve.resolve(
            self.bundle(["Ubuntu", "Debian"]), products
        )
        self.assertEqual(
            [item["offer"] for item in document["unresolved"]], ["Debian"]
        )

    def test_detected_offer_not_declared_fails(self):
        products = [product("u", "Odoo on Ubuntu 26.04")]
        with self.assertRaisesRegex(ValueError, "not declared"):
            offer_resolve.resolve(self.bundle(["Debian"]), products)


class TargetRoundTripTests(unittest.TestCase):
    def test_listing_targets_preserves_offer(self):
        item = {
            "targets": [
                {"code": "u", "entityId": "entity-u", "offer": "Ubuntu"},
                {"code": "d", "entityId": "entity-d", "offer": ""},
            ]
        }
        self.assertEqual(
            offer.listing_targets(item),
            [
                {"code": "u", "entityId": "entity-u", "offer": "Ubuntu"},
                {"code": "d", "entityId": "entity-d", "offer": ""},
            ],
        )


class InventoryTests(unittest.TestCase):
    def test_full_inventory_reads_every_page_and_describes_products(self):
        client = mock.Mock()
        client.list_entities.side_effect = [
            {"EntitySummaryList": [{"EntityId": "prod-a", "AmiProductSummary": {"Visibility": "Public"}}], "NextToken": "page2"},
            {"EntitySummaryList": [{"EntityId": "prod-b"}]},
        ]
        client.batch_describe_entities.return_value = {"EntityDetails": {
            "prod-a": {"DetailsDocument": {"Description": {"Sku": "sku-a", "ProductCode": "code-a", "ProductTitle": "Odoo", "ShortDescription": "ERP"}}},
            "prod-b": {"Details": '{"Description":{"Sku":"sku-b","ProductTitle":"Drupal"}}'},
        }}
        with mock.patch.object(offer_resolve, "aws_client", return_value=client):
            products = offer_resolve.list_aws_products()
        self.assertEqual([p.code for p in products], ["code-a", ""])
        self.assertEqual(products[0].alternate_code, "sku-a")
        self.assertEqual(products[1].alternate_code, "sku-b")
        self.assertEqual(products[0].visibility, "Public")
        self.assertEqual(client.list_entities.call_args_list[1].kwargs["NextToken"], "page2")
        client.describe_entity.assert_not_called()
        self.assertEqual(client.batch_describe_entities.call_args.kwargs, {
            "EntityRequestList": [
                {"Catalog": "AWSMarketplace", "EntityId": "prod-a"},
                {"Catalog": "AWSMarketplace", "EntityId": "prod-b"},
            ],
        })

    def test_public_inventory_filters_before_describing_and_batches_after_all_pages(self):
        client = mock.Mock()
        events = []

        def list_page(**kwargs):
            events.append("list")
            if kwargs.get("NextToken"):
                return {"EntitySummaryList": [{
                    "EntityId": "public-20", "AmiProductSummary": {"Visibility": "Public"},
                }]}
            return {"EntitySummaryList": [
                {"EntityId": f"public-{i}", "AmiProductSummary": {"Visibility": "Public"}}
                for i in range(20)
            ] + [
                {"EntityId": state or "unknown", "AmiProductSummary": {"Visibility": state}}
                for state in ("Restricted", "Limited", "Draft", "")
            ], "NextToken": "page2"}

        def describe_batch(**kwargs):
            events.append("batch")
            self.assertNotIn("Catalog", kwargs)
            return {"EntityDetails": {
                row["EntityId"]: {"DetailsDocument": {"Description": {
                    "ProductCode": "qy0c6rqyf152lwjyxlpjf51q", "Sku": "odoo_ubuntu",
                }}} for row in kwargs["EntityRequestList"]
            }}

        client.list_entities.side_effect = list_page
        client.batch_describe_entities.side_effect = describe_batch
        progress = mock.Mock()
        with mock.patch.object(offer_resolve, "aws_client", return_value=client):
            products = offer_resolve.list_aws_products(public_only=True, progress=progress)
        self.assertEqual(events, ["list", "list", "batch", "batch"])
        self.assertEqual(len(products), 21)
        self.assertTrue(all(p.visibility == "Public" for p in products))
        self.assertEqual([len(c.kwargs["EntityRequestList"])
                          for c in client.batch_describe_entities.call_args_list], [20, 1])
        for call in client.list_entities.call_args_list:
            self.assertEqual(call.kwargs["EntityTypeFilters"], {
                "AmiProductFilters": {"Visibility": {"ValueList": ["Public"]}},
            })
        self.assertEqual(progress.call_count, 3)

    def test_visibility_changed_to_restricted_is_excluded(self):
        client = mock.Mock()
        client.list_entities.return_value = {"EntitySummaryList": [{
            "EntityId": "prod-a", "AmiProductSummary": {"Visibility": "Public"},
        }]}
        client.batch_describe_entities.return_value = {"EntityDetails": {
            "prod-a": {"DetailsDocument": {"Description": {
                "Visibility": "Restricted", "ProductCode": "code-a",
            }}},
        }}
        with mock.patch.object(offer_resolve, "aws_client", return_value=client):
            self.assertEqual(offer_resolve.list_aws_products(public_only=True), [])

    def test_batch_errors_or_missing_details_block_partial_inventory(self):
        for response in ({"Errors": {"prod-a": {"ErrorCode": "AccessDenied"}}},
                         {"EntityDetails": {}}):
            client = mock.Mock()
            client.list_entities.return_value = {"EntitySummaryList": [{"EntityId": "prod-a"}]}
            client.batch_describe_entities.return_value = response
            with mock.patch.object(offer_resolve, "aws_client", return_value=client):
                with self.assertRaisesRegex(ValueError, "inventory is incomplete"):
                    offer_resolve.list_aws_products()

    def test_missing_entity_id_blocks_incomplete_inventory(self):
        client = mock.Mock()
        client.list_entities.return_value = {"EntitySummaryList": [{}]}
        with mock.patch.object(offer_resolve, "aws_client", return_value=client):
            with self.assertRaisesRegex(ValueError, "missing entity ID"):
                offer_resolve.list_aws_products()
        client.batch_describe_entities.assert_not_called()

    def test_incomplete_inventory_fails_not_returns_partial(self):
        client = mock.Mock()
        client.list_entities.return_value = {"EntitySummaryList": [], "NextToken": "same"}
        with mock.patch.object(offer_resolve, "aws_client", return_value=client):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                offer_resolve.list_aws_products()

    def test_api_failure_is_not_an_empty_inventory(self):
        client = mock.Mock()
        client.list_entities.side_effect = RuntimeError("network unavailable")
        with mock.patch.object(offer_resolve, "aws_client", return_value=client):
            with self.assertRaisesRegex(ValueError, "list_entities failed"):
                offer_resolve.list_aws_products()


if __name__ == "__main__":
    unittest.main()
