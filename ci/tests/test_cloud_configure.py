from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ci/cloud_configure.py"
sys.path.insert(0, str(ROOT / "ci"))
import cloud_configure  # noqa: E402


class CloudConfigureTests(unittest.TestCase):
    def test_templates_cover_every_target_and_contain_no_secrets(self):
        targets = cloud_configure.configured_targets()
        self.assertEqual(set(targets), set(cloud_configure.TEMPLATES))
        for target in targets:
            text = cloud_configure.render(target)
            self.assertIn(f"Local configuration for {target}", text)
            self.assertNotIn("AKIA", text)
            self.assertNotIn("secret-value", text)

    def test_contentful_and_wecom_have_their_own_files(self):
        contentful = cloud_configure.render("contentful")
        self.assertIn("CONTENTFUL_SPACE_ID=", contentful)
        self.assertIn("CONTENTFUL_TOKEN=", contentful)
        self.assertIn("CONTENTFUL_ENVIRONMENT=master", contentful)
        self.assertNotIn("WECOM_", contentful)
        wecom = cloud_configure.render("wecom")
        self.assertIn("WECOM_WEBHOOK=", wecom)
        self.assertNotIn("CONTENTFUL_", wecom)

    def test_platform_files_do_not_mix_credentials(self):
        aws = cloud_configure.render("aws")
        aliyun = cloud_configure.render("aliyun")
        self.assertNotIn("ALIBABA_CLOUD", aws)
        self.assertNotIn("CONTENTFUL_", aws)
        self.assertNotIn("WECOM_", aws)
        self.assertNotIn("AWS_ACCESS_KEY_ID", aliyun)
        self.assertNotIn("AZURE_", aliyun)

    def test_all_generates_every_configured_platform(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name)
            with mock.patch.object(cloud_configure, "DEFAULT_DIR", output), \
                    mock.patch.object(sys, "argv", ["cloud_configure.py", "--cloud", "all"]):
                self.assertEqual(cloud_configure.main(), 0)
            self.assertEqual(
                {path.stem for path in output.glob("*.env")},
                set(cloud_configure.configured_targets()),
            )

    def test_all_skips_existing_files_without_force(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name)
            existing = output / "aws.env"
            existing.write_text("keep-me\n")
            with mock.patch.object(cloud_configure, "DEFAULT_DIR", output), \
                    mock.patch.object(sys, "argv", ["cloud_configure.py", "--cloud", "all"]):
                self.assertEqual(cloud_configure.main(), 0)
            self.assertEqual(existing.read_text(), "keep-me\n")

    def test_aws_template_offers_profile_and_key_options(self):
        text = cloud_configure.render("aws")
        self.assertIn("# AWS_PROFILE=marketplace-readonly", text)
        self.assertIn("AWS_ACCESS_KEY_ID=", text)
        self.assertIn("AWS_SECRET_ACCESS_KEY=", text)
        self.assertIn("AWS_SESSION_TOKEN=", text)
        self.assertIn("AWS_DEFAULT_REGION=us-east-1", text)

    def test_unknown_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown target"):
            cloud_configure.render("not-a-cloud")

    def test_default_output_is_under_gitignored_secrets_directory(self):
        self.assertEqual(cloud_configure.DEFAULT_DIR, ROOT / ".secrets")
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".secrets/", ignored)

    def test_write_is_owner_only_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "aws.env"
            cloud_configure.write_template(path, "AWS_DEFAULT_REGION=us-east-1\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                cloud_configure.write_template(path, "new content\n")
            self.assertEqual(path.read_text(), "AWS_DEFAULT_REGION=us-east-1\n")

    def test_force_replaces_an_existing_file(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "aws.env"
            cloud_configure.write_template(path, "old\n")
            cloud_configure.write_template(path, "new\n", force=True)
            self.assertEqual(path.read_text(), "new\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cli_writes_a_loadable_file_under_requested_output(self):
        with tempfile.TemporaryDirectory() as name:
            output = Path(name) / "aws.env"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--cloud", "aws", "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            text = output.read_text()
            self.assertIn("AWS_ACCESS_KEY_ID=", text)
            self.assertNotIn("CONTENTFUL_", text)
            self.assertIn(f"mk --env-file {output}", result.stdout)


if __name__ == "__main__":
    unittest.main()
