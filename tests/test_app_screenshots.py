from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from cli import app_screenshots as screenshots
from cli.appstore_browser import AppStoreBrowser
from cli.commands import tools as tools_commands
from cli.cli import cli


def png(width=1920, height=1040):
    # Header-only fixture: dimensions are checked without an image-library dependency.
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + bytes(9)


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.catalog = self.directory / "catalog.json"
        self.catalog.write_text(json.dumps([
            {"key": "n8n", "trademark": "n8n"},
            {"key": "mysql", "trademark": "MySQL"},
        ]))
        self.apps = screenshots.select_applications(str(self.catalog))
        self.credentials = screenshots.Credentials("https://example.test/", "fixture-user", "fixture-password")

    def run_batch(self, browser, **kwargs):
        with mock.patch("cli.appstore_browser.AppStoreBrowser", return_value=browser):
            browser.__enter__.return_value = browser
            return screenshots.run_screenshots(
                self.apps, credentials=self.credentials, output_dir=self.directory / "output", **kwargs,
            )

    def test_selects_all_and_normalizes_product_keys(self):
        self.assertEqual(self.apps, [{"id": "n8n", "trademark": "n8n"}, {"id": "mysql", "trademark": "MySQL"}])

    def test_selection_preserves_requested_order_and_deduplicates(self):
        result = screenshots.select_applications(str(self.catalog), "mysql, n8n,mysql")
        self.assertEqual([app["id"] for app in result], ["mysql", "n8n"])

    def test_rejects_unknown_empty_and_unsafe_selections(self):
        for value in ("", "n8n,", "not-found", "../n8n", "n8n\nINJECT", "N8N"):
            with self.subTest(value=value), self.assertRaises(screenshots.ScreenshotError):
                screenshots.select_applications(str(self.catalog), value)

    def test_rejects_empty_or_unsafe_catalog(self):
        for value in ([], [{"key": "../bad", "trademark": "bad"}]):
            self.catalog.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(screenshots.ScreenshotError):
                screenshots.select_applications(str(self.catalog))

    def test_missing_blank_or_invalid_trademark_does_not_abort_selection(self):
        for value in ({}, {"trademark": None}, {"trademark": ""}, {"trademark": "  "}, {"trademark": 123}):
            self.catalog.write_text(json.dumps([
                {"key": "n8n", "trademark": "n8n"}, {"key": "ffmpeg", **value},
                {"key": "mysql", "trademark": "MySQL"},
            ]))
            with self.subTest(value=value):
                apps = screenshots.select_applications(str(self.catalog))
                self.assertEqual([app["id"] for app in apps], ["n8n", "ffmpeg", "mysql"])
                self.assertEqual(apps[1], {"id": "ffmpeg", "trademark": ""})

    def test_full_batch_with_missing_trademark_runs_and_writes_report(self):
        self.catalog.write_text(json.dumps([
            {"key": "n8n", "trademark": "n8n"}, {"key": "ffmpeg", "trademark": None},
            {"key": "mysql", "trademark": "MySQL"},
        ]))
        self.apps = screenshots.select_applications(str(self.catalog))
        browser = mock.MagicMock()
        browser.capture.return_value = png()
        report = self.run_batch(browser, upload=False)
        self.assertEqual((report["succeeded"], report["failed"]), (3, 0))
        self.assertEqual(browser.capture.call_args_list[1].args[0], {"id": "ffmpeg", "trademark": ""})
        self.assertTrue((self.directory / "output/ffmpeg-installation.png").is_file())
        self.assertEqual(json.loads((self.directory / "output/report.json").read_text()), report)

    def test_catalog_errors_do_not_expose_source(self):
        with mock.patch.object(screenshots, "load_catalog", side_effect=ValueError("sensitive-url")):
            with self.assertRaises(screenshots.ScreenshotError) as raised:
                screenshots.select_applications("sensitive-url")
        self.assertNotIn("sensitive-url", str(raised.exception))

    def test_credentials_hidden_from_repr_and_validated(self):
        self.assertNotIn("fixture-user", repr(self.credentials))
        self.assertNotIn("fixture-password", repr(self.credentials))
        for url in ("file:///bad", "https://user:password@example.test", "https://example.test/?token=secret", "https://example.test:bad"):
            with self.subTest(url=url), mock.patch.dict(os.environ, {
                "WEBSOFT9_URL": url, "WEBSOFT9_USER": "user", "WEBSOFT9_PASSWORD": "secret",
            }), self.assertRaises(screenshots.ScreenshotError) as raised:
                screenshots.Credentials.from_environment()
            self.assertNotIn("secret", str(raised.exception))

    def test_credentials_require_all_three_variables(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(screenshots.ScreenshotError):
            screenshots.Credentials.from_environment()

    def test_requires_exact_png_dimensions(self):
        screenshots.validate_png(png())
        for image in (b"", b"not PNG", png(1920, 1080), png(3840, 2080)):
            with self.subTest(image=image[:10]), self.assertRaises(screenshots.ScreenshotError):
                screenshots.validate_png(image)

    def test_local_only_never_initializes_aws(self):
        browser = mock.MagicMock()
        browser.capture.return_value = png()
        with mock.patch.object(screenshots, "create_s3_client") as aws:
            report = self.run_batch(browser, upload=False)
        aws.assert_not_called()
        self.assertEqual(report["succeeded"], 2)
        self.assertEqual(report["failed"], 0)
        self.assertTrue((self.directory / "output/n8n-installation.png").is_file())
        self.assertNotIn("url", report["results"][0])

    def test_upload_uses_fixed_overwrite_key_and_no_acl(self):
        client = mock.Mock()
        url = screenshots.upload_screenshot(client, "n8n", png())
        request = client.put_object.call_args.kwargs
        self.assertEqual(request["Bucket"], screenshots.BUCKET)
        self.assertEqual(request["Key"], "offers/n8n-installation.png")
        self.assertEqual(request["ContentType"], "image/png")
        self.assertEqual(request["ExpectedBucketOwner"], "797851739507")
        self.assertNotIn("ACL", request)
        self.assertNotIn("IfNoneMatch", request)
        self.assertEqual(url, screenshots.PUBLIC_BASE + "/offers/n8n-installation.png")

    def test_language_aliases_normalize_and_unsupported_values_fail(self):
        for value, expected in (("EN", "en"), ("en-US", "en"), ("zh", "zh-CN"), ("ZH-cn", "zh-CN")):
            with self.subTest(value=value):
                self.assertEqual(screenshots.resolve_language(value).id, expected)
        for value in ("", "fr", "zh-TW", None):
            with self.subTest(value=value), self.assertRaises(screenshots.ScreenshotError) as raised:
                screenshots.resolve_language(value)
            self.assertNotIn("fr", str(raised.exception))

    def test_english_keeps_historical_name_and_other_languages_add_suffix(self):
        self.assertEqual(screenshots.DEFAULT_LANGUAGE.filename_suffix, "")
        self.assertEqual(screenshots.LANGUAGES["zh-CN"].filename_suffix, "-zh-CN")

    def test_non_english_run_suffixes_local_file_and_upload_key(self):
        browser = mock.MagicMock()
        browser.capture.return_value = png()
        client = mock.Mock()
        with mock.patch.object(screenshots, "create_s3_client", return_value=client):
            report = self.run_batch(browser, language="zh-CN")
        self.assertEqual(report["language"], "zh-CN")
        self.assertEqual(report["results"][0]["file"], "n8n-installation-zh-CN.png")
        self.assertTrue((self.directory / "output/n8n-installation-zh-CN.png").is_file())
        self.assertEqual(client.put_object.call_args_list[0].kwargs["Key"],
                         "offers/n8n-installation-zh-CN.png")
        self.assertEqual(report["results"][0]["url"],
                         screenshots.PUBLIC_BASE + "/offers/n8n-installation-zh-CN.png")

    def test_failed_identity_never_uploads_and_next_app_continues(self):
        browser = mock.MagicMock()
        browser.capture.side_effect = [screenshots.ScreenshotError("detail_identity_failed"), png()]
        client = mock.Mock()
        with mock.patch.object(screenshots, "create_s3_client", return_value=client):
            report = self.run_batch(browser)
        self.assertEqual((report["succeeded"], report["failed"]), (1, 1))
        client.put_object.assert_called_once()
        self.assertEqual(client.put_object.call_args.kwargs["Key"], "offers/mysql-installation.png")
        self.assertFalse((self.directory / "output/n8n-installation.png").exists())

    def test_bad_image_never_uploads(self):
        browser = mock.MagicMock()
        browser.capture.return_value = png(800, 600)
        client = mock.Mock()
        with mock.patch.object(screenshots, "create_s3_client", return_value=client):
            report = self.run_batch(browser)
        client.put_object.assert_not_called()
        self.assertEqual(report["failed"], 2)

    def test_upload_failure_is_sanitized_and_next_app_continues(self):
        browser = mock.MagicMock()
        browser.capture.return_value = png()
        client = mock.Mock()
        client.put_object.side_effect = [RuntimeError("fixture-secret-token"), {}]
        with mock.patch.object(screenshots, "create_s3_client", return_value=client):
            report = self.run_batch(browser)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["results"][0]["error"], "upload_failed")
        self.assertNotIn("fixture-secret-token", json.dumps(report))

    def test_s3_error_classification_does_not_expose_sensitive_sdk_messages(self):
        client = mock.Mock()
        for code, expected in (("AccessDenied", "upload_access_denied"),
                               ("ExpiredToken", "upload_credentials_expired"),
                               ("InvalidAccessKeyId", "upload_invalid_credentials"),
                               ("NoSuchBucket", "upload_bucket_not_found"),
                               ("UnknownError", "upload_failed")):
            error = RuntimeError("fixture-secret-token")
            error.response = {"Error": {"Code": code, "Message": "fixture-secret-token"}}
            client.put_object.side_effect = error
            with self.subTest(code=code), self.assertRaises(screenshots.ScreenshotError) as raised:
                screenshots.upload_screenshot(client, "wordpress", png())
            self.assertEqual(str(raised.exception), expected)
            self.assertNotIn("fixture-secret-token", str(raised.exception))

    def test_login_failure_marks_all_items_and_writes_report(self):
        with mock.patch("cli.appstore_browser.AppStoreBrowser") as browser:
            browser.return_value.__enter__.side_effect = screenshots.ScreenshotError("login_failed")
            report = screenshots.run_screenshots(
                self.apps, credentials=self.credentials, output_dir=self.directory / "output", upload=False,
            )
        self.assertEqual(report["failed"], 2)
        self.assertEqual(report["error"], "login_failed")
        self.assertEqual(json.loads((self.directory / "output/report.json").read_text()), report)

    def test_raw_browser_error_is_never_exported(self):
        browser = mock.MagicMock()
        browser.capture.side_effect = RuntimeError("fixture-password sensitive-url")
        report = self.run_batch(browser, upload=False)
        serialized = json.dumps(report) + screenshots.render_summary(report)
        self.assertNotIn("fixture-password", serialized)
        self.assertNotIn("sensitive-url", serialized)
        self.assertEqual(report["failed"], 2)

    def test_summary_has_copyable_failed_ids_and_keeps_detail_table(self):
        report = {"succeeded": 1, "failed": 2, "results": [
            {"appid": "moodle", "status": "failed", "error": "no_search_results"},
            {"appid": "wordpress", "status": "success"},
            {"appid": "jitsi", "status": "failed", "error": "exact_id_not_found"},
        ]}
        text = screenshots.render_summary(report)
        self.assertIn("1 succeeded, 2 failed", text)
        self.assertIn("Language: en", text)
        self.assertIn("\nFailed list: moodle, jitsi\n", text)
        self.assertIn("| Application | Status | Result |", text)
        self.assertIn("| wordpress | success | Local only |", text)
        self.assertIn("no_search_results: AppStore explicitly reported", text)
        self.assertIn("exact_id_not_found: Multiple results were returned", text)

    def test_summary_has_explicit_empty_failed_list(self):
        text = screenshots.render_summary({"succeeded": 1, "failed": 0, "results": [
            {"appid": "wordpress", "status": "success"},
        ]})
        self.assertIn("\nFailed list: none\n", text)

    def test_summary_reports_selected_language(self):
        text = screenshots.render_summary({"succeeded": 1, "failed": 0, "language": "zh-CN", "results": [
            {"appid": "wordpress", "status": "success"},
        ]})
        self.assertIn("Language: zh-CN", text)


