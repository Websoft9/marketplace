from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from cli import env_file
from cli.cli import cli


class ParseTests(unittest.TestCase):
    def test_comments_export_quotes_and_blank_lines(self):
        text = """
# comment
export AWS_PROFILE="marketplace-readonly"
AWS_DEFAULT_REGION='us-east-1'

CONTENTFUL_ENVIRONMENT=master
"""
        self.assertEqual(env_file.parse(text), {
            "AWS_PROFILE": "marketplace-readonly",
            "AWS_DEFAULT_REGION": "us-east-1",
            "CONTENTFUL_ENVIRONMENT": "master",
        })

    def test_invalid_line_and_key_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "expected KEY=VALUE"):
            env_file.parse("NOT_AN_ASSIGNMENT\n")
        with self.assertRaisesRegex(ValueError, "invalid variable name"):
            env_file.parse("1BAD=value\n")
        with self.assertRaisesRegex(ValueError, "invalid variable name"):
            env_file.parse("BAD-KEY=value\n")

    def test_existing_environment_wins_by_default(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "test.env"
            path.write_text("MK_ENV_TEST=from-file\n")
            path.chmod(0o600)
            with mock.patch.dict(os.environ, {"MK_ENV_TEST": "from-shell"}, clear=False):
                loaded = env_file.load(path)
                self.assertEqual(os.environ["MK_ENV_TEST"], "from-shell")
                self.assertNotIn("MK_ENV_TEST", loaded)
                loaded = env_file.load(path, override=True)
                self.assertEqual(os.environ["MK_ENV_TEST"], "from-file")
                self.assertEqual(loaded, {"MK_ENV_TEST": "from-file"})

    def test_missing_file_is_reported(self):
        with self.assertRaisesRegex(ValueError, "cannot read env file"):
            env_file.load(Path("/tmp/opencode/does-not-exist.env"))


class CliOptionTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "aws.env"
        self.path.write_text("MK_ENV_CLI_TEST=loaded\n")
        self.path.chmod(0o600)
        self.addCleanup(os.environ.pop, "MK_ENV_CLI_TEST", None)

    def test_env_file_option_loads_before_the_command(self):
        os.environ.pop("MK_ENV_CLI_TEST", None)
        result = self.runner.invoke(cli, ["--env-file", str(self.path), "help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(os.environ["MK_ENV_CLI_TEST"], "loaded")

    def test_mk_env_file_variable_is_honoured(self):
        os.environ.pop("MK_ENV_CLI_TEST", None)
        with mock.patch.dict(os.environ, {"MK_ENV_FILE": str(self.path)}, clear=False):
            result = self.runner.invoke(cli, ["help"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(os.environ["MK_ENV_CLI_TEST"], "loaded")

    def test_missing_env_file_fails_before_the_command(self):
        result = self.runner.invoke(
            cli, ["--env-file", str(Path(self.temp.name) / "missing.env"), "help"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("cannot read env file", result.output)


if __name__ == "__main__":
    unittest.main()
