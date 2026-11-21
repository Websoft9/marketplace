from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli import offer, offer_resolve, os_catalog
from cli.marketplace import CONFIG_PATH


class OsCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "os-catalog.json"
        self.example = {
            "version": 1,
            "versionPattern": r"[0-9]+(?:\.[0-9]+)*",
            "versionPatternScope": "immediately-after-matched-os-name",
            "families": [{"id": "example-linux", "display": "Example Linux",
                          "aliases": ["Example Linux", "Example OS"], "versionExamples": ["13", "26.04"]}],
        }

    def write(self, document):
        self.path.write_text(json.dumps(document), encoding="utf-8")
        return self.path

    def test_existing_consumers_share_configuration_backed_constants(self):
        self.assertIs(offer.OS_NAMES, os_catalog.OS_NAMES)
        self.assertIs(offer_resolve.OS_NAMES, os_catalog.OS_NAMES)
        self.assertIs(offer_resolve.OS_DISPLAY, os_catalog.OS_DISPLAY)
        loaded = os_catalog.load_catalog()
        names, display = os_catalog.alias_maps(loaded)
        self.assertEqual(os_catalog.OS_NAMES, names)
        self.assertEqual(os_catalog.OS_DISPLAY, display)

    def test_legacy_names_and_displays_are_preserved(self):
        expected = {
            "amazon linux": "Amazon Linux", "oracle linux": "Oracle Linux",
            "red hat": "Red Hat", "rocky linux": "Rocky Linux",
            "almalinux": "AlmaLinux", "alma linux": "AlmaLinux",
            "centos": "CentOS", "debian": "Debian", "ubuntu": "Ubuntu",
            "suse": "SUSE", "sles": "SLES", "fedora": "Fedora",
            "windows server": "Windows Server", "freebsd": "FreeBSD", "rhel": "RHEL",
        }
        for alias, display in expected.items():
            with self.subTest(alias=alias):
                self.assertIn(alias, os_catalog.OS_NAMES)
                self.assertEqual(os_catalog.OS_DISPLAY[alias], display)

    def test_loader_preserves_versions_as_examples_not_a_whitelist(self):
        loaded = os_catalog.load_catalog(self.write(self.example))
        self.assertEqual(loaded, self.example)
        names, display = os_catalog.alias_maps(loaded)
        self.assertEqual(names, ("example linux", "example os"))
        self.assertEqual(display["example os"], "Example Linux")
        for text, expected in (("Ubuntu 26.04", "Ubuntu"), ("Debian 13", "Debian"),
                               ("Oracle Linux 9.4", "Oracle Linux"), ("Ubuntu 99.99", "Ubuntu")):
            self.assertEqual(offer_resolve.detect_os(text), [expected])

    def test_bad_catalogs_fail_closed(self):
        mutations = (
            lambda d: d.update(version=2),
            lambda d: d.update(version=True),
            lambda d: d.update(unknown=True),
            lambda d: d.update(versionPattern="["),
            lambda d: d.update(versionPattern=".*"),
            lambda d: d.update(versionPattern=None),
            lambda d: d.update(versionPatternScope="unknown"),
            lambda d: d.update(families=[]),
            lambda d: d.update(families=[None]),
            lambda d: d["families"].append(copy.deepcopy(d["families"][0])),
            lambda d: d["families"][0].update(id="Example Linux"),
            lambda d: d["families"][0].update(display=""),
            lambda d: d["families"][0].update(display=" Example Linux "),
            lambda d: d["families"][0].update(display="Undeclared canonical name"),
            lambda d: d["families"][0].update(aliases=[]),
            lambda d: d["families"][0].update(aliases=[None]),
            lambda d: d["families"][0].update(aliases=[""]),
            lambda d: d["families"][0].update(aliases=[" Example Linux "]),
            lambda d: d["families"][0]["aliases"].append("EXAMPLE   LINUX"),
            lambda d: d["families"].append({"id": "different-family", "display": "Example Linux",
                                           "aliases": ["Example Linux"]}),
            lambda d: d["families"][0].update(versionExamples=[26.04]),
            lambda d: d["families"][0].update(versionExamples=["13", "13"]),
            lambda d: d["families"][0].update(versionExamples=["not-a-version"]),
            lambda d: d["families"][0].update(versionExamples="13"),
            lambda d: d["families"][0].update(unknown=True),
        )
        for mutate in mutations:
            document = copy.deepcopy(self.example)
            mutate(document)
            with self.subTest(document=document):
                with self.assertRaisesRegex(ValueError, "invalid OS catalog"):
                    os_catalog.load_catalog(self.write(document))

    def test_missing_and_malformed_files_do_not_use_a_hardcoded_fallback(self):
        with self.assertRaisesRegex(ValueError, "missing, unreadable or not valid JSON"):
            os_catalog.load_catalog(self.path)
        for text in ("invalid json", "[]", "null", "{}"):
            self.path.write_text(text)
            with self.assertRaisesRegex(ValueError, "invalid OS catalog"):
                os_catalog.load_catalog(self.path)

    def test_new_alias_in_json_reaches_all_consumers_in_a_fresh_process(self):
        # A separate process proves consumers actually read the configuration,
        # rather than merely testing a loader while keeping a code-side OS list.
        config = self.root / "config"
        config.mkdir()
        for filename in ("marketplace.json", "sources.json"):
            (config / filename).write_bytes((CONFIG_PATH.parent / filename).read_bytes())
        (config / "os-catalog.json").write_text(json.dumps(self.example))
        cases = self.root / "cases.json"
        cases.write_text(json.dumps({"schemaVersion": 1, "apps": {
            "odoo": {"multiOffers": ["Example OS", "Example Linux; Multisite"]},
        }}))
        script = """
import json,sys
from pathlib import Path
from cli import offer,offer_resolve
assert offer.is_os_label('Example OS 13')
assert offer_resolve.is_os_part('Example OS')
assert offer_resolve.detect_os('App on Example OS 13') == ['Example Linux']
assert offer_resolve.detect_os('Example Linux and Example OS') == ['Example Linux']
assert not offer_resolve._safe_id_name('example')
assert not offer_resolve.identify('Example Linux', '', {'example':['Example Database']})
assert offer.load_special_cases(Path(sys.argv[1]))['odoo']['multiOffers'][0] == 'Example OS'
print(json.dumps({'names':offer.OS_NAMES,'display':offer_resolve.OS_DISPLAY}))
"""
        env = dict(os.environ, MK_CONFIG_PATH=str(config / "marketplace.json"))
        result = subprocess.run([sys.executable, "-c", script, str(cases)], env=env,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["names"], ["example linux", "example os"])
        self.assertEqual(data["display"]["example os"], "Example Linux")

    def test_offer_labels_remain_family_only_and_do_not_accept_versions(self):
        for label in ("Ubuntu 26.04", "Debian 13", "Oracle Linux 9"):
            self.write({"schemaVersion": 1, "apps": {"odoo": {"multiOffers": [label]}}})
            with self.assertRaisesRegex(ValueError, "OS family name without a version"):
                offer.load_special_cases(self.path)
        self.assertEqual(offer_resolve.detect_offer("Odoo on Oracle Linux 9", "", ["Oracle Linux"]), "Oracle Linux")


if __name__ == "__main__":
    unittest.main()