class ScreenshotCommandTests(unittest.TestCase):
    def test_default_batch_reports_missing_ffmpeg_and_continues_to_wordpress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps([
                {"key": "ffmpeg", "trademark": None}, {"key": "wordpress", "trademark": "WordPress"},
            ]))
            with mock.patch("cli.app_screenshots.Credentials.from_environment"), \
                 mock.patch("cli.appstore_browser.AppStoreBrowser") as factory, \
                 mock.patch.object(screenshots, "create_s3_client") as aws:
                browser = factory.return_value.__enter__.return_value
                browser.capture.side_effect = [screenshots.ScreenshotError("no_search_results"), png()]
                result = CliRunner().invoke(cli, [
                    "asset", "screenshots", "--catalog", str(catalog), "--no-upload",
                    "--output-dir", str(root / "output"), "--summary", str(root / "summary.md"),
                ])
            self.assertEqual(result.exit_code, 1, result.output)
            self.assertIn("1 succeeded, 1 failed", result.output)
            self.assertNotIn("must have a trademark", result.output)
            self.assertEqual(browser.capture.call_count, 2)
            factory.return_value.__enter__.assert_called_once()
            aws.assert_not_called()
            report = json.loads((root / "output/report.json").read_text())
            self.assertEqual(report["results"][0]["error"], "no_search_results")
            self.assertEqual(report["results"][1]["status"], "success")
            self.assertIn("no_search_results", (root / "summary.md").read_text())

    def test_command_groups_have_separate_responsibilities(self):
        runner = CliRunner()
        root = runner.invoke(cli, ["--help"])
        assets = runner.invoke(cli, ["asset", "--help"])
        tools = runner.invoke(cli, ["tools", "--help"])
        self.assertEqual(root.exit_code, 0, root.output)
        self.assertEqual(assets.exit_code, 0, assets.output)
        self.assertEqual(tools.exit_code, 0, tools.output)
        self.assertIn("Tools:", root.output)
        self.assertIn("screenshots", assets.output)
        self.assertNotIn("install-browser", assets.output)
        self.assertIn("install-browser", tools.output)
        self.assertNotIn("screenshots", tools.output)
        self.assertEqual(runner.invoke(cli, ["app", "--help"]).exit_code, 2)
        self.assertEqual(runner.invoke(cli, ["asset", "install-browser"]).exit_code, 2)

    def test_help_does_not_require_browser_or_aws(self):
        result = CliRunner().invoke(cli, ["asset", "screenshots", "--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--no-upload", result.output)
        self.assertIn("--lang", result.output)
        self.assertIn("1920x1040", result.output)

    def invoke_batch(self, report, *args):
        with mock.patch("cli.app_screenshots.select_applications", return_value=[{"id": "n8n", "trademark": "n8n"}]), \
             mock.patch("cli.app_screenshots.Credentials.from_environment"), \
             mock.patch("cli.app_screenshots.run_screenshots", return_value=report) as run:
            result = CliRunner().invoke(cli, ["asset", "screenshots", *args])
        return result, run

    def test_cli_delegates_to_one_shared_implementation(self):
        result, run = self.invoke_batch({"succeeded": 1, "failed": 0}, "--no-upload")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertFalse(run.call_args.kwargs["upload"])
        self.assertEqual(run.call_args.kwargs["timeout"], 3)
        self.assertEqual(run.call_args.kwargs["language"], screenshots.LANGUAGES["en"])

    def test_cli_normalizes_language_and_rejects_unsupported_values(self):
        result, run = self.invoke_batch({"succeeded": 1, "failed": 0}, "--no-upload", "--lang", "zh")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(run.call_args.kwargs["language"].id, "zh-CN")
        for value in ("fr", "zh-TW"):
            with self.subTest(value=value):
                result = CliRunner().invoke(cli, ["asset", "screenshots", "--lang", value])
                self.assertEqual(result.exit_code, 2, result.output)

    def test_timeout_cannot_exceed_three_seconds(self):
        result = CliRunner().invoke(cli, ["asset", "screenshots", "--timeout", "4"])
        self.assertEqual(result.exit_code, 2, result.output)

    def test_default_output_is_repository_relative_not_working_directory_relative(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result, run = self.invoke_batch({"succeeded": 1, "failed": 0}, "--no-upload")
        self.assertEqual(result.exit_code, 0, result.output)
        expected = Path(__file__).resolve().parents[1] / "data" / ".work" / "screenshots"
        self.assertEqual(run.call_args.kwargs["output_dir"], expected)

    def test_explicit_output_directory_is_preserved(self):
        result, run = self.invoke_batch({"succeeded": 1, "failed": 0},
                                       "--no-upload", "--output-dir", "custom-screenshots")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(run.call_args.kwargs["output_dir"], Path("custom-screenshots"))

    def test_cli_fails_on_partial_failure_or_fatal_error(self):
        for report in ({"succeeded": 0, "failed": 1}, {"succeeded": 1, "failed": 0, "error": "browser_setup_failed"}):
            result, _ = self.invoke_batch(report)
            self.assertEqual(result.exit_code, 1, result.output)

    def test_allow_app_failures_succeeds_and_warns_even_when_all_apps_fail(self):
        for succeeded, failed in ((1, 1), (0, 2)):
            with self.subTest(succeeded=succeeded, failed=failed):
                result, _ = self.invoke_batch({"succeeded": succeeded, "failed": failed}, "--allow-app-failures")
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("Warning:", result.output)

    def test_allow_app_failures_does_not_hide_fatal_errors(self):
        result, _ = self.invoke_batch({"succeeded": 0, "failed": 2, "error": "login_failed"}, "--allow-app-failures")
        self.assertEqual(result.exit_code, 1, result.output)

    def test_cli_rejects_profile_in_local_only_mode(self):
        result = CliRunner().invoke(cli, ["asset", "screenshots", "--no-upload", "--profile", "test"])
        self.assertEqual(result.exit_code, 2, result.output)

    def test_cli_appends_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.md"
            path.write_text("Existing summary\n")
            result, _ = self.invoke_batch({"succeeded": 1, "failed": 0, "results": [
                {"appid": "n8n", "status": "success", "url": screenshots.PUBLIC_BASE + "/offers/n8n-installation.png"},
            ]}, "--summary", str(path))
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(path.read_text().startswith("Existing summary\n"))
            self.assertIn("1920 × 1040", path.read_text())


class BrowserRetryTests(unittest.TestCase):
    def setUp(self):
        self.browser = AppStoreBrowser(screenshots.Credentials("https://example.test/", "fixture", "fixture"))
        self.app = {"id": "wordpress", "trademark": "Ignored display name"}

    def test_network_failure_retries_once_and_can_recover(self):
        with mock.patch.object(self.browser, "_capture_once", side_effect=[
            screenshots.ScreenshotError("network_error"), png(),
        ]) as attempt:
            self.assertEqual(self.browser.capture(self.app), png())
        self.assertEqual(attempt.call_count, 2)
        self.assertEqual(self.browser.last_attempts, 2)

    def test_loading_failures_stop_after_one_retry(self):
        for code in ("page_load_timeout", "search_loading_timeout", "search_results_timeout", "detail_loading_timeout"):
            with self.subTest(code=code), mock.patch.object(self.browser, "_capture_once", side_effect=
                    screenshots.ScreenshotError(code)) as attempt:
                with self.assertRaises(screenshots.ScreenshotError):
                    self.browser.capture(self.app)
                self.assertEqual(attempt.call_count, 2)

    def test_deterministic_failures_are_not_retried(self):
        for code in ("no_search_results", "exact_id_not_found", "result_id_unavailable", "session_unauthorized", "detail_image_failed"):
            with self.subTest(code=code), mock.patch.object(self.browser, "_capture_once", side_effect=
                    screenshots.ScreenshotError(code)) as attempt:
                with self.assertRaises(screenshots.ScreenshotError):
                    self.browser.capture(self.app)
                self.assertEqual(attempt.call_count, 1)

    def test_programmatic_timeout_is_capped_and_attempt_count_resets_per_app(self):
        browser = AppStoreBrowser(self.browser.credentials, timeout=30)
        self.assertEqual(browser.timeout_ms, 3000)
        with mock.patch.object(browser, "_capture_once", side_effect=[
            screenshots.ScreenshotError("network_error"), png(), png(),
        ]):
            browser.capture(self.app)
            self.assertEqual(browser.last_attempts, 2)
            browser.capture(self.app)
            self.assertEqual(browser.last_attempts, 1)


class BrowserLanguageTests(unittest.TestCase):
    def setUp(self):
        self.browser = AppStoreBrowser(screenshots.Credentials("https://example.test/", "fixture", "fixture"),
                                       language="zh-CN")

    def test_auth_status_is_localized_without_touching_the_account(self):
        response = mock.Mock()
        response.json.return_value = {"authenticated": True,
                                      "current_user": {"id": "user-1", "locale": "en"}}
        route = mock.Mock()
        route.fetch.return_value = response
        self.browser._localize_status(route, mock.Mock())
        route.fulfill.assert_called_once()
        self.assertEqual(route.fulfill.call_args.kwargs["response"], response)
        self.assertEqual(route.fulfill.call_args.kwargs["json"]["current_user"]["locale"], "zh-CN")

    def test_payloads_without_a_user_or_json_are_forwarded_unchanged(self):
        response = mock.Mock()
        response.json.return_value = {"authenticated": False}
        route = mock.Mock()
        route.fetch.return_value = response
        self.browser._localize_status(route, mock.Mock())
        route.fulfill.assert_called_once_with(response=response)

        response = mock.Mock()
        response.json.side_effect = ValueError("not json")
        route = mock.Mock()
        route.fetch.return_value = response
        self.browser._localize_status(route, mock.Mock())
        route.fulfill.assert_called_once_with(response=response)

    def test_failed_fetch_falls_back_to_the_browser_request(self):
        route = mock.Mock()
        route.fetch.side_effect = RuntimeError("fixture")
        self.browser._localize_status(route, mock.Mock())
        route.continue_.assert_called_once()
        route.fulfill.assert_not_called()


class BrowserInstallCommandTests(unittest.TestCase):
    def test_installs_chromium_using_the_current_mk_interpreter(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0)
        with mock.patch.object(tools_commands.importlib.util, "find_spec", return_value=object()), \
             mock.patch.object(tools_commands.subprocess, "run", return_value=completed) as run:
            result = CliRunner().invoke(cli, ["tools", "install-browser"])
        self.assertEqual(result.exit_code, 0, result.output)
        run.assert_called_once_with([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
        self.assertIn("Chromium is ready", result.output)

    def test_missing_playwright_points_to_make_install(self):
        with mock.patch.object(tools_commands.importlib.util, "find_spec", return_value=None), \
             mock.patch.object(tools_commands.subprocess, "run") as run:
            result = CliRunner().invoke(cli, ["tools", "install-browser"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("make install", result.output)
        run.assert_not_called()

    def test_browser_install_failure_is_reported(self):
        completed = subprocess.CompletedProcess(args=[], returncode=1)
        with mock.patch.object(tools_commands.importlib.util, "find_spec", return_value=object()), \
             mock.patch.object(tools_commands.subprocess, "run", return_value=completed):
            result = CliRunner().invoke(cli, ["tools", "install-browser"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("installation failed", result.output)


class InstallDocumentationTests(unittest.TestCase):
    def test_make_cli_preserves_existing_environment_when_dependencies_are_missing(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "venv"
            binary = environment / "bin"
            binary.mkdir(parents=True)
            python = binary / "python"
            python.write_text('#!/bin/sh\nexit 1\n')
            python.chmod(0o700)
            marker = environment / "existing-user-file"
            marker.write_text("preserve")
            result = subprocess.run(["make", "cli", f"CLI_VENV={environment}", "ARGS=help"],
                                    cwd=root, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("make install", result.stderr)
            self.assertEqual(marker.read_text(), "preserve")

    def test_make_install_and_browser_shortcut_are_documented(self):
        root = Path(__file__).resolve().parents[1]
        makefile = (root / "Makefile").read_text()
        spec = (root / "specs/appstore-screenshots.md").read_text()
        self.assertIn("install", makefile.split(".PHONY:", 1)[1].splitlines()[0])
        self.assertIn(".[aws,aliyun,screenshots]", makefile)
        self.assertIn("make install", spec)
        self.assertIn("tools install-browser", spec)


class ScreenshotWorkflowTests(unittest.TestCase):
    def test_workflow_uses_shared_cli_and_limits_writes(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/appstore-screenshots.yml"
        text = path.read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertIn('mk asset screenshots "${args[@]}"', text)
        self.assertIn('--summary "$GITHUB_STEP_SUMMARY" --allow-app-failures', text)
        self.assertNotIn("continue-on-error:", text)
        self.assertIn("role-to-assume: ${{ vars.AWS_ROLE_ARN }}", text)
        self.assertIn("id-token: write", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("group: appstore-screenshots", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn('"--apps=$APPS"', text)
        self.assertIn('--lang "$APPSTORE_LANG"', text)
        self.assertIn("language:", text)
        self.assertIn('*-installation*.png', text)
        self.assertNotIn("${{ inputs.apps }}", text.split("        run: |")[-1])
        self.assertIn("path: ${{ runner.temp }}/${{ env.SCREENSHOTS_SUBDIR }}/report.json", text)
        self.assertNotIn(".secrets/", text)


if __name__ == "__main__":
    unittest.main()
