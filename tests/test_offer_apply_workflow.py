from __future__ import annotations

import os
import json
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/offer-apply.yml"


class ApplyWorkflowInputTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_form_has_five_compact_inputs_with_update_type_after_cloud(self):
        inputs = self.text.split("    inputs:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"^      ([a-z_]+):$", inputs, re.MULTILINE),
                         ["appid", "cloud", "update_type", "product_code", "label"])
        self.assertIn('description: "appid, e.g. odoo"', inputs)
        self.assertIn('description: "cloud"', inputs)
        self.assertIn('description: "product code, e.g. xxxx"', inputs)
        self.assertIn('description: "Label: force only for the specified code"', inputs)
        mode = inputs.split("      update_type:\n", 1)[1].split("      product_code:", 1)[0]
        self.assertIn("type: choice", mode)
        self.assertIn("default: Product information", mode)
        self.assertEqual(re.findall(r"^          - (.+)$", mode, re.MULTILINE),
                         ["Product information", "Asset"])
        label = inputs.split("      label:\n", 1)[1]
        self.assertIn("default: Auto", label)
        self.assertIn("- Auto", label)
        self.assertIn("- Remove label", label)
        self.assertNotRegex(label, r"- (?:none|base)\b")
        self.assertNotIn("app_name", self.text)
        self.assertNotIn("APP_NAME", self.text)
        self.assertNotIn("--app-name", self.text)

    def test_all_consumers_use_the_renamed_input_keys(self):
        self.assertNotRegex(self.text, r"inputs\.(?:app|code|offer|app_name)\b")
        self.assertIn('run-name: "Apply ${{ inputs.update_type }} for ${{ inputs.appid }} to ${{ inputs.cloud }}"', self.text)
        self.assertEqual(self.text.count("UPDATE_TYPE: ${{ inputs.update_type }}"), 2)
        self.assertIn("CODE: ${{ inputs.product_code }}", self.text)
        self.assertIn("OFFER: ${{ inputs.label }}", self.text)
        self.assertEqual(self.text.count("APP: ${{ inputs.appid }}"), 2)
        self.assertEqual(self.text.count("offer-plan-${{ inputs.cloud }}-${{ inputs.appid }}"), 2)
        self.assertIn('args+=("--code=$CODE")', self.text)
        self.assertIn('args+=("--offer=$OFFER")', self.text)

    def test_label_requires_product_code_before_any_setup_or_cloud_access(self):
        block = self.text.split("      - name: Validate workflow inputs\n", 1)[1].split("      - name:", 1)[0]
        script = textwrap.dedent(block.split("run: |\n", 1)[1])
        self.assertLess(self.text.index("Validate workflow inputs"), self.text.index("Check out repository"))
        for label, code, expected in (("Auto", "", 0), ("", "", 0), ("Ubuntu", "", 1),
                                      ("Remove label", "", 1), ("Remove label", "  \t", 1),
                                      ("Remove label", "product-code", 0), ("Ubuntu", "product-code", 0),
                                      ("Multisite", "product-code", 0)):
            with self.subTest(label=label, code=code):
                result = subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                                        env=dict(os.environ, OFFER=label, CODE=code,
                                                 UPDATE_TYPE="Product information"), timeout=5)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if expected:
                    self.assertIn("Label requires a product code", result.stdout)

    def step_script(self, name):
        block = self.text.split(f"      - name: {name}\n", 1)[1].split("      - name:", 1)[0]
        return textwrap.dedent(block.split("run: |\n", 1)[1])

    def test_asset_requires_auto_label_and_rejects_unknown_mode_before_setup(self):
        script = self.step_script("Validate workflow inputs")
        for mode, label, expected in (("Asset", "Auto", 0), ("Asset", "", 0),
                                      ("Asset", "Ubuntu", 1), ("Asset", "Remove label", 1),
                                      ("Both", "Auto", 1)):
            with self.subTest(mode=mode, label=label):
                result = subprocess.run(["bash", "-e", "-c", script], text=True, capture_output=True,
                                        env=dict(os.environ, UPDATE_TYPE=mode, OFFER=label, CODE="product-code"),
                                        timeout=5)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_modes_build_plan_and_apply_mutually_exclusive_content_using_reviewed_artifacts(self):
        for mode in ("Product information", "Asset"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run = root / "offer-run"
                run.mkdir()
                (run / "catalog.json").write_text('{"items": []}')
                (run / "apps-verson.json").write_text('{}')
                executable = root / "mk"
                executable.write_text(textwrap.dedent('''\
                    #!/usr/bin/env python3
                    import json, os, sys
                    from pathlib import Path
                    args = sys.argv[1:]
                    with open(os.environ["MK_CALLS"], "a") as log:
                        log.write(json.dumps(args) + "\\n")
                    if "--output" in args:
                        path = Path(args[args.index("--output") + 1])
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(json.dumps({"planHash": "a" * 64}))
                    elif args[:2] == ["offer", "apply"]:
                        print(json.dumps({"status": "SUBMITTED", "changeSetId": "cs-1"}))
                    else:
                        print("Submission summary")
                    '''))
                executable.chmod(0o755)
                env = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                           APP="odoo", CLOUD="aws", UPDATE_TYPE=mode,
                           MK_CALLS=str(root / "calls.jsonl"), GITHUB_STEP_SUMMARY=str(root / "summary.md"))
                for step in ("Build preview", "Generate revision-bound plan", "Submit reviewed plan"):
                    script = self.step_script(step).replace("/tmp/opencode/offer-run", str(run))
                    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script],
                                            cwd=root, env=env, text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
                build, plan, apply, report = calls
                self.assertEqual(plan[:2], ["offer", "plan"])
                self.assertIn("--all-codes", plan)
                self.assertEqual(apply[:2], ["offer", "apply"])
                self.assertEqual(apply[apply.index("--wait-seconds") + 1], "0")
                self.assertEqual(apply[apply.index("--plan") + 1], str(run / "plan.json"))
                self.assertEqual(report[:3], ["offer", "report", "apply"])
                if mode == "Asset":
                    self.assertEqual(build[:3], ["offer", "assets", "build"])
                    for args in (plan, apply):
                        self.assertEqual(args[args.index("--assets") + 1], str(run / "assets.json"))
                    self.assertEqual(apply[apply.index("--approve-plan-hash") + 1], "a" * 64)
                    self.assertEqual((run / "assets.json").read_text(), (run / "preview.json").read_text())
                else:
                    self.assertEqual(build[:2], ["offer", "build"])
                    self.assertFalse((run / "assets.json").exists())
                    for args in (plan, apply):
                        self.assertNotIn("--assets", args)
                        self.assertNotIn("--approve-plan-hash", args)
                self.assertIn(mode, (root / "summary.md").read_text())

    def test_shared_approval_artifacts_and_lock_are_not_split_by_mode(self):
        self.assertIn("group: marketplace-offer-apply-${{ inputs.cloud }}-${{ inputs.appid }}", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("needs: plan", self.text)
        self.assertIn("name: ${{ inputs.cloud }}-marketplace-production", self.text)
        self.assertEqual(self.text.count("name: offer-plan-${{ inputs.cloud }}-${{ inputs.appid }}-${{ github.run_id }}"), 2)
        self.assertIn("path: /tmp/opencode/offer-run/", self.text)
        apply_job = self.text.split("\n  apply:\n", 1)[1]
        self.assertIn("Download reviewed plan", apply_job)
        self.assertNotIn("mk offer assets build", apply_job)
        self.assertNotIn("mk offer plan", apply_job)
        self.assertNotIn("mk asset screenshots", self.text)

    def test_multiline_shell_steps_have_valid_bash_syntax(self):
        steps = re.findall(r"^      - name: (.+)$", self.text, re.MULTILINE)
        for name in steps:
            block = self.text.split(f"      - name: {name}\n", 1)[1].split("      - name:", 1)[0]
            if "run: |\n" not in block:
                continue
            with self.subTest(step=name):
                result = subprocess.run(["bash", "-n"], input=self.step_script(name), text=True,
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_form_choices_translate_to_existing_cli_semantics(self):
        block = self.text.split("      - name: Resolve public offer targets from store facts\n", 1)[1]
        prefix = block.split("run: |\n", 1)[1].split("          mk offer resolve", 1)[0]
        script = textwrap.dedent(prefix) + '\nfor arg in "${args[@]}"; do printf "%s\\n" "$arg"; done\n'
        for label, code, expected in (
            ("Auto", "", []),
            ("Auto", "product-code", ["--code=product-code"]),
            ("", "product-code", ["--code=product-code"]),
            ("Remove label", "product-code", ["--code=product-code", "--offer=base"]),
            ("Ubuntu", "product-code", ["--code=product-code", "--offer=Ubuntu"]),
            ("Multisite", "product-code", ["--code=product-code", "--offer=Multisite"]),
        ):
            with self.subTest(label=label, code=code):
                result = subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                                        env=dict(os.environ, OFFER=label, CODE=code), timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), expected)


if __name__ == "__main__":
    unittest.main()
