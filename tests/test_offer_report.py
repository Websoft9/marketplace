from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from cli import offer_report
from cli.cli import cli


PLAN = {
    "schemaVersion": 1,
    "key": "odoo",
    "sourceHash": "source-hash",
    "planHash": "plan-hash",
    "publishBlockers": [],
    "targets": [
        {
            "code": "code-a",
            "entityId": "prod-1",
            "plan": {
                "baseRevision": "prod-1@2",
                "request": {"ChangeSet": []},
                "diff": {
                    "ProductTitle": {"before": "Old <title>", "after": "New & title"}
                },
            },
        }
    ],
}

APPLY = {
    "schemaVersion": 1,
    "key": "odoo",
    "status": "PARTIAL",
    "targets": [
        {
            "code": "code-a",
            "entityId": "prod-1",
            "status": "SUBMITTED",
            "changeSetId": "cs-1",
        }
    ],
    "skipped": ["code-b"],
}

PREFLIGHT = {
    "schemaVersion": 1,
    "app": "app-b",
    "cloud": "aws",
    "codes": ["code-a", "forced-code"],
    "codeSource": "explicit",
    "unlistedCodes": ["forced-code"],
}

RESOLVE = {
    "schemaVersion": 1,
    "app": "odoo",
    "cloud": "aws",
    "targets": [
        {
            "code": "code-a",
            "entityId": "prod-1",
            "offer": "Ubuntu",
            "title": "Odoo | Ubuntu",
            "visibility": "Public",
            "match": "app",
        },
        {
            "code": "code-b",
            "entityId": "prod-2",
            "offer": "",
            "title": "Odoo",
            "visibility": "Limited",
            "match": "override",
        },
    ],
    "unresolved": [{"offer": "Debian", "reason": "no live product matches"}],
}


