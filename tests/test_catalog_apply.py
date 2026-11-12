from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from cli import catalog_apply, catalog_file
from cli.cli import cli
from cli.commands.catalog import _merge_dataset
from tests.test_catalog_verify import app, dataset


class CatalogApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "catalog.json"
        self.baseline = self.root / "baseline.json"
        self.candidate = self.root / "candidate.json"
        self.before = dataset(app("odoo", "Odoo"), app("wordpress", "WordPress"))
        self.before["items"][0]["targets"] = {"aws": "keep"}
        self.write(self.output, self.before)
        self.write(self.baseline, self.before)

    def write(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data), encoding="utf-8")

    def apply(self, data: dict, **options) -> dict:
        self.write(self.candidate, data)
        return catalog_apply.apply(self.candidate, self.baseline, self.output,
                                   app_ids=("odoo",), fields=("summary",), **options)

    def test_commits_only_selected_fields_with_exact_backup(self) -> None:
        original = self.output.read_bytes()
        data = copy.deepcopy(self.before)
        data["items"][0]["summary"] = "Modular ERP suite"
        result = self.apply(data)
        self.assertEqual(result["changed"], {"odoo": ["summary"]})
        self.assertEqual(Path(result["backup"]).read_bytes(), original)
        self.assertEqual(json.loads(self.output.read_text()), data)

    def test_skip_invalid_restores_app_and_validates_only_survivors(self) -> None:
        self.before["items"][1]["summary"] = None
        self.write(self.output, self.before)
        self.write(self.baseline, self.before)
        data = copy.deepcopy(self.before)
        data["items"][0]["summary"] = "Modular ERP suite"
        self.write(self.candidate, data)
        result = catalog_apply.apply(self.candidate, self.baseline, self.output,
                                     app_ids=("odoo", "wordpress"), fields=("summary",), skip_invalid=True)
        self.assertEqual(result["succeeded"], ["odoo"])
        self.assertEqual(result["skipped"], ["wordpress"])
        self.assertEqual(json.loads(self.output.read_text())["items"][1], self.before["items"][1])

    def test_isolation_errors_never_skipped(self) -> None:
        for mutate in (
            lambda d: d["items"][0].pop("targets"),
            lambda d: d["items"][1].update(summary="Changed"),
            lambda d: d.update(generatedAt="Changed"),
            lambda d: d["items"].reverse(),
            lambda d: d["items"].append(copy.deepcopy(d["items"][0])),
            lambda d: d["items"].__setitem__(0, "malformed"),
        ):
            with self.subTest(mutate=mutate):
                data = copy.deepcopy(self.before)
                mutate(data)
                with self.assertRaisesRegex(ValueError, "isolation/structure"):
                    self.apply(data, skip_invalid=True)
                self.assertEqual(json.loads(self.output.read_text()), self.before)

    def test_invalid_format_requires_explicit_skip(self) -> None:
        data = copy.deepcopy(self.before)
        data["items"][0]["summary"] = "word " * 11
        with self.assertRaisesRegex(ValueError, "validation failed"):
            self.apply(data)
        result = self.apply(data, skip_invalid=True)
        self.assertEqual(result["succeeded"], [])
        self.assertIsNone(result["backup"])
        self.assertEqual(json.loads(self.output.read_text()), self.before)

    def test_stale_baseline_and_in_place_candidate_refused(self) -> None:
        data = copy.deepcopy(self.before)
        data["items"][0]["summary"] = "Concurrent edit"
        self.write(self.output, data)
        with self.assertRaisesRegex(ValueError, "differs from baseline"):
            self.apply(self.before)
        with self.assertRaisesRegex(ValueError, "separate"):
            catalog_apply.apply(self.output, self.baseline, self.output, app_ids=("odoo",), fields=("summary",))

    def test_atomic_failure_keeps_original_and_backup(self) -> None:
        original = self.output.read_bytes()
        with mock.patch("cli.catalog_file.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                catalog_file.replace(self.output, "new", expected=original)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(len(list(self.root.glob("catalog.json.before-*.json"))), 1)
        self.assertEqual(list(self.root.glob("*.pending-*")), [])

    def test_atomic_writer_detects_concurrent_changes(self) -> None:
        original = self.output.read_bytes()
        self.output.write_text("external edit")
        with self.assertRaisesRegex(ValueError, "changed since"):
            catalog_file.replace(self.output, "new", expected=original)
        self.assertEqual(self.output.read_text(), "external edit")

    def test_merge_preserves_missing_refresh_state_and_clears_recovered_ids(self) -> None:
        self.before["missing"] = ["unrelated", "odoo"]
        self.write(self.output, self.before)
        pulled = dataset(app("odoo", "Odoo"))
        pulled["missing"] = ["wordpress"]
        merged = _merge_dataset(self.output, pulled)
        self.assertEqual(merged["missing"], ["unrelated", "wordpress"])
        self.assertEqual(merged["items"][1], self.before["items"][1])

    def test_pull_backs_up_before_refresh_and_refuses_concurrent_edit(self) -> None:
        original = self.output.read_bytes()
        pulled = dataset(app("odoo", "Odoo", summary="Pulled ERP suite"))
        args = ["catalog", "pull", "--space-id", "test-space", "--token", "test-token",
                "--apps", "odoo", "--merge", "--output", str(self.output)]
        with mock.patch("cli.commands.catalog.contentful.pull_dataset", return_value=pulled), \
             mock.patch("cli.commands.catalog.marketplace.offer_codes_by_app", return_value={}):
            result = CliRunner().invoke(cli, args)
        self.assertEqual(result.exit_code, 0, result.output)
        backups = list(self.root.glob("catalog.json.before-*.json"))
        self.assertEqual(backups[0].read_bytes(), original)

        def concurrent_pull(**kwargs):
            external = copy.deepcopy(self.before)
            external["items"][0]["summary"] = "Concurrent edit"
            self.write(self.output, external)
            return pulled

        with mock.patch("cli.commands.catalog.contentful.pull_dataset", side_effect=concurrent_pull), \
             mock.patch("cli.commands.catalog.marketplace.offer_codes_by_app", return_value={}):
            result = CliRunner().invoke(cli, args)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("changed since", result.output)
        self.assertEqual(json.loads(self.output.read_text())["items"][0]["summary"], "Concurrent edit")

    def test_cli_honors_catalog_dir_and_returns_checkpoint(self) -> None:
        data = copy.deepcopy(self.before)
        data["items"][0]["summary"] = "Modular ERP suite"
        self.write(self.candidate, data)
        with mock.patch.dict(os.environ, {"MK_CATALOG_DIR": str(self.root)}):
            result = CliRunner().invoke(cli, ["catalog", "apply", "--candidate", str(self.candidate),
                "--baseline", str(self.baseline), "--apps", "odoo", "--fields", "summary"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)["succeeded"], ["odoo"])


if __name__ == "__main__":
    unittest.main()
