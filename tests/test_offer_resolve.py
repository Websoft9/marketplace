from __future__ import annotations

import unittest
from unittest import mock

from cli import aws_inventory, offer, offer_resolve


def product(code, title, summary="", entity_id=None):
    return offer_resolve.Product(
        code=code,
        entity_id=entity_id or f"entity-{code}",
        title=title,
        summary=summary,
    )


class MatchTests(unittest.TestCase):
    def test_real_catalog_names_match_without_maintained_aliases(self):
        identities = {
            "nextcloud": ["Nextcloud Hub"], "superset": ["Apache Superset"], "moodle": ["Moodle™"],
            "laravel": ["Laravel"], "php": ["PHP"], "postgresql": ["PostgreSQL"], "pgvector": ["pgvector"],
        }
        for title, owner, method in (
            ("Nextcloud 企业网盘与文档管理", "nextcloud", "title-app-id"),
            ("Superset 数据可视化平台", "superset", "title-apache-short-name"),
            ("Apache Superset on AWS", "superset", "title-trademark"),
            ("Moodle 在线学习平台", "moodle", "title-trademark"),
            ("Laravel-PHP 应用开发框架", "laravel", "title-trademark"),
            ("PostgreSQL 向量数据库（pgvector）", "postgresql", "title-trademark"),
            ("pgvector for PostgreSQL", "pgvector", "title-trademark"),
        ):
            with self.subTest(title=title):
                result = offer_resolve.identify_details(title, "", identities)
                self.assertEqual(result["appId"], owner)
                self.assertEqual(result["method"], method)

    def test_first_occurrence_not_catalog_order_decides(self):
        for identities in ({"laravel": ["Laravel"], "php": ["PHP"]},
                           {"php": ["PHP"], "laravel": ["Laravel"]}):
            self.assertEqual(offer_resolve.identify("Laravel-PHP", "", identities), {"laravel"})
            self.assertEqual(offer_resolve.identify("PHP for Laravel", "", identities), {"php"})
            result = offer_resolve.identify_details("Laravel-PHP", "", identities)
            self.assertEqual([(h["appId"], h["start"], h["selected"]) for h in result["evidence"]],
                             [("laravel", 0, True), ("php", 8, False)])

    def test_full_trademark_is_preferred_before_deriving_a_name_for_that_app(self):
        result = offer_resolve.identify_details("Superset then Apache Superset", "", {
            "superset": ["Apache Superset"],
        })
        self.assertEqual(result["method"], "title-trademark")
        self.assertEqual(result["evidence"][0]["name"], "apache superset")
        self.assertEqual(result["evidence"][0]["start"], 14)

    def test_other_apps_later_full_name_does_not_hide_earlier_derived_name(self):
        identities = {"superset": ["Apache Superset"], "php": ["PHP"]}
        self.assertEqual(offer_resolve.identify("Superset with PHP", "", identities), {"superset"})
        self.assertEqual(offer_resolve.identify("PHP for Apache Superset", "", identities), {"php"})

    def test_unicode_marks_whitespace_and_dashes_are_normalized_without_destroying_names(self):
        for title in ("Moodle", "Ｍｏｏｄｌｅ", "Moodle®", "Moodle™"):
            self.assertEqual(offer_resolve.identify(title, "", {"moodle": ["Moodle™"]}), {"moodle"})
        self.assertEqual(offer_resolve.identify("Ａｐａｃｈｅ　 Ｓｕｐｅｒｓｅｔ®", "", {
            "superset": ["Apache Superset"],
        }), {"superset"})
        self.assertEqual(offer_resolve.identify("Laravel—PHP", "", {
            "php": ["PHP"], "laravel": ["Laravel"],
        }), {"laravel"})
        for name in ("C++", "C#", ".NET", "Node.js"):
            result = offer_resolve.identify_details(name + " on AWS", "", {"runtime": [name]})
            self.assertEqual(result["appId"], "runtime")
            self.assertEqual(result["evidence"][0]["name"], name.casefold())

    def test_boundaries_do_not_match_names_inside_longer_words_or_identifiers(self):
        identities = {"moodle": ["Moodle™"]}
        for title in ("supermoodle", "moodlebox", "my_moodle", "moodle_v2"):
            self.assertEqual(offer_resolve.identify(title, "", identities), set())
        for title in ("Moodle中文版", "开源Moodle平台", "Moodle-on-AWS", "(Moodle)"):
            self.assertEqual(offer_resolve.identify(title, "", identities), {"moodle"})
        self.assertEqual(offer_resolve.identify("phpmyadmin", "", {"php": ["PHP"]}), set())

    def test_short_generic_ids_do_not_become_new_aliases_but_explicit_brands_still_work(self):
        for app in ("go", "r", "ai", "php", "sql", "app"):
            self.assertEqual(offer_resolve.identify(app, "", {app: ["Some Extended Product"]}), set())
        self.assertEqual(offer_resolve.identify("PHP runtime", "", {"php": ["PHP"]}), {"php"})
        self.assertEqual(offer_resolve.identify("n8n 工作流", "", {"n8n": ["n8n Automation Platform"]}), {"n8n"})
        self.assertFalse(offer_resolve.match_trademark("target", "", "Drupal"))

    def test_os_vendor_prefix_is_not_inferred_as_database_identity(self):
        identities = {"oracle": ["Oracle Database"]}
        for title in ("Oracle Linux 9 官方原版", "Oracle Linux with GNOME Desktop",
                      "Oracle Linux 官方镜像（已验证部署 Oracle 数据库）", "Oracle on AWS"):
            self.assertEqual(offer_resolve.identify(title, "", identities), set())
        self.assertEqual(offer_resolve.identify("Oracle Database on AWS", "", identities), {"oracle"})

    def test_apache_is_the_only_automatically_removed_prefix(self):
        self.assertEqual(offer_resolve.identify("Server", "", {"custom": ["Apache HTTP Server"]}), set())
        self.assertEqual(offer_resolve.identify("Superset", "", {"custom": ["Vendor Superset"]}), set())

    def test_title_id_fallback_still_precedes_summary_full_trademark(self):
        result = offer_resolve.identify_details("Nextcloud 企业网盘", "Drupal Community", {
            "nextcloud": ["Nextcloud Hub"], "drupal": ["Drupal"],
        })
        self.assertEqual(result["appId"], "nextcloud")
        self.assertEqual(result["method"], "title-app-id")

    def test_shortened_names_and_ids_also_work_in_summary_lead_only(self):
        for app, names, summary, method in (
            ("nextcloud", ["Nextcloud Hub"], "Nextcloud 34, integrates with PHP", "summary-lead-app-id"),
            ("superset", ["Apache Superset"], "Superset 6，integrates with PHP", "summary-lead-apache-short-name"),
        ):
            result = offer_resolve.identify_details("Business platform", summary, {app: names, "php": ["PHP"]})
            self.assertEqual(result["appId"], app)
            self.assertEqual(result["method"], method)
        self.assertEqual(offer_resolve.identify("Business platform", "Generic platform, Nextcloud", {
            "nextcloud": ["Nextcloud Hub"],
        }), set())

    def test_same_start_prefers_stronger_then_longer_evidence_but_exact_ties_remain(self):
        self.assertEqual(offer_resolve.identify("C++", "", {"c": ["C"], "cpp": ["C++"]}), {"cpp"})
        result = offer_resolve.identify_details("Superset", "", {
            "short": ["Apache Superset"], "exact": ["Superset"],
        })
        self.assertEqual(result["appId"], "exact")
        for identities in ({"b": ["Shared brand"], "a": ["Shared brand"]},
                           {"a": ["Shared brand"], "b": ["Shared brand"]}):
            result = offer_resolve.identify_details("Shared brand", "", identities)
            self.assertEqual(result["status"], "ambiguous")
            self.assertEqual(result["candidates"], ["a", "b"])

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
    def test_generator_and_action_resolver_share_the_same_v2_decisions(self):
        from cli import offer_generate

        identities = {
            "nextcloud": ["Nextcloud Hub"], "superset": ["Apache Superset"], "moodle": ["Moodle™"],
            "laravel": ["Laravel"], "php": ["PHP"], "postgresql": ["PostgreSQL"], "pgvector": ["pgvector"],
        }
        for title, app in (("Nextcloud 企业网盘", "nextcloud"), ("Superset 数据可视化", "superset"),
                           ("Moodle 在线学习", "moodle"), ("Laravel-PHP", "laravel"),
                           ("PostgreSQL (pgvector)", "postgresql")):
            with self.subTest(title=title):
                records = [product("code", title)]
                _, generated = offer_generate.generate(records, identities, [], platform="aliyun")
                resolved = offer_resolve.resolve({"key": app, "trademark": identities[app][0],
                                                  "multiOffers": None}, records, identities=identities)
                self.assertEqual(generated["products"][0]["appMatch"], resolved["targets"][0]["appMatch"])
                self.assertEqual(resolved["targets"][0]["appMatch"]["appId"], app)
                self.assertEqual(resolved["targets"][0]["identityRulesVersion"],
                                 "deterministic-first-appearance-v2")

    def test_later_application_is_not_implicitly_selected_by_action(self):
        bundle = {"key": "laravel", "trademark": "Laravel", "multiOffers": None}
        records = [product("code", "PHP for Laravel")]
        identities = {"php": ["PHP"], "laravel": ["Laravel"]}
        with self.assertRaisesRegex(ValueError, "no AWS product matches"):
            offer_resolve.resolve(bundle, records, identities=identities)
        explicit = offer_resolve.resolve(bundle, records, code="code", identities=identities)
        self.assertEqual(explicit["targets"][0]["match"], "override")
        self.assertEqual(explicit["targets"][0]["appMatch"]["appId"], "php")

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

    def test_multiple_title_owners_choose_first_appearance(self):
        identities = {"odoo": ["Odoo"], "drupal": ["Drupal"]}
        products = [product("bundle", "Odoo and Drupal on AWS")]
        automatic = offer_resolve.resolve(self.bundle(None), products, identities=identities)
        self.assertEqual(automatic["targets"][0]["match"], "app")
        self.assertEqual(automatic["targets"][0]["appMatch"]["appId"], "odoo")
        result = offer_resolve.resolve(
            self.bundle(None), products, code="bundle", identities=identities
        )
        self.assertEqual(result["targets"][0]["match"], "app")

    def test_identical_identity_at_identical_position_still_needs_explicit_override(self):
        identities = {"odoo": ["Shared brand"], "drupal": ["Shared brand"]}
        products = [product("bundle", "Shared brand on AWS")]
        with self.assertRaisesRegex(ValueError, "ambiguous application"):
            offer_resolve.resolve(self.bundle(None), products, identities=identities)
        result = offer_resolve.resolve(self.bundle(None), products, code="bundle", identities=identities)
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
        with mock.patch.object(aws_inventory, "aws_client", return_value=client):
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
        with mock.patch.object(aws_inventory, "aws_client", return_value=client):
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
        with mock.patch.object(aws_inventory, "aws_client", return_value=client):
            self.assertEqual(offer_resolve.list_aws_products(public_only=True), [])

    def test_batch_errors_or_missing_details_block_partial_inventory(self):
        for response in ({"Errors": {"prod-a": {"ErrorCode": "AccessDenied"}}},
                         {"EntityDetails": {}}):
            client = mock.Mock()
            client.list_entities.return_value = {"EntitySummaryList": [{"EntityId": "prod-a"}]}
            client.batch_describe_entities.return_value = response
            with mock.patch.object(aws_inventory, "aws_client", return_value=client):
                with self.assertRaisesRegex(ValueError, "inventory is incomplete"):
                    offer_resolve.list_aws_products()

    def test_missing_entity_id_blocks_incomplete_inventory(self):
        client = mock.Mock()
        client.list_entities.return_value = {"EntitySummaryList": [{}]}
        with mock.patch.object(aws_inventory, "aws_client", return_value=client):
            with self.assertRaisesRegex(ValueError, "missing entity ID"):
                offer_resolve.list_aws_products()
        client.batch_describe_entities.assert_not_called()

    def test_incomplete_inventory_fails_not_returns_partial(self):
        client = mock.Mock()
        client.list_entities.return_value = {"EntitySummaryList": [], "NextToken": "same"}
        with mock.patch.object(aws_inventory, "aws_client", return_value=client):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                offer_resolve.list_aws_products()

    def test_api_failure_is_not_an_empty_inventory(self):
        client = mock.Mock()
        client.list_entities.side_effect = RuntimeError("network unavailable")
        with mock.patch.object(aws_inventory, "aws_client", return_value=client):
            with self.assertRaisesRegex(ValueError, "list_entities failed"):
                offer_resolve.list_aws_products()


if __name__ == "__main__":
    unittest.main()
