from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cli import contentful, contentful_management as cma  # noqa: E402
from cli.cli import cli  # noqa: E402

SCHEMA = {
    "key": {"type": "Symbol", "localized": False},
    "trademark": {"type": "Symbol", "localized": True},
    "overview": {"type": "Text", "localized": True},
    "keywords": {"type": "Array", "localized": True, "items": {"type": "Symbol"}},
    "useCases": {"type": "Array", "localized": False, "items": {"type": "Symbol"}},
    "relatedApps": {"type": "Array", "localized": False, "items": {"type": "Link", "linkType": "Entry"}},
    "summary": {"type": "Symbol", "localized": True},
    "hasenterprise": {"type": "Boolean", "localized": False},
    "other": {"type": "Symbol", "localized": False},
}
ENTRY = {
    "sys": {"id": "entry-1", "version": 3},
    "fields": {
        "key": {"en-US": "wordpress"},
        "trademark": {"en-US": "Old WP", "zh-CN": "旧"},
        "overview": {"en-US": "Old overview"},
        "keywords": {"en-US": ["old"]},
        "useCases": {"en-US": ["old"]},
        "relatedApps": {"en-US": [{"sys": {"type": "Link", "linkType": "Entry", "id": "old-link"}}]},
        "summary": {"en-US": "Old summary"},
        "hasenterprise": {"en-US": False},
        "other": "keep",
    },
}
ITEM = {
    "id": "wordpress",
    "trademark": "WordPress",
    "overview": "New overview",
    "keywords": ["site", "cms"],
    "usecases": ["publishing"],
    "relatedApps": ["PostgreSQL", "pgAdmin"],
    "summary": "New summary",
    "hasenterprise": True,
}
RELATED_APP_IDS = {"PostgreSQL": "pg-entry", "pgAdmin": "pgadmin-entry"}
RELATED_PRODUCTS = [
    {"sys": {"id": "pg-entry"}, "fields": {"trademark": {"en-US": "PostgreSQL"}}},
    {"sys": {"id": "pgadmin-entry"}, "fields": {"trademark": {"en-US": "pgAdmin"}}},
]


