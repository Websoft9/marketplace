"""Exercise the actual inline generation shell with a fake CLI, without cloud access."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/offer-codes-matrix.yml"
PLATFORMS = ["aws", "aliyun", "alibabacloud", "azure", "azure-vmlab", "huaweiyun", "huaweicloud"]


def step_text(name):
    text = WORKFLOW.read_text()
    return text.split(f"      - name: {name}\n", 1)[1].split("\n      - name: ", 1)[0]


def script(name):
    return textwrap.dedent(step_text(name).split("        run: |\n", 1)[1])


class MatrixFailureIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        mk = self.bin / "mk"
        mk.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            from pathlib import Path

            if sys.argv[1:3] == ["offer-codes", "verify"]:
                print("Verified mock matrix: 0 errors")
                sys.exit(int(os.environ.get("VERIFY_EXIT", "0")))
            platform = sys.argv[sys.argv.index("--platform") + 1]
            with open(os.environ["CALLS"], "a") as calls:
                calls.write(platform + "\\n")
            failed = platform == os.environ.get("FAIL_PLATFORM")
            report = {"platform": platform, "written": not failed, "checked": 1,
                      "matched": 0 if failed else 1, "unresolved": [], "products": [],
                      "inventory": {"complete": not failed, "errors": []}}
            if failed:
                report["inventory"]["errors"] = [{"code": "InventoryError", "message": "TimeoutError"}]
            else:
                Path(os.environ["MATRIX_FILE"]).write_text("[]\\n")
            print("[]" if failed and os.environ.get("MALFORMED_REPORT") == "1" else json.dumps(report))
            sys.exit(1 if failed else 0)
            """))
        mk.chmod(0o700)
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "PLATFORM": "all", "MATRIX_DIR": str(self.root / "matrix"),
                    "MATRIX_FILE": str(self.root / "matrix/matrix.json"),
                    "GITHUB_OUTPUT": str(self.root / "outputs"),
                    "GITHUB_STEP_SUMMARY": str(self.root / "summary"), "CALLS": str(self.root / "calls"),
                    "AWS_READY": "true", "ALIYUN_READY": "true", "ALIBABACLOUD_READY": "true",
                    "AZURE_READY": "true", "AZURE_VMLAB_READY": "true",
                    "FAIL_PLATFORM": "", "VERIFY_EXIT": "0", "MALFORMED_REPORT": "0"}

    def run_step(self, name):
        return subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script(name)],
                              env=self.env, capture_output=True, text=True, timeout=30)

    def status(self):
        return json.loads((self.root / "matrix/run-status.json").read_text())

    def test_vmlab_timeout_still_runs_both_huawei_platforms_and_blocks_publication(self):
        self.env["FAIL_PLATFORM"] = "azure-vmlab"
        result = self.run_step("Generate offer-code matrix")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.root / "calls").read_text().splitlines(), PLATFORMS)
        status = self.status()
        self.assertFalse(status["complete"])
        self.assertFalse(status["combinedPublishEligible"])
        self.assertEqual(status["platforms"]["azure-vmlab"], "generation_failed")
        self.assertEqual(status["platforms"]["huaweiyun"], "success")
        self.assertEqual(status["platforms"]["huaweicloud"], "success")
        self.assertIn("complete=false", (self.root / "outputs").read_text())
        self.assertEqual(self.run_step("Summarize generation results").returncode, 0)
        summary = (self.root / "summary").read_text()
        self.assertIn("R2 publication blocked", summary)
        self.assertIn("| huaweicloud | success |", summary)
        self.assertIn("| azure-vmlab | generation_failed |", summary)

    def test_auth_failure_only_skips_its_own_platform_and_emits_failed_report(self):
        self.env["ALIYUN_READY"] = "false"
        self.env["AZURE_VMLAB_READY"] = "false"
        result = self.run_step("Generate offer-code matrix")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.root / "calls").read_text().splitlines(),
                         [p for p in PLATFORMS if p not in ("aliyun", "azure-vmlab")])
        status = self.status()
        for platform in ("aliyun", "azure-vmlab"):
            self.assertEqual(status["platforms"][platform], "credential_setup_failed")
            report = json.loads((self.root / f"matrix/generation-{platform}.json").read_text())
            self.assertFalse(report["written"])
            self.assertEqual(report["inventory"]["errors"][0]["code"], "CredentialSetupFailed")
        self.assertFalse(status["combinedPublishEligible"])

    def test_success_is_publish_eligible_and_all_reports_are_present(self):
        result = self.run_step("Generate offer-code matrix")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.status()["combinedPublishEligible"])
        self.assertTrue(all(v == "success" for v in self.status()["platforms"].values()))
        self.assertTrue((self.root / "outputs").read_text().endswith("complete=true\n"))
        for platform in PLATFORMS:
            self.assertTrue((self.root / f"matrix/generation-{platform}.json").is_file())

    def test_verification_failure_blocks_publication_even_if_every_platform_writes(self):
        self.env["VERIFY_EXIT"] = "1"
        result = self.run_step("Generate offer-code matrix")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(all(v == "success" for v in self.status()["platforms"].values()))
        self.assertFalse(self.status()["combinedPublishEligible"])

    def test_invalid_failure_report_does_not_stop_later_platforms_or_summary(self):
        self.env.update(FAIL_PLATFORM="azure-vmlab", MALFORMED_REPORT="1")
        result = self.run_step("Generate offer-code matrix")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.root / "calls").read_text().splitlines(), PLATFORMS)
        self.assertFalse(self.status()["combinedPublishEligible"])
        self.assertEqual(self.run_step("Summarize generation results").returncode, 0)

    def test_single_public_platform_does_not_depend_on_any_credentials(self):
        self.env["PLATFORM"] = "huaweicloud"
        for key in list(self.env):
            if key.endswith("_READY"):
                self.env[key] = "false"
        result = self.run_step("Generate offer-code matrix")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.status()["platforms"], {"huaweicloud": "success"})

    def test_workflow_gates_auth_by_outcome_but_only_publishes_complete_generation(self):
        checks = ["Check AWS role configuration", "Configure AWS credentials with GitHub OIDC",
                  "Check Aliyun role configuration", "Configure Aliyun credentials with GitHub OIDC",
                  "Expose Aliyun credential names", "Report Aliyun caller identity",
                  "Check Alibaba Cloud International role configuration",
                  "Configure Alibaba Cloud International credentials with GitHub OIDC",
                  "Check Azure publisher credentials", "Check Azure VMLab publisher credentials"]
        for name in checks:
            self.assertIn("continue-on-error: true", step_text(name))
        for name, dependency in (("Configure AWS credentials with GitHub OIDC", "aws_config"),
                                 ("Configure Aliyun credentials with GitHub OIDC", "aliyun_config"),
                                 ("Expose Aliyun credential names", "aliyun_auth"),
                                 ("Configure Alibaba Cloud International credentials with GitHub OIDC", "alibabacloud_config")):
            self.assertIn(f"steps.{dependency}.outcome == 'success'", step_text(name))
        self.assertNotIn("continue-on-error: true", step_text("Generate offer-code matrix"))
        self.assertIn("if: always()", step_text("Upload offer-code matrix"))
        self.assertIn("if: success() && steps.generate_matrix.outputs.complete == 'true'",
                      step_text("Publish matrix to Cloudflare R2"))


if __name__ == "__main__":
    unittest.main()
