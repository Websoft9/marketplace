from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cli import catalog_verify  # noqa: E402
from cli.cli import cli  # noqa: E402
from cli.commands.catalog import _parse_app_ids  # noqa: E402


def app(app_id: str, trademark: str, **overrides) -> dict:
    item = {
        "id": app_id,
        "trademark": trademark,
        "overview": f"{trademark} is a self-hosted application for teams.",
        "keywords": ["self-hosted", "team"],
        "usecases": ["Collaboration"],
        "relatedApps": [],
        "summary": "Self-hosted team application",
        "hasenterprise": True,
    }
    item.update(overrides)
    return item


def dataset(*items: dict) -> dict:
    return {
        "schemaVersion": 1,
        "generatedAt": "2026-01-01T00:00:00Z",
        "count": len(items),
        "items": list(items),
        "source": {"catalog": "test"},
    }


class CatalogVerifyTests(unittest.TestCase):
    def test_generated_profile_enforces_formats_but_keeps_semantic_warnings(self) -> None:
        data = dataset(app("target", "Target", keywords=["ERP."],
                           usecases=["AI agent development"], overview="Team application!", summary="Team application."))
        existing = catalog_verify.verify(data)
        generated = catalog_verify.verify(data, profile="generated")
        self.assertEqual(existing["errors"], 0)
        self.assertGreaterEqual(generated["errors"], 4)
        self.assertEqual(generated["checked"]["target"]["summary"], "error")
        data = dataset(app("target", "Target", summary="Leading application"))
        result = catalog_verify.verify(data, profile="generated")
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["warnings"], 1)

    def test_generated_related_apps_cap_and_runtime_are_errors(self) -> None:
        others = [app(f"app{i}", f"App{i}") for i in range(9)]
        data = dataset(app("target", "Target", relatedApps=[f"App{i}" for i in range(9)]), *others)
        self.assertEqual(catalog_verify.verify(data, app_ids=["target"], fields=["relatedApps"], profile="generated")["errors"], 1)
        self.assertEqual(catalog_verify.verify(data, app_ids=["target"], fields=["relatedApps"])["errors"], 0)
        data = dataset(app("target", "Target", relatedApps=["Docker"]), app("docker", "Docker"))
        self.assertEqual(catalog_verify.verify(data, app_ids=["target"], fields=["relatedApps"], profile="generated")["errors"], 1)

    def test_push_profile_permits_partial_empty_fields_and_platform_link_normalization(self) -> None:
        data = dataset({"id": "odoo", "summary": None, "hasenterprise": False,
                        "relatedApps": ["NotInLocalFile", "NotInLocalFile"]})
        result = catalog_verify.verify(data, profile="push")
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["checked"]["odoo"]["summary"], "skipped-empty")
        self.assertEqual(result["checked"]["odoo"]["hasenterprise"], "ok")

    def test_generated_ascii_punctuation_preserves_published_product_names(self) -> None:
        data = dataset(app("target", "App—Name", overview="App—Name manages workflows."))
        self.assertEqual(catalog_verify.verify(data, fields=["overview"], profile="generated")["errors"], 0)
        data["items"][0]["overview"] = "App—Name manages workflows—visually."
        self.assertEqual(catalog_verify.verify(data, fields=["overview"], profile="generated")["errors"], 1)

    def test_baseline_detects_absent_versus_null_unselected_fields(self) -> None:
        before = dataset(app("odoo", "Odoo"))
        before["items"][0]["extra"] = None
        after = json.loads(json.dumps(before))
        del after["items"][0]["extra"]
        result = catalog_verify.verify(after, fields=["summary"], baseline=before)
        self.assertEqual(result["errors"], 1)

    def test_all_keyword_selects_every_item(self) -> None:
        self.assertIsNone(_parse_app_ids("all"))
        self.assertIsNone(_parse_app_ids("ALL"))
        self.assertEqual(_parse_app_ids("odoo,wordpress"), ("odoo", "wordpress"))
        self.assertEqual(_parse_app_ids(""), ())

    def test_valid_item_passes_without_issues(self) -> None:
        data = dataset(app("odoo", "Odoo"), app("wordpress", "WordPress"))
        result = catalog_verify.verify(data, app_ids=["odoo"])
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["checked"], {"odoo": {
            "overview": "ok",
            "keywords": "ok",
            "usecases": "ok",
            "relatedApps": "ok",
            "summary": "ok",
            "hasenterprise": "ok",
        }})

    def test_keywords_reject_duplicates_long_values_and_empty(self) -> None:
        data = dataset(app("odoo", "Odoo", keywords=["erp", "erp", "x" * 51]))
        result = catalog_verify.verify(data, app_ids=["odoo"], fields=["keywords"])
        messages = [issue["message"] for issue in result["issues"]]
        self.assertTrue(any("duplicates" in message for message in messages))
        self.assertTrue(any("maximum 50" in message for message in messages))
        data = dataset(app("odoo", "Odoo", keywords=[]))
        result = catalog_verify.verify(data, app_ids=["odoo"], fields=["keywords"])
        self.assertEqual(result["errors"], 0)
        self.assertEqual(result["warnings"], 1)
        result = catalog_verify.verify(
            data, app_ids=["odoo"], fields=["keywords"], require_non_empty=True
        )
        self.assertEqual(result["errors"], 1)

    def test_usecases_reject_long_phrases_and_more_than_ten(self) -> None:
        data = dataset(app("odoo", "Odoo", usecases=["This phrase has five words"]))
        result = catalog_verify.verify(data, app_ids=["odoo"], fields=["usecases"])
        self.assertTrue(any("maximum 4" in issue["message"] for issue in result["issues"]))
        data = dataset(app("odoo", "Odoo", usecases=[f"Use {index}" for index in range(11)]))
        result = catalog_verify.verify(data, app_ids=["odoo"], fields=["usecases"])
        self.assertTrue(any("maximum 10" in issue["message"] for issue in result["issues"]))

    def test_related_apps_must_be_catalog_trademarks_and_not_self(self) -> None:
        data = dataset(
            app("odoo", "Odoo", relatedApps=["WordPress", "Odoo", "Docker"]),
            app("wordpress", "WordPress"),
        )
        result = catalog_verify.verify(data, app_ids=["odoo"], fields=["relatedApps"])
        messages = [issue["message"] for issue in result["issues"]]
        self.assertTrue(any("itself" in message for message in messages))
        self.assertTrue(any("not a trademark" in message for message in messages))
        self.assertTrue(any("generic runtime" in message for message in messages))

    def test_related_apps_warn_above_eight_without_blocking(self) -> None:
        others = [app(f"app{index}", f"App{index}") for index in range(9)]
        data = dataset(app("odoo", "Odoo", relatedApps=[f"App{index}" for index in range(9)]), *others)
        result = catalog_verify.verify(data, app_ids=["odoo"], fields=["relatedApps"])
        self.assertEqual(result["errors"], 0)
        self.assertTrue(any("must stay at 8" in issue["message"] for issue in result["issues"]))

    def test_repeated_same_layer_apps_are_warned(self) -> None:
        data = dataset(
            app(
                "target",
                "Target",
                relatedApps=["PostgreSQL", "MySQL", "Ollama", "LocalAI", "GitLab", "Gitea"],
            ),
            app("postgresql", "PostgreSQL"),
            app("mysql", "MySQL"),
            app("ollama", "Ollama"),
            app("localai", "LocalAI"),
            app("gitlab", "GitLab"),
            app("gitea", "Gitea"),
        )
        result = catalog_verify.verify(data, app_ids=["target"], fields=["relatedApps"])
        messages = [issue["message"] for issue in result["issues"]]
        self.assertTrue(any("multiple relational data stores" in message for message in messages))
        self.assertTrue(any("multiple model runtimes" in message for message in messages))
        self.assertTrue(any("multiple source-control tools" in message for message in messages))
        self.assertEqual(result["errors"], 0)

    def test_overview_and_summary_limits(self) -> None:
        data = dataset(app(
            "odoo",
            "Odoo",
            overview="x" * 351,
            summary="one two three four five six seven eight nine ten eleven",
        ))
        result = catalog_verify.verify(
            data, app_ids=["odoo"], fields=["overview", "summary"]
        )
        messages = [issue["message"] for issue in result["issues"]]
        self.assertTrue(any("maximum 350" in message for message in messages))
        self.assertTrue(any("maximum 10" in message for message in messages))

    def test_summary_rejects_open_source_wording(self) -> None:
        data = dataset(app("odoo", "Odoo", summary="Open-source ERP suite"))
        result = catalog_verify.verify(data, app_ids=["odoo"], fields=["summary"])
        self.assertEqual(result["errors"], 1)
        self.assertIn("open source", result["issues"][0]["message"])

    def test_hasenterprise_must_be_boolean(self) -> None:
        data = dataset(app("odoo", "Odoo", hasenterprise="yes"))
        result = catalog_verify.verify(data, app_ids=["odoo"], fields=["hasenterprise"])
        self.assertEqual(result["errors"], 1)
        self.assertIn("JSON Boolean", result["issues"][0]["message"])

    def test_missing_field_is_an_error(self) -> None:
        item = app("odoo", "Odoo")
        del item["summary"]
        result = catalog_verify.verify(
            dataset(item), app_ids=["odoo"], fields=["summary"]
        )
        self.assertEqual(result["errors"], 1)

    def test_unknown_application_is_reported(self) -> None:
        result = catalog_verify.verify(dataset(app("odoo", "Odoo")), app_ids=["missing"])
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["issues"][0]["app"], "missing")

    def test_baseline_allows_selected_fields_and_rejects_other_changes(self) -> None:
        before = dataset(app("odoo", "Odoo"), app("wordpress", "WordPress"))
        after = json.loads(json.dumps(before))
        after["items"][0]["summary"] = "Modular ERP and business apps"
        result = catalog_verify.verify(
            after, app_ids=["odoo"], fields=["summary"], baseline=before
        )
        self.assertEqual(result["errors"], 0, result["issues"])

        after["items"][0]["overview"] = "Changed without being selected."
        result = catalog_verify.verify(
            after, app_ids=["odoo"], fields=["summary"], baseline=before
        )
        self.assertTrue(any(issue["message"] == "unselected field changed" for issue in result["issues"]))

        after = json.loads(json.dumps(before))
        after["items"][1]["summary"] = "Changed"
        result = catalog_verify.verify(
            after, app_ids=["odoo"], fields=["summary"], baseline=before
        )
        self.assertTrue(any(issue["message"] == "unselected item changed" for issue in result["issues"]))

    def test_reference_catalog_supplies_related_app_trademarks(self) -> None:
        data = dataset(app("target", "Target", relatedApps=["CloudBeaver"]))
        local = catalog_verify.verify(
            data, app_ids=["target"], fields=["relatedApps"], profile="generated")
        self.assertEqual(local["errors"], 1)
        reference = [{"key": "cloudbeaver", "trademark": "CloudBeaver"}]
        result = catalog_verify.verify(
            data, app_ids=["target"], fields=["relatedApps"],
            profile="generated", reference=reference)
        self.assertEqual(result["errors"], 0)

    def test_cli_verify_reference_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            reference = Path(directory) / "product_en.json"
            path.write_text(json.dumps(dataset(app("target", "Target", relatedApps=["CloudBeaver"]))), encoding="utf-8")
            reference.write_text(json.dumps([{"key": "cloudbeaver", "trademark": "CloudBeaver"}]), encoding="utf-8")
            result = CliRunner().invoke(
                cli, ["catalog", "verify", "--input", str(path), "--apps", "target",
                      "--fields", "relatedApps", "--profile", "generated",
                      "--reference", str(reference), "--json"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(json.loads(result.output)["errors"], 0)

    def test_cli_verify_prints_issues_and_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(dataset(app("odoo", "Odoo", summary="too many words " * 2))), encoding="utf-8")
            runner = CliRunner()
            result = runner.invoke(cli, ["catalog", "verify", "--input", str(path), "--apps", "odoo"])
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("odoo:", result.output)

    def test_cli_verify_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(json.dumps(dataset(app("odoo", "Odoo"))), encoding="utf-8")
            runner = CliRunner()
            result = runner.invoke(
                cli, ["catalog", "verify", "--input", str(path), "--apps", "odoo", "--json"]
            )
            self.assertEqual(result.exit_code, 0, result.output)
            payload = json.loads(result.output)
            self.assertEqual(payload["errors"], 0)
            self.assertEqual(payload["selected"], ["odoo"])


if __name__ == "__main__":
    unittest.main()
