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

    def test_rejects_empty_unsafe_or_incomplete_catalog(self):
        for value in ([], [{"key": "../bad", "trademark": "bad"}], [{"key": "n8n"}]):
            self.catalog.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(screenshots.ScreenshotError):
                screenshots.select_applications(str(self.catalog))

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


class ScreenshotCommandTests(unittest.TestCase):
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
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

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
        self.assertIn("role-to-assume: ${{ vars.AWS_ROLE_ARN }}", text)
        self.assertIn("id-token: write", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("group: appstore-screenshots", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn('"--apps=$APPS"', text)
        self.assertNotIn("${{ inputs.apps }}", text.split("        run: |")[-1])
        self.assertIn("path: ${{ runner.temp }}/${{ env.SCREENSHOTS_SUBDIR }}/report.json", text)
        self.assertNotIn(".secrets/", text)


if __name__ == "__main__":
    unittest.main()