class BuildChangesTests(unittest.TestCase):
    def test_merges_only_target_locale_and_preserves_other_fields(self) -> None:
        fields, changed, skipped, unchanged = cma.build_changes(
            ITEM, ENTRY, SCHEMA, related_app_ids=RELATED_APP_IDS,
            allowed_related_apps=set(RELATED_APP_IDS))
        self.assertEqual(changed, ["trademark", "overview", "keywords", "usecases", "relatedApps", "summary", "hasenterprise"])
        self.assertEqual(skipped, [])
        self.assertEqual(unchanged, [])
        self.assertEqual(fields["trademark"], {"en-US": "WordPress", "zh-CN": "旧"})
        self.assertEqual(fields["overview"], {"en-US": "New overview"})
        self.assertEqual(fields["keywords"], {"en-US": ["site", "cms"]})
        self.assertEqual(fields["useCases"], {"en-US": ["publishing"]})
        self.assertEqual(fields["relatedApps"], {"en-US": [
            {"sys": {"type": "Link", "linkType": "Entry", "id": "pg-entry"}},
            {"sys": {"type": "Link", "linkType": "Entry", "id": "pgadmin-entry"}},
        ]})
        self.assertEqual(fields["summary"], {"en-US": "New summary"})
        self.assertEqual(fields["hasenterprise"], {"en-US": True})
        self.assertEqual(fields["other"], "keep")
        self.assertEqual(fields["key"], {"en-US": "wordpress"})

    def test_empty_values_are_skipped_or_cleared(self) -> None:
        item = {"id": "wordpress", "trademark": "", "keywords": [], "summary": None}
        fields, changed, skipped, unchanged = cma.build_changes(item, ENTRY, SCHEMA)
        self.assertEqual(changed, [])
        self.assertEqual(skipped, ["trademark", "keywords", "summary"])
        self.assertEqual(fields, ENTRY["fields"])

        fields, changed, skipped, unchanged = cma.build_changes(item, ENTRY, SCHEMA, clear=True)
        self.assertEqual(changed, ["trademark", "keywords", "summary"])
        self.assertEqual(skipped, [])
        self.assertEqual(fields["trademark"]["en-US"], "")
        self.assertEqual(fields["keywords"]["en-US"], [])
        self.assertEqual(fields["summary"]["en-US"], "")

    def test_unchanged_values_are_not_written(self) -> None:
        item = {"id": "wordpress", "trademark": "Old WP"}
        fields, changed, skipped, unchanged = cma.build_changes(item, ENTRY, SCHEMA)
        self.assertEqual(changed, [])
        self.assertEqual(unchanged, ["trademark"])
        self.assertEqual(fields["trademark"]["zh-CN"], "旧")

    def test_unknown_or_mistyped_input_is_rejected(self) -> None:
        with self.assertRaisesRegex(contentful.ContentfulError, "Unsupported input field"):
            cma.build_changes({"id": "wordpress", "tags": ["x"]}, ENTRY, SCHEMA)
        with self.assertRaisesRegex(contentful.ContentfulError, "array of strings"):
            cma.build_changes({"id": "wordpress", "keywords": "not-a-list"}, ENTRY, SCHEMA)
        with self.assertRaisesRegex(contentful.ContentfulError, "must be a string"):
            cma.build_changes({"id": "wordpress", "summary": ["bad"]}, ENTRY, SCHEMA)
        with self.assertRaisesRegex(contentful.ContentfulError, "must be a boolean"):
            cma.build_changes({"id": "wordpress", "hasenterprise": "yes"}, ENTRY, SCHEMA)
        with self.assertRaisesRegex(contentful.ContentfulError, "array of application Trademarks"):
            cma.build_changes({"id": "wordpress", "relatedApps": "PostgreSQL"}, ENTRY, SCHEMA,
                              related_app_ids=RELATED_APP_IDS, allowed_related_apps=set(RELATED_APP_IDS))
        with self.assertRaisesRegex(contentful.ContentfulError, "not found in Contentful"):
            cma.build_changes({"id": "wordpress", "relatedApps": ["Missing"]}, ENTRY, SCHEMA,
                              related_app_ids=RELATED_APP_IDS, allowed_related_apps={"Missing"})

    def test_field_filter_limits_the_update(self) -> None:
        fields, changed, skipped, unchanged = cma.build_changes(
            ITEM, ENTRY, SCHEMA, field_names={"summary"})
        self.assertEqual(changed, ["summary"])
        self.assertEqual(skipped, [])
        self.assertEqual(unchanged, [])
        self.assertEqual(fields["summary"], {"en-US": "New summary"})
        self.assertEqual(fields["keywords"], ENTRY["fields"]["keywords"])
        self.assertEqual(fields["overview"], ENTRY["fields"]["overview"])

    def test_unknown_field_filter_is_rejected(self) -> None:
        with self.assertRaisesRegex(contentful.ContentfulError, "Unsupported push field"):
            cma.build_changes(ITEM, ENTRY, SCHEMA, field_names={"tags"})

    def test_related_apps_drop_non_store_and_duplicates(self) -> None:
        item = {"id": "wordpress", "relatedApps": ["PostgreSQL", "NotInStore", "PostgreSQL"]}
        fields, changed, _, _ = cma.build_changes(
            item, ENTRY, SCHEMA, related_app_ids=RELATED_APP_IDS,
            allowed_related_apps={"PostgreSQL"})
        self.assertEqual(fields["relatedApps"], {"en-US": [
            {"sys": {"type": "Link", "linkType": "Entry", "id": "pg-entry"}},
        ]})
        self.assertIn("relatedApps", changed)


class InputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "input.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, data) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_accepts_dataset_and_array_and_rejects_unknown_fields(self) -> None:
        self.write({"items": [ITEM]})
        by_id, order = cma.load_input(str(self.path))
        self.assertEqual(order, ["wordpress"])
        self.assertEqual(by_id["wordpress"]["keywords"], ["site", "cms"])

        self.write([ITEM])
        self.assertEqual(cma.load_input(str(self.path))[1], ["wordpress"])

        self.write({"items": [{"id": "wordpress", "tags": []}]})
        with self.assertRaisesRegex(contentful.ContentfulError, "unsupported input field"):
            cma.load_input(str(self.path))

    def test_pull_offer_codes_are_ignored(self) -> None:
        self.write({"items": [{**ITEM, "offerCodes": {"aws": ["example"]}}]})
        by_id, order = cma.load_input(str(self.path))
        self.assertEqual(order, ["wordpress"])
        self.assertIn("offerCodes", by_id["wordpress"])

    def test_duplicate_and_missing_ids_fail(self) -> None:
        self.write({"items": [ITEM, ITEM]})
        with self.assertRaisesRegex(contentful.ContentfulError, "duplicate"):
            cma.load_input(str(self.path))
        self.write({"items": [{"trademark": "No id"}]})
        with self.assertRaisesRegex(contentful.ContentfulError, "no string id"):
            cma.load_input(str(self.path))