class ReportTests(unittest.TestCase):
    def test_resolve_report_surfaces_identity_catalog_degradation(self):
        text = offer_report.resolve_targets({**RESOLVE, "matching": {"degraded": True, "warnings": [
            {"appId": "ffmpeg", "message": "Missing/empty trademark; using safe application ID fallback."},
        ]}})
        self.assertIn(":warning: Identity catalog `ffmpeg`", text)
        self.assertIn("safe application ID fallback", text)

    def test_resolve_report_includes_store_fact_scope_source_and_time(self):
        text = offer_report.resolve_targets({**RESOLVE, "inventory": {
            "source": "snapshot", "queriedAt": "2026-11-17T00:00:00+00:00", "complete": True,
            "scope": {"visibility": "public", "productType": "image"},
            "store": {"id": "SELF", "provider": "Authenticated AWS seller"},
        }})
        self.assertIn("Store: Authenticated AWS seller (`SELF`)", text)
        self.assertIn("Inventory source: `snapshot`", text)
        self.assertIn("2026-11-17T00:00:00+00:00", text)
        self.assertIn("Scope: `public` `image`; complete: `true`", text)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.runner = CliRunner()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_preflight_report_flags_forced_codes(self):
        text = offer_report.preflight(PREFLIGHT)
        self.assertIn("## Offer apply: `app-b`", text)
        self.assertIn("Codes outside the offer-code matrix (forced): `forced-code`", text)
        self.assertIn("| 2 | `forced-code` |", text)

    def test_preflight_report_marks_skipped_codes(self):
        text = offer_report.preflight(
            {
                "app": "odoo",
                "cloud": "aws",
                "codes": [],
                "codeSource": "skipped",
            }
        )
        self.assertIn("Offer codes: skipped (live resolution follows)", text)
        self.assertNotIn("| # | Offer code |", text)

    def test_plan_report_renders_arns_and_pending_diffs(self):
        text = offer_report.plan(
            PLAN, cloud="aws", app_name="Odoo", account_id="123456789012"
        )
        self.assertIn("## Offer plan: Odoo (`odoo`)", text)
        self.assertIn("| Offer code | Entity ARN |", text)
        self.assertIn(
            "arn:aws:aws-marketplace:us-east-1:123456789012:"
            "AWSMarketplace/AmiProduct/prod-1",
            text,
        )
        self.assertIn("<summary><code>ProductTitle</code></summary>", text)
        self.assertIn("<pre>Old &lt;title&gt;</pre>", text)
        self.assertIn("<pre>New &amp; title</pre>", text)
        self.assertIn("1 with pending changes", text)

    def test_plan_report_without_aws_context_uses_entity_ids(self):
        text = offer_report.plan(PLAN, cloud=None)
        self.assertIn("| Offer code | EntityId |", text)
        self.assertIn("`prod-1`", text)
        self.assertNotIn("arn:aws", text)

    def test_plan_report_reports_no_changes(self):
        plan = json.loads(json.dumps(PLAN))
        plan["targets"][0]["plan"]["request"] = None
        plan["targets"][0]["plan"]["diff"] = {}
        text = offer_report.plan(plan)
        self.assertIn("No cloud change is required.", text)

    def test_apply_report_lists_partial_results(self):
        text = offer_report.apply(APPLY, cloud="aws")
        self.assertIn("- Status: `PARTIAL`", text)
        self.assertIn("| `code-a` | `prod-1` | `SUBMITTED` | `cs-1` |", text)
        self.assertIn("Skipped offer codes: code-b", text)
        self.assertIn("re-run the workflow", text)
        self.assertIn("aws accepted every ChangeSet", text)

    def test_resolve_report_renders_targets_and_unresolved(self):
        text = offer_report.resolve_targets(RESOLVE)
        self.assertIn("## Resolved offer targets", text)
        self.assertIn("| `code-a` | Ubuntu | app | Public | Odoo \\| Ubuntu |", text)
        self.assertIn("| `code-b` | base | override | Limited | Odoo |", text)
        self.assertIn("- Unresolved offer `Debian`: no live product matches", text)

    def test_asset_partial_report_preserves_specific_resume_hint(self):
        value = {**APPLY, "resumeHint": "Inspect submitted ChangeSet IDs before retrying; plan remaining targets."}
        text = offer_report.apply(value, cloud="aws")
        self.assertIn(value["resumeHint"], text)
        self.assertNotIn("already-updated targets plan to NO_CHANGES", text)

    def test_report_command_writes_markdown(self):
        path = self.write("plan.json", PLAN)
        output = self.directory / "plan.md"
        result = self.runner.invoke(
            cli,
            [
                "offer", "report", "plan",
                "--input", str(path),
                "--cloud", "aws",
                "--app-name", "Odoo",
                "--account-id", "123456789012",
                "--output", str(output),
            ],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("## Offer plan: Odoo (`odoo`)", output.read_text(encoding="utf-8"))


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_accepts_a_supported_cloud_and_valid_roles(self):
        result = self.runner.invoke(
            cli,
            [
                "offer", "doctor", "--cloud", "aws",
                "--plan-role-arn", "arn:aws:iam::123456789012:role/plan",
                "--apply-role-arn", "arn:aws:iam::123456789012:role/apply",
            ],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            json.loads(result.output)["checkedRoles"], ["plan", "apply"]
        )

    def test_rejects_a_cloud_without_plan_apply_support(self):
        result = self.runner.invoke(cli, ["offer", "doctor", "--cloud", "azure"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("no offer plan/apply implementation", result.output)
        self.assertIn("aws", result.output)

    def test_rejects_an_empty_plan_role(self):
        result = self.runner.invoke(
            cli, ["offer", "doctor", "--cloud", "aws", "--plan-role-arn", ""]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("AWS_PLAN_ROLE_ARN, or AWS_ROLE_ARN", result.output)

    def test_rejects_a_malformed_apply_role(self):
        result = self.runner.invoke(
            cli,
            ["offer", "doctor", "--cloud", "aws", "--apply-role-arn", "not-an-arn"],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("must be an IAM role ARN", result.output)


class VersionsSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.runner = CliRunner()

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshots_a_local_version_source(self):
        source = self.directory / "versions-source.json"
        source.write_text(
            json.dumps([{"id": "odoo", "version": "20.0"}]), encoding="utf-8"
        )
        output = self.directory / "versions.json"
        result = self.runner.invoke(
            cli,
            ["offer", "versions", "--source", str(source), "--output", str(output)],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8")),
            [{"id": "odoo", "version": "20.0"}],
        )

    def test_rejects_a_non_array_version_source(self):
        source = self.directory / "versions-source.json"
        source.write_text(json.dumps({"odoo": "20.0"}), encoding="utf-8")
        output = self.directory / "versions.json"
        result = self.runner.invoke(
            cli,
            ["offer", "versions", "--source", str(source), "--output", str(output)],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("must be a JSON array", result.output)


if __name__ == "__main__":
    unittest.main()
