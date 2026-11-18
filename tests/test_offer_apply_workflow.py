from __future__ import annotations

import os
import re
import subprocess
import textwrap
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/offer-apply.yml"


class ApplyWorkflowInputTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_form_has_only_four_compact_inputs(self):
        inputs = self.text.split("    inputs:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertEqual(re.findall(r"^      ([a-z_]+):$", inputs, re.MULTILINE),
                         ["appid", "cloud", "product_code", "label"])
        self.assertIn('description: "appid, e.g. odoo"', inputs)
        self.assertIn('description: "cloud"', inputs)
        self.assertIn('description: "product code, e.g. xxxx"', inputs)
        self.assertIn('description: "Label: force only for the specified product code"', inputs)
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
        self.assertIn('run-name: "Apply ${{ inputs.appid }} to ${{ inputs.cloud }}"', self.text)
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
                                        env=dict(os.environ, OFFER=label, CODE=code), timeout=5)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                if expected:
                    self.assertIn("Label requires a product code", result.stdout)

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