class PushTests(unittest.TestCase):
    def test_local_validation_stops_before_api_for_dry_run_and_write(self) -> None:
        item = {**ITEM, "summary": "word " * 11}
        self.path.write_text(json.dumps({"items": [item]}), encoding="utf-8")
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(contentful.ContentfulError, "local push validation failed"):
                    self.run_push(dry_run=dry_run)
                self.schema.assert_not_called()
                self.entries.assert_not_called()
                self.update.assert_not_called()

    def test_push_validation_respects_selected_fields_and_empty_values(self) -> None:
        item = {**ITEM, "keywords": "legacy malformed value", "summary": None, "hasenterprise": False}
        self.path.write_text(json.dumps({"items": [item]}), encoding="utf-8")
        result = self.run_push(field_names={"summary", "hasenterprise"}, dry_run=True)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["skippedEmpty"], 1)

    def test_stdin_is_validated_before_any_api_access(self) -> None:
        with mock.patch.object(cma, "fetch_schema") as schema, \
             mock.patch("sys.stdin", io.StringIO(json.dumps([{"id": "wordpress", "summary": "word " * 11}]))):
            with self.assertRaisesRegex(contentful.ContentfulError, "local push validation failed"):
                cma.push(token="secret", space_id="space", environment="master", input_path="-", dry_run=True)
            schema.assert_not_called()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "input.json"
        self.path.write_text(json.dumps({"items": [ITEM]}), encoding="utf-8")
        self.update = mock.Mock(return_value={"sys": {"version": 4}})
        self.publish = mock.Mock(return_value={})
        self.schema = mock.Mock(return_value=SCHEMA)
        self.entries = mock.Mock(side_effect=self.fetch_entries)

    def fetch_entries(self, **kwargs):
        if kwargs.get("select"):
            return RELATED_PRODUCTS
        if kwargs.get("keys"):
            return [ENTRY]
        return [ENTRY, *RELATED_PRODUCTS]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_push(self, **kwargs):
        kwargs.setdefault("allowed_related_apps", set(RELATED_APP_IDS))
        kwargs.setdefault("catalog_keys", ("wordpress", "postgresql", "pgadmin"))
        with mock.patch.object(cma, "fetch_schema", self.schema), \
             mock.patch.object(cma, "fetch_entries", self.entries), \
             mock.patch.object(cma, "update_entry", self.update), \
             mock.patch.object(cma, "publish_entry", self.publish):
            return cma.push(token="secret", space_id="space", environment="master",
                            input_path=str(self.path), verbose=True, **kwargs)

    def test_updates_draft_with_version_and_preserves_fields(self) -> None:
        summary = self.run_push(publish=True)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["published"], 1)
        kwargs = self.update.call_args.kwargs
        self.assertEqual(kwargs["entry_id"], "entry-1")
        self.assertEqual(kwargs["version"], 3)
        self.assertEqual(kwargs["fields"]["trademark"], {"en-US": "WordPress", "zh-CN": "旧"})
        self.assertEqual(kwargs["fields"]["useCases"], {"en-US": ["publishing"]})
        self.assertEqual(self.publish.call_args.kwargs["version"], 4)
        self.assertIn(("wordpress",), [call.kwargs.get("keys") for call in self.entries.call_args_list])

    def test_related_apps_resolve_to_linked_entries(self) -> None:
        summary = self.run_push()
        self.assertEqual(summary["updated"], 1)
        related = self.update.call_args.kwargs["fields"]["relatedApps"]
        self.assertEqual(related, {"en-US": [
            {"sys": {"type": "Link", "linkType": "Entry", "id": "pg-entry"}},
            {"sys": {"type": "Link", "linkType": "Entry", "id": "pgadmin-entry"}},
        ]})

    def test_unresolved_related_app_is_reported(self) -> None:
        self.path.write_text(json.dumps({"items": [{"id": "wordpress", "relatedApps": ["Missing"]}]}),
                             encoding="utf-8")
        summary = self.run_push(allowed_related_apps={"Missing"})
        self.assertEqual(summary["updated"], 0)
        self.assertIn("not found in Contentful", summary["errors"][0])

    def test_non_store_related_apps_are_dropped_and_deduped(self) -> None:
        self.path.write_text(json.dumps(
            {"items": [{"id": "wordpress", "relatedApps": ["PostgreSQL", "NotInStore", "PostgreSQL"]}]}),
            encoding="utf-8")
        summary = self.run_push()
        self.assertEqual(summary["updated"], 1)
        related = self.update.call_args.kwargs["fields"]["relatedApps"]["en-US"]
        self.assertEqual([link["sys"]["id"] for link in related], ["pg-entry"])

    def test_dry_run_never_writes(self) -> None:
        summary = self.run_push(dry_run=True)
        self.assertEqual(summary["updated"], 1)
        self.update.assert_not_called()
        self.publish.assert_not_called()

    def test_without_publish_only_the_draft_is_updated(self) -> None:
        summary = self.run_push()
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["published"], 0)
        self.publish.assert_not_called()

    def test_unchanged_entry_is_skipped(self) -> None:
        current = {"items": [{"id": "wordpress", "trademark": "Old WP"}]}
        self.path.write_text(json.dumps(current), encoding="utf-8")
        summary = self.run_push()
        self.assertEqual(summary["unchanged"], 1)
        self.update.assert_not_called()

    def test_missing_entry_fails_or_is_allowed(self) -> None:
        self.entries.side_effect = None
        self.entries.return_value = []
        summary = self.run_push()
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("not found", summary["errors"][0])

        summary = self.run_push(allow_missing=True)
        self.assertEqual(summary["missing"], ["wordpress"])
        self.assertEqual(summary["errors"], [])

    def test_version_conflict_is_reported_and_does_not_publish(self) -> None:
        self.update.side_effect = contentful.ContentfulError("Contentful version conflict (HTTP 409).")
        summary = self.run_push(publish=True)
        self.assertEqual(summary["updated"], 0)
        self.assertEqual(summary["published"], 0)
        self.assertIn("version conflict", summary["errors"][0])

    def test_publish_requires_dry_run_separation(self) -> None:
        with self.assertRaisesRegex(contentful.ContentfulError, "dry-run"):
            self.run_push(dry_run=True, publish=True)

    def test_apps_subset_order_and_unknown_app(self) -> None:
        other = {"items": [ITEM, {**ITEM, "id": "odoo"}]}
        self.path.write_text(json.dumps(other), encoding="utf-8")
        summary = self.run_push(app_ids=("odoo", "wordpress"))
        self.assertEqual(summary["selected"], 2)
        with self.assertRaisesRegex(contentful.ContentfulError, "not in the target catalog"):
            self.run_push(app_ids=("missing",))


