from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ci/scripts/validate_marketplace_ops_skill.py"

SKILL_MD = """\
---
name: marketplace-operations-check
description: Test skill.
---

# Test

| Input | Required | Values | Default |
| --- | --- | --- | --- |
| `checks` | no | Subset of `codes,status` | `codes` |

## Check registry

| Check | Network | Output | Reference |
| --- | --- | --- | --- |
| `codes` | no | local | [codes](references/checks/codes.md) |
| `status` | yes | page | [status](references/checks/status.md) |
"""

CONFIG = {
    "platforms": [
        {"key": "aws", "urlTemplate": "https://example.test/aws/{code}"},
        {"key": "azure", "urlTemplate": "https://example.test/azure/{code}"},
    ]
}

MATRIX = [{"id": "odoo", "productID": {"aws": ["sku-1"], "azure": ["pub.offer"]}}]


class ValidateMarketplaceOpsSkillTests(unittest.TestCase):
    def make_root(self) -> tempfile.TemporaryDirectory:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        skill = root / "skills/marketplace-operations-check"
        (skill / "references/checks").mkdir(parents=True)
        (skill / "references/platforms").mkdir(parents=True)
        (skill / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        for name in ("codes", "status"):
            (skill / f"references/checks/{name}.md").write_text("x\n", encoding="utf-8")
        (skill / "references/platforms/aws.md").write_text(
            "- Listing URL: `https://example.test/aws/{code}`\n", encoding="utf-8"
        )
        (root / "config").mkdir()
        (root / "config/marketplace.json").write_text(json.dumps(CONFIG), encoding="utf-8")
        (root / "data/offer-codes").mkdir(parents=True)
        (root / "data/offer-codes/matrix.json").write_text(json.dumps(MATRIX), encoding="utf-8")
        self.addCleanup(tmp.cleanup)
        return tmp

    def run_validator(self, root: Path) -> tuple[int, list[str]]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root), "--json"],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode, json.loads(result.stdout)["errors"]

    def test_valid_layout_passes(self) -> None:
        root = Path(self.make_root().name)
        code, errors = self.run_validator(root)
        self.assertEqual(errors, [])
        self.assertEqual(code, 0)

    def test_registry_mismatch_fails(self) -> None:
        tmp = self.make_root()
        root = Path(tmp.name)
        (root / "skills/marketplace-operations-check/references/checks/extra.md").write_text(
            "x\n", encoding="utf-8"
        )
        code, errors = self.run_validator(root)
        self.assertNotEqual(code, 0)
        self.assertTrue(any("references/checks differ" in error for error in errors))

    def test_unknown_platform_reference_fails(self) -> None:
        tmp = self.make_root()
        root = Path(tmp.name)
        (root / "skills/marketplace-operations-check/references/platforms/gcp.md").write_text(
            "- Listing URL: `https://example.test/gcp/{code}`\n", encoding="utf-8"
        )
        code, errors = self.run_validator(root)
        self.assertNotEqual(code, 0)
        self.assertTrue(any("no key in config/marketplace.json" in error for error in errors))

    def test_url_drift_fails(self) -> None:
        tmp = self.make_root()
        root = Path(tmp.name)
        (root / "skills/marketplace-operations-check/references/platforms/aws.md").write_text(
            "- Listing URL: `https://example.test/old/{code}`\n", encoding="utf-8"
        )
        code, errors = self.run_validator(root)
        self.assertNotEqual(code, 0)
        self.assertTrue(any("differs from configured" in error for error in errors))

    def test_matrix_platform_missing_from_config_fails(self) -> None:
        tmp = self.make_root()
        root = Path(tmp.name)
        (root / "data/offer-codes/matrix.json").write_text(
            json.dumps([{"id": "odoo", "productID": {"huaweiyun": ["x"]}}]),
            encoding="utf-8",
        )
        code, errors = self.run_validator(root)
        self.assertNotEqual(code, 0)
        self.assertTrue(any("is not in config/marketplace.json" in error for error in errors))

    def test_repository_layout_is_consistent(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, textwrap.dedent(result.stderr))


if __name__ == "__main__":
    unittest.main()
