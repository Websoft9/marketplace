"""Regression tests for offer-code audit and mutation safety boundaries."""
from __future__ import annotations

import copy
import json
import multiprocessing
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from click.testing import CliRunner

from cli import offer_codes, offer_listing
from cli.cli import cli


def app_entry(app, **platform_codes):
    return {
        "id": app, "type": "app", "applications": [app],
        "productID": {p: platform_codes.get(p, []) for p in offer_codes.REQUIRED_PLATFORMS},
    }


def _lock_worker(source, entered, release):
    with offer_codes.source_lock(source):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("timed out waiting for lock test release")


class OfferSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.offer = self.root / "matrix.json"
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps([{"key": "old"}, {"key": "new"}]))
        self.initial = [app_entry("old", aws=["a" * 25, "b" * 25], azure=["publisher.old"])]
        self.offer.write_text(json.dumps(self.initial))
        self.runner = CliRunner()

    def invoke(self, command, *args):
        argv = ["offer-codes", command, "--offer", str(self.offer)]
        if command in {"add", "replace"}:
            argv += ["--catalog", str(self.catalog)]
        return self.runner.invoke(cli, argv + list(args))

    def replace(self, *args):
        return self.invoke("replace", "--from", "old", "--to", "new", *args)

    def test_explicit_empty_replace_filters_never_write(self):
        before = self.offer.read_bytes()
        for flag in ("--code", "--platform"):
            for value in ("", " ", ",", ", ,"):
                with self.subTest(flag=flag, value=value):
                    result = self.replace(flag, value, "--delete-empty")
                    self.assertNotEqual(result.exit_code, 0, result.output)
                    self.assertIn("must not be empty", result.output)
                    self.assertEqual(self.offer.read_bytes(), before)

    def test_service_rejects_explicit_empty_selections(self):
        for selection in ({"platforms": []}, {"codes": []}):
            documents = [("mock", copy.deepcopy(self.initial))]
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                offer_codes.replace_code(documents, old_app="old", new_app="new", **selection)
            self.assertEqual(documents[0][1], self.initial)

    def test_replace_accepts_repeated_platforms(self):
        result = self.replace("--platform", "aws", "--platform", "azure", "--dry-run", "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        moves = json.loads(result.stdout)["result"]["moves"]
        self.assertEqual({m["platform"] for m in moves}, {"aws", "azure"})

    def test_writes_fail_closed_when_catalog_is_unreadable(self):
        self.catalog.unlink()
        before = self.offer.read_bytes()
        for command, args in (
            ("add", ["--app", "new", "--platform", "aws", "--code", "c" * 25]),
            ("replace", ["--from", "old", "--to", "new"]),
        ):
            with self.subTest(command=command):
                result = self.invoke(command, *args)
                self.assertEqual(result.exit_code, 1, result.output)
                self.assertIn("cannot validate application catalog", result.output)
                self.assertEqual(self.offer.read_bytes(), before)

    def test_offline_bypass_is_explicit_and_reported(self):
        self.catalog.unlink()
        result = self.invoke("add", "--app", "new", "--platform", "aws", "--code", "c" * 25,
                             "--skip-catalog-check", "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        output = json.loads(result.stdout)
        self.assertTrue(output["written"])
        self.assertIn("explicitly skipped", output["notes"][0])

    def test_verify_offline_mode_never_loads_catalog(self):
        with mock.patch.object(offer_codes, "load_catalog_ids", side_effect=AssertionError("network forbidden")):
            result = self.invoke("verify", "--skip-catalog-check", "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIsNone(json.loads(result.stdout)["productCatalog"])

    def test_replace_offline_bypass_is_explicit_and_reported(self):
        self.catalog.unlink()
        result = self.replace("--skip-catalog-check", "--dry-run", "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("explicitly skipped", json.loads(result.stdout)["notes"][0])

    def test_catalog_network_failure_also_blocks_writing(self):
        with mock.patch.object(offer_codes, "load_catalog_ids", side_effect=urllib.error.URLError("offline")):
            result = self.replace()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("cannot validate application catalog", result.output)

    def test_find_and_listing_check_reject_invalid_roots_and_entries(self):
        for document in ({"wrong": []}, {"items": {}}, [None], [app_entry("old", aws="bad")]):
            self.offer.write_text(json.dumps(document))
            for command in ("find", "listing-check"):
                with self.subTest(command=command, document=document):
                    result = self.invoke(command, "--json")
                    self.assertEqual(result.exit_code, 1, result.output)
                    self.assertIn("invalid offer source", result.output)

    def test_empty_array_is_valid_for_read_only_commands(self):
        self.offer.write_text("[]")
        for command in ("find", "listing-check"):
            result = self.invoke(command, "--json")
            self.assertEqual(result.exit_code, 0, result.output)
            json.loads(result.stdout)

    def test_verify_rejects_padded_type(self):
        item = app_entry("old")
        item["type"] = " app "
        result = offer_codes.verify([("mock", [item])])
        self.assertTrue(any(i["field"] == "type" and i["severity"] == "error" for i in result["issues"]))

    def test_verify_detects_alias_and_entry_id_collisions(self):
        for alias in ("shared", "second"):
            first = app_entry("first")
            second = app_entry("second")
            first["applications"].append(alias)
            if alias == "shared":
                second["applications"].append(alias)
            result = offer_codes.verify([("one", [first]), ("two", [second])])
            self.assertTrue(any("ambiguous application id or alias" in i["message"] for i in result["issues"]))

    def test_verify_detects_alias_collision_with_non_app_entry(self):
        first = app_entry("first")
        second = app_entry("websoft9")
        second["type"] = "websoft9"
        second["applications"].append("first")
        result = offer_codes.verify([("mock", [first, second])])
        self.assertTrue(any("ambiguous application id or alias" in i["message"] for i in result["issues"]))

    def test_stale_snapshot_is_never_overwritten(self):
        documents = offer_codes.load_source(str(self.offer))
        expected = {str(self.offer): self.offer.read_text()}
        offer_codes.add_codes(documents, app="old", platform="aws", codes=["c" * 25])
        latest = copy.deepcopy(self.initial)
        latest[0]["productID"]["aws"].append("d" * 25)
        self.offer.write_text(json.dumps(latest))
        with self.assertRaisesRegex(ValueError, "changed since it was read"):
            offer_codes.write_documents(documents, [str(self.offer)], expected=expected)
        self.assertEqual(json.loads(self.offer.read_text()), latest)

    def test_cli_detects_external_edit_during_catalog_load(self):
        from cli.commands import offer_codes as commands
        def changed(*args):
            latest = copy.deepcopy(self.initial)
            latest[0]["productID"]["aws"].append("d" * 25)
            self.offer.write_text(json.dumps(latest))
            return {"old", "new"}, []
        with mock.patch.object(commands, "_load_catalog", side_effect=changed):
            result = self.replace()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("changed since it was read", result.output)
        self.assertIn("d" * 25, self.offer.read_text())

    def test_directory_and_file_writers_use_the_same_lock(self):
        context = multiprocessing.get_context("fork")
        entered, release = context.Event(), context.Event()
        process = context.Process(target=_lock_worker, args=(str(self.offer), entered, release))
        try:
            with offer_codes.source_lock(str(self.root)):
                process.start()
                self.assertFalse(entered.wait(0.2))
            self.assertTrue(entered.wait(5))
            release.set()
            process.join(5)
            self.assertEqual(process.exitcode, 0)
        finally:
            release.set()
            if process.is_alive():
                process.terminate()
                process.join(5)

    def test_failed_second_replace_rolls_back_first_file(self):
        second = self.root / "second.json"
        second.write_text("[]\n")
        originals = {str(p): p.read_bytes() for p in (self.offer, second)}
        documents = [(str(p), [app_entry("updated")]) for p in (self.offer, second)]
        real_replace = os.replace
        count = 0
        def fail_second(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("simulated second-file failure")
            return real_replace(source, target)
        with mock.patch.object(offer_codes.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "second-file failure"):
                offer_codes.write_documents(documents, list(originals))
        for path, before in originals.items():
            self.assertEqual(Path(path).read_bytes(), before)
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_staging_failure_leaves_originals_unchanged(self):
        before = self.offer.read_bytes()
        with mock.patch.object(offer_codes, "serialize_document", side_effect=ValueError("serialization failed")):
            with self.assertRaisesRegex(ValueError, "serialization failed"):
                offer_codes.write_documents([(str(self.offer), [])], [str(self.offer)])
        self.assertEqual(self.offer.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".*.tmp")))

    def test_failed_rollback_retains_original_backup_and_reports_path(self):
        second = self.root / "second.json"
        second.write_text("[]")
        before = self.offer.read_bytes()
        real_replace = os.replace
        calls = 0
        def fail_commit_and_rollback(source, target):
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise OSError("simulated filesystem failure")
            return real_replace(source, target)
        with mock.patch.object(offer_codes.os, "replace", side_effect=fail_commit_and_rollback):
            with self.assertRaisesRegex(OSError, "original preserved at") as caught:
                offer_codes.write_documents([(str(self.offer), []), (str(second), [])],
                                             [str(self.offer), str(second)])
        backups = list(self.root.glob(".*.tmp"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertIn(str(backups[0]), str(caught.exception))


class ListingSafetyTests(unittest.TestCase):
    def listing_cli(self, status, strict=True):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "offers.json"
            path.write_text(json.dumps([app_entry("old", aws=["a" * 25])]))
            adapter = mock.Mock()
            adapter.availability.return_value = (True, "")
            adapter.check.return_value = {"a" * 25: offer_listing.Outcome(status)}
            with mock.patch.dict(offer_listing.ADAPTERS, {"aws": adapter}):
                return CliRunner().invoke(cli, ["offer-codes", "listing-check", "--offer", str(path),
                                               "--platform", "aws", "--json", *(["--strict"] if strict else [])])

    def test_strict_all_exists_succeeds(self):
        result = self.listing_cli(offer_listing.EXISTS)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIsNone(result.exception)
        self.assertEqual(json.loads(result.stdout)["summary"]["not_found"], 0)

    def test_strict_not_found_exits_cleanly_with_json_report(self):
        result = self.listing_cli(offer_listing.NOT_FOUND)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIsInstance(result.exception, SystemExit)
        self.assertIn("not-found code(s)", result.output)
        report, _ = json.JSONDecoder().raw_decode(result.output)
        self.assertEqual(report["summary"]["not_found"], 1)

    def test_adapter_error_exits_cleanly(self):
        result = self.listing_cli(offer_listing.ERROR)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIsInstance(result.exception, SystemExit)

    def test_listing_scope_filters_before_calling_adapter(self):
        documents = [("mock", [app_entry("first", aws=["a" * 25]),
                                app_entry("second", aws=["b" * 25])])]
        adapter = mock.Mock()
        adapter.availability.return_value = (True, "")
        adapter.check.return_value = {"b" * 25: offer_listing.Outcome(offer_listing.EXISTS)}
        result = offer_listing.check_listings(documents, apps=["second"], codes=["b" * 25],
                                             adapters={"aws": adapter})
        adapter.check.assert_called_once_with(["b" * 25])
        self.assertEqual(result["summary"]["checked"], 1)

    def test_listing_cli_scope_and_empty_filters(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "offers.json"
            path.write_text(json.dumps([app_entry("old", aws=["a" * 25]),
                                        app_entry("new", aws=["b" * 25])]))
            adapter = mock.Mock()
            adapter.availability.return_value = (True, "")
            adapter.check.return_value = {"b" * 25: offer_listing.Outcome(offer_listing.EXISTS)}
            argv = ["offer-codes", "listing-check", "--offer", str(path), "--platform", "aws"]
            with mock.patch.dict(offer_listing.ADAPTERS, {"aws": adapter}):
                result = CliRunner().invoke(cli, argv + ["--app", "new", "--code", "b" * 25, "--json"])
                self.assertEqual(result.exit_code, 0, result.output)
                adapter.check.assert_called_once_with(["b" * 25])
                adapter.check.reset_mock()
                result = CliRunner().invoke(cli, argv + ["--app", ","])
                self.assertEqual(result.exit_code, 1, result.output)
                adapter.check.assert_not_called()

    def test_no_matching_codes_never_calls_backend(self):
        adapter = mock.Mock()
        result = offer_listing.check_listings(
            [("mock", [app_entry("old", aws=["a" * 25])])],
            apps=["missing"], adapters={"aws": adapter},
        )
        adapter.availability.assert_not_called()
        adapter.check.assert_not_called()
        self.assertEqual(result["results"], [])

    def test_aws_uses_session_provider_chain_without_static_env_keys(self):
        session = mock.Mock()
        boto3 = SimpleNamespace(Session=mock.Mock(return_value=session))
        with mock.patch.dict(sys.modules, {"boto3": boto3}), mock.patch.dict(os.environ, {"AWS_PROFILE": "seller"}, clear=True):
            self.assertEqual(offer_listing.AwsAdapter().availability(), (True, ""))
        boto3.Session.assert_called_once_with(profile_name="seller")
        session.get_credentials.return_value.get_frozen_credentials.assert_called_once()

    def test_aws_unavailable_credentials_are_safe_and_skipped(self):
        session = mock.Mock()
        session.get_credentials.side_effect = RuntimeError("secret credential response")
        with mock.patch.dict(sys.modules, {"boto3": SimpleNamespace(Session=lambda **kwargs: session)}):
            available, reason = offer_listing.AwsAdapter().availability()
        self.assertFalse(available)
        self.assertNotIn("secret", reason)

    def test_aws_missing_credentials_are_skipped(self):
        session = mock.Mock()
        session.get_credentials.return_value = None
        with mock.patch.dict(sys.modules, {"boto3": SimpleNamespace(Session=lambda **kwargs: session)}):
            available, reason = offer_listing.AwsAdapter().availability()
        self.assertFalse(available)
        self.assertIn("provider chain", reason)

    def alibaba_response(self, payload):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with mock.patch("urllib.request.urlopen", return_value=response):
            return offer_listing.AlibabaCloudAdapter().check(["code"])["code"]

    def test_alibaba_failure_and_unknown_success_are_errors_not_missing(self):
        for payload in ({"success": False, "code": "TooManyRequests"},
                        {"success": False, "code": "AccessDenied"},
                        {"success": True}, {"success": True, "result": []}):
            self.assertEqual(self.alibaba_response(payload).status, offer_listing.ERROR)

    def test_alibaba_recognized_product_exists(self):
        outcome = self.alibaba_response({"success": True, "result": {"product": {"commodityInfo": {"name": "App"}}}})
        self.assertEqual(outcome.status, offer_listing.EXISTS)

    def test_alibaba_explicit_http_missing(self):
        for code in (404, 410, 429):
            with mock.patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("https://example.test", code, "error", {}, None)):
                outcome = offer_listing.AlibabaCloudAdapter().check(["code"])["code"]
            expected = offer_listing.NOT_FOUND if code in {404, 410} else offer_listing.ERROR
            self.assertEqual(outcome.status, expected)

    def aliyun_response(self, payload):
        client = mock.Mock()
        client.do_action_with_exception.return_value = json.dumps(payload)
        modules = {
            "aliyunsdkcore": SimpleNamespace(), "aliyunsdkcore.auth": SimpleNamespace(),
            "aliyunsdkcore.auth.credentials": SimpleNamespace(StsTokenCredential=mock.Mock()),
            "aliyunsdkcore.client": SimpleNamespace(AcsClient=mock.Mock(return_value=client)),
            "aliyunsdkmarket": SimpleNamespace(), "aliyunsdkmarket.request": SimpleNamespace(),
            "aliyunsdkmarket.request.v20151101": SimpleNamespace(),
            "aliyunsdkmarket.request.v20151101.DescribeProductRequest": SimpleNamespace(DescribeProductRequest=mock.Mock()),
        }
        with mock.patch.dict(sys.modules, modules), mock.patch.dict(os.environ, {
            "ALIBABA_CLOUD_ACCESS_KEY_ID": "test", "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "test",
        }):
            return offer_listing.AliyunAdapter().check(["code"])["code"]

    def test_aliyun_failures_need_exact_missing_codes(self):
        for code in ("NotAuthorized", "Throttling", "Unknown"):
            self.assertEqual(self.aliyun_response({"Success": False, "Code": code}).status, offer_listing.ERROR)
        self.assertEqual(self.aliyun_response({"Success": False, "Code": "ProductNotFound"}).status, offer_listing.NOT_FOUND)
        self.assertEqual(self.aliyun_response({}).status, offer_listing.ERROR)


class WorkflowContractTests(unittest.TestCase):
    def test_issue_reporting_consumes_current_summary_fields(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/offer-listing-check.yml"
        text = path.read_text()
        self.assertNotIn('summary["notFound"]', text)
        self.assertIn('summary["not_found"]', text)

    def test_workflow_reporting_handles_clean_and_missing_results(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/offer-listing-check.yml"
        block = path.read_text().split("python3 - <<'PY'\n")[-1]
        lines = []
        for line in block.splitlines():
            if line.strip() == "PY":
                break
            lines.append(line)
        source = "\n".join(lines)
        import textwrap
        source = textwrap.dedent(source)
        with tempfile.TemporaryDirectory() as name:
            report = Path(name) / "report.json"
            for missing in (0, 1):
                data = {"summary": {"checked": 1, "exists": 1 - missing, "not_found": missing,
                                    "not_public": 0, "errors": 0}, "results": []}
                report.write_text(json.dumps(data))
                with mock.patch.dict(os.environ, {"REPORT": str(report), "RUN_URL": "https://example.test/run"}), \
                     mock.patch("subprocess.run", return_value=SimpleNamespace(stdout="")) as run:
                    if missing:
                        exec(compile(source, str(path), "exec"), {})
                        self.assertEqual(run.call_count, 2)
                        self.assertIn("create", run.call_args.args[0])
                    else:
                        with self.assertRaises(SystemExit) as caught:
                            exec(compile(source, str(path), "exec"), {})
                        self.assertEqual(caught.exception.code, 0)
                        run.assert_not_called()

    def test_all_workflow_python_blocks_compile(self):
        import textwrap
        path = Path(__file__).resolve().parents[1] / ".github/workflows/offer-listing-check.yml"
        blocks = path.read_text().split("python3 - <<'PY'")[1:]
        for block in blocks:
            lines = []
            for line in block.splitlines()[1:]:
                if line.strip() == "PY":
                    break
                lines.append(line)
            compile(textwrap.dedent("\n".join(lines)), str(path), "exec")


class SkillContractTests(unittest.TestCase):
    def test_skill_keeps_backend_opt_in_and_requires_independent_evidence(self):
        path = Path(__file__).resolve().parents[1] / ".agents/skills/offer-code-verify/SKILL.md"
        text = path.read_text()
        self.assertIn("| `backend` | no | `false` (default)", text)
        self.assertIn("never calls a marketplace write API", text)
        self.assertNotIn("never calls a marketplace seller API", text)
        self.assertIn("--skip-catalog-check", text)
        self.assertIn("--limit 40", text)
        self.assertIn("independent, current delisting evidence", text)
        self.assertIn("never executing `add`, `remove` or `replace`", text)


if __name__ == "__main__":
    unittest.main()