class ManagementRequestTests(unittest.TestCase):
    def test_error_messages_do_not_leak_token(self) -> None:
        error = urllib.error.HTTPError("https://api.contentful.com", 401, "Unauthorized", {},
                                       io.BytesIO(b'{"message":"invalid token"}'))
        with mock.patch.object(cma.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(contentful.ContentfulError) as caught:
                cma._request("GET", "https://example.test", "secret-token")
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))

    def test_version_conflict_message_is_actionable(self) -> None:
        error = urllib.error.HTTPError("https://api.contentful.com", 409, "Conflict", {}, io.BytesIO(b""))
        with mock.patch.object(cma.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(contentful.ContentfulError, "version conflict"):
                cma._request("PUT", "https://example.test", "token")

    def test_management_entry_query_filters_by_application_keys(self) -> None:
        with mock.patch.object(cma, "_request", return_value={"total": 1, "items": [ENTRY]}) as request:
            cma.fetch_entries(token="t", space_id="s", environment="e", content_type="product",
                              keys=("wordpress",))
        url = request.call_args.args[1]
        self.assertIn("fields.key=wordpress", url)
        with mock.patch.object(cma, "_request", return_value={"total": 1, "items": [ENTRY]}) as request:
            cma.fetch_entries(token="t", space_id="s", environment="e", content_type="product",
                              keys=("wordpress", "odoo"))
        url = request.call_args.args[1]
        self.assertIn("fields.key%5Bin%5D=wordpress%2Codoo", url)


class PushCliTests(unittest.TestCase):
    def test_default_input_uses_catalog_dir(self) -> None:
        summary = {"selected": 0, "matched": 0, "updated": 0, "published": 0,
                   "unchanged": 0, "skippedEmpty": 0, "missing": [], "errors": []}
        runner = CliRunner(mix_stderr=False)
        with mock.patch.dict("os.environ", {"MK_CATALOG_DIR": "/tmp/example-catalog"}), \
             mock.patch.object(cma, "push", return_value=summary) as push:
            result = runner.invoke(
                cli,
                ["catalog", "push", "--space-id", "space", "--token", "secret", "--no-progress"],
            )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(push.call_args.kwargs["input_path"], "/tmp/example-catalog/catalog.json")

    def test_contentful_token_environment_variable_is_used(self) -> None:
        summary = {"selected": 0, "matched": 0, "updated": 0, "published": 0,
                   "unchanged": 0, "skippedEmpty": 0, "missing": [], "errors": []}
        runner = CliRunner(mix_stderr=False)
        with mock.patch.dict("os.environ", {"CONTENTFUL_TOKEN": "secret-token"}), \
             mock.patch.object(cma, "push", return_value=summary) as push:
            result = runner.invoke(
                cli,
                ["catalog", "push", "--space-id", "space", "--no-progress"],
            )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(push.call_args.kwargs["token"], "secret-token")

    def test_fields_option_is_forwarded_and_validated(self) -> None:
        summary = {"selected": 0, "matched": 0, "updated": 0, "published": 0,
                   "unchanged": 0, "skippedEmpty": 0, "missing": [], "errors": []}
        runner = CliRunner(mix_stderr=False)
        with mock.patch.object(cma, "push", return_value=summary) as push:
            result = runner.invoke(
                cli,
                ["catalog", "push", "--space-id", "space", "--token", "secret",
                 "--fields", "summary,keywords", "--no-progress"],
            )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(push.call_args.kwargs["field_names"], {"summary", "keywords"})

        result = runner.invoke(
            cli,
            ["catalog", "push", "--space-id", "space", "--token", "secret",
             "--fields", "tags", "--no-progress"],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("unsupported push field", result.stderr)

    def test_token_is_required_and_help_lists_options(self) -> None:
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli, ["catalog", "push", "--space-id", "space"],
                               env={"CONTENTFUL_TOKEN": ""})
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("token", (result.stderr or result.stdout).lower())
        result = runner.invoke(cli, ["catalog", "push", "--help"])
        self.assertEqual(result.exit_code, 0)
        for option in ("--input", "--apps", "--fields", "--dry-run", "--publish", "--clear"):
            self.assertIn(option, result.output)


if __name__ == "__main__":
    unittest.main()
