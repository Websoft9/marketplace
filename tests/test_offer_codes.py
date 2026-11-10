from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from cli import offer_codes, offer_listing
from cli.cli import cli


PLATFORMS = ("aliyun", "huaweiyun", "alibabacloud", "huaweicloud", "azure", "aws")

VALID_CODES = {
    "aliyun": ["cmjj000001"],
    "huaweiyun": ["11111111-2222-3333-4444-555555555555"],
    "alibabacloud": ["sgcmjj000001"],
    "huaweicloud": [],
    "azure": ["publisher.offer"],
    "aws": ["abcdefghijklmnopqrstuvwxy"],
}


def entry(app: str, suffix: str = "1", **codes: list[str]) -> dict:
    product_id = {
        "aliyun": [f"cmjj0000{suffix}"],
        "huaweiyun": [f"1111111{suffix}-2222-3333-4444-555555555555"],
        "alibabacloud": [f"sgcmjj0000{suffix}"],
        "huaweicloud": [],
        "azure": [f"publisher.offer{suffix}"],
        "aws": [f"abcdefghijklmnopqrstuvwx{suffix}"],
    }
    product_id.update(codes)
    return {
        "id": app,
        "productID": product_id,
        "type": "app",
        "applications": [app],
    }

class OfferCodesVerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.runner = CliRunner()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name: str, value: object) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def catalog(self, *ids: str) -> Path:
        return self.write(
            "catalog.json", {"items": [{"id": app_id} for app_id in ids]}
        )

    def invoke(self, offer_path: Path, catalog_path: Path | None = None, *extra: str):
        args = ["offer-codes", "verify", "--offer", str(offer_path)]
        args.extend([
            "--product-catalog", str(catalog_path or self.catalog("a", "b", "c")),
        ])
        if not any("check-coverage" in item for item in extra):
            args.append("--no-check-coverage")
        args.extend(extra)
        return self.runner.invoke(cli, args)

    def test_valid_dataset_passes(self):
        path = self.write("offer.json", [entry("a", "1"), entry("b", "2")])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("0 error(s), 0 warning(s)", result.output)

    def test_missing_required_field_is_error(self):
        item = entry("a")
        del item["applications"]
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("missing required field 'applications'", result.output)

    def test_missing_platform_key_is_error(self):
        item = entry("a")
        del item["productID"]["aws"]
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("productID.aws", result.output)
        self.assertIn("missing platform key", result.output)

    def test_non_array_codes_is_error(self):
        item = entry("a")
        item["productID"]["aws"] = "abcdefghijklmnopqrstuvwxy"
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("must be an array", result.output)

    def test_duplicate_code_within_entry_is_error(self):
        path = self.write(
            "offer.json", [entry("a", aliyun=["cmjj000001", "cmjj000001"])]
        )
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("duplicates code", result.output)

    def test_duplicate_id_across_files_is_error(self):
        self.write("01-first.json", [entry("a")])
        self.write("02-second.json", [entry("a")])
        result = self.invoke(self.directory)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("duplicate application id", result.output)

    def test_duplicate_code_across_apps_is_warning(self):
        path = self.write(
            "offer.json",
            [entry("a", aliyun=["cmjj000001"]), entry("b", aliyun=["cmjj000001"])],
        )
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("is assigned to 2 applications: a, b", result.output)

    def test_strict_promotes_warnings_to_failure(self):
        path = self.write(
            "offer.json",
            [entry("a", aliyun=["cmjj000001"]), entry("b", aliyun=["cmjj000001"])],
        )
        result = self.invoke(path, None, "--strict")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("warning", result.output)

    def test_strict_does_not_fail_on_product_en_differences(self):
        path = self.write("offer.json", [entry("a")])
        result = self.invoke(path, None, "--strict", "--check-coverage")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "product_en.json application(s) have no offer entry", result.output
        )

    def test_unknown_platform_is_warning(self):
        path = self.write("offer.json", [entry("a", googlecloud=["abc"])])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("productID.googlecloud", result.output)
        self.assertIn("not part of the standard set", result.output)

    def test_bad_id_pattern_is_warning(self):
        item = entry("a")
        item["id"] = "Bad_Id"
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("should use lowercase", result.output)

    def test_short_aws_code_is_warning(self):
        path = self.write("offer.json", [entry("a", aws=["abc"])])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("does not match the expected aws format", result.output)

    def test_id_missing_from_catalog_is_warning(self):
        path = self.write("offer.json", [entry("a"), entry("z")])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("z: 1 issue(s)", result.output)

    def test_coverage_check_reports_missing_product_apps(self):
        path = self.write("offer.json", [entry("a")])
        result = self.invoke(path, None, "--check-coverage")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "2 product_en.json application(s) have no offer entry", result.output
        )

    def test_coverage_warning_is_on_by_default(self):
        path = self.write("offer.json", [entry("a")])
        result = self.runner.invoke(
            cli,
            [
                "offer-codes", "verify", "--offer", str(path),
                "--product-catalog", str(self.catalog("a", "b", "c")),
            ],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(
            "2 product_en.json application(s) have no offer entry", result.output
        )

    def test_product_catalog_accepts_product_en_key_field(self):
        product = self.write(
            "product_en.json", [{"key": "a"}, {"key": "b"}]
        )
        path = self.write("offer.json", [entry("a", "1"), entry("b", "2")])
        result = self.invoke(path, product)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("0 error(s), 0 warning(s)", result.output)

    def test_missing_catalog_skips_id_checks(self):
        path = self.write("offer.json", [entry("a")])
        missing = self.directory / "absent.json"
        result = self.invoke(path, missing)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("skipped application-id checks", result.output)

    def test_malformed_json_is_reported(self):
        path = self.directory / "offer.json"
        path.write_text("{not json", encoding="utf-8")
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("failed to read offer codes", result.output)

    def test_empty_directory_is_reported(self):
        empty = self.directory / "empty"
        empty.mkdir()
        result = self.invoke(empty)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("has no JSON files", result.output)

    def test_json_output_is_machine_readable(self):
        path = self.write("offer.json", [entry("a")])
        result = self.invoke(path, None, "--json")
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["entries"], 1)
        self.assertEqual(payload["errors"], 0)
        self.assertIn("issues", payload)

    def test_variants_accept_one_label_for_a_multi_code_platform(self):
        code_a, code_b = "a" * 25, "b" * 25
        item = entry("a", aws=[code_a, code_b])
        item["variants"] = {"aws": {code_a: "Ubuntu 26.04"}}
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("0 error(s), 0 warning(s)", result.output)

    def test_variants_are_optional_for_multi_code_platforms(self):
        item = entry("a", aws=["a" * 25, "b" * 25])
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 0, result.output)

    def test_variant_label_may_hold_several_segments(self):
        code_a = "a" * 25
        item = entry("a", aws=[code_a])
        item["variants"] = {"aws": {code_a: "Ubuntu 26.04; Multisite"}}
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 0, result.output)

    def test_variant_unknown_code_is_error(self):
        item = entry("a")
        item["variants"] = {"aws": {"z" * 25: "Ubuntu 26.04"}}
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("is not present in productID.aws", result.output)

    def test_variant_unknown_platform_is_error(self):
        item = entry("a")
        item["variants"] = {"gcp": {"abc": "Ubuntu 26.04"}}
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("platform has no productID entry", result.output)

    def test_variant_label_rejects_empty_segments(self):
        code_a = "a" * 25
        item = entry("a", aws=[code_a])
        item["variants"] = {"aws": {code_a: "Ubuntu 26.04; "}}
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("empty ';'-separated segment", result.output)

    def test_variants_must_label_at_least_one_code(self):
        item = entry("a", aws=["a" * 25, "b" * 25])
        item["variants"] = {"aws": {}}
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("must label at least one code", result.output)

    def test_variant_label_must_be_a_string(self):
        code_a = "a" * 25
        item = entry("a", aws=[code_a])
        item["variants"] = {"aws": {code_a: ["Ubuntu 26.04"]}}
        path = self.write("offer.json", [item])
        result = self.invoke(path)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("must be a string", result.output)


class OfferCodesModuleTests(unittest.TestCase):
    def test_load_source_reads_directory_in_order(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "02-b.json").write_text(json.dumps([entry("b")]), encoding="utf-8")
            (directory / "01-a.json").write_text(json.dumps([entry("a")]), encoding="utf-8")
            documents = offer_codes.load_source(str(directory))
            self.assertEqual([label.rsplit("/", 1)[-1] for label, _ in documents],
                             ["01-a.json", "02-b.json"])

    def test_catalog_ids_ignores_malformed_items(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "catalog.json"
            path.write_text(
                json.dumps({"items": [{"id": "a"}, {"id": " "}, "bad", {"key": "b"}]}),
                encoding="utf-8",
            )
            self.assertEqual(offer_codes.load_catalog_ids(str(path)), {"a", "b"})

class FakeAdapter(offer_listing.Adapter):
    platform = "fake"

    def __init__(self, outcomes=None, available=True, reason="", supported=True):
        self.outcomes = outcomes or {}
        self._available = available
        self.reason = reason
        self.supported = supported

    def availability(self):
        return self._available, self.reason

    def check(self, codes):
        return {
            code: self.outcomes.get(
                code, offer_listing.Outcome(offer_listing.ERROR, "no mock")
            )
            for code in codes
        }


class ReachabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.runner = CliRunner()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        path = self.directory / "offer.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def documents(self, value):
        return [("mock", value)]

    def test_audit_classifies_results(self):
        document = [
            entry("a", "1", aws=["aaaaaaaaaaaaaaaaaaaaaaaaa"]),
            entry("b", "2", aws=["bbbbbbbbbbbbbbbbbbbbbbbbb"]),
        ]
        adapter = FakeAdapter(
            {
                "aaaaaaaaaaaaaaaaaaaaaaaaa": offer_listing.Outcome(
                    offer_listing.EXISTS
                ),
                "bbbbbbbbbbbbbbbbbbbbbbbbb": offer_listing.Outcome(
                    offer_listing.NOT_FOUND, "gone"
                ),
            }
        )
        result = offer_listing.check_listings(
            self.documents(document),
            platforms=["aws"],
            adapters={"aws": adapter},
        )
        self.assertEqual(result["summary"]["exists"], 1)
        self.assertEqual(result["summary"]["not_found"], 1)
        self.assertEqual(result["platforms"]["aws"]["status"], "ok")

    def test_audit_marks_unsupported_and_skipped(self):
        document = [entry("a", "1", aws=["aaaaaaaaaaaaaaaaaaaaaaaaa"])]
        unsupported = FakeAdapter(available=False, reason="no adapter", supported=False)
        skipped = FakeAdapter(available=False, reason="missing credentials")
        result = offer_listing.check_listings(
            self.documents(document),
            platforms=["aws"],
            adapters={"aws": unsupported},
        )
        self.assertEqual(result["summary"]["unsupported"], 1)
        self.assertEqual(result["platforms"]["aws"]["status"], "unsupported")

        result = offer_listing.check_listings(
            self.documents(document),
            platforms=["aws"],
            adapters={"aws": skipped},
        )
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertIn("missing credentials", result["results"][0]["detail"])

    def test_audit_reports_adapter_failure_without_stopping(self):
        class Broken(FakeAdapter):
            def check(self, codes):
                raise RuntimeError("boom")

        document = [entry("a", "1", aws=["aaaaaaaaaaaaaaaaaaaaaaaaa"])]
        result = offer_listing.check_listings(
            self.documents(document),
            platforms=["aws"],
            adapters={"aws": Broken()},
        )
        self.assertEqual(result["summary"]["errors"], 1)
        self.assertIn("RuntimeError", result["results"][0]["detail"])

    def test_audit_counts_non_public_listings(self):
        document = [entry("a", "1", aws=["aaaaaaaaaaaaaaaaaaaaaaaaa"])]
        adapter = FakeAdapter(
            {
                "aaaaaaaaaaaaaaaaaaaaaaaaa": offer_listing.Outcome(
                    offer_listing.EXISTS, visibility="Private", public=False
                )
            }
        )
        result = offer_listing.check_listings(
            self.documents(document),
            platforms=["aws"],
            adapters={"aws": adapter},
        )
        self.assertEqual(result["summary"]["exists"], 1)
        self.assertEqual(result["summary"]["not_public"], 1)
        self.assertEqual(result["results"][0]["visibility"], "Private")

    def test_audit_rejects_unknown_platform(self):
        with self.assertRaisesRegex(ValueError, "unknown platform"):
            offer_listing.check_listings(
                self.documents([entry("a")]),
                platforms=["nope"],
                adapters={"aws": FakeAdapter()},
            )

    def test_command_json_output_and_exit_code(self):
        path = self.write([entry("a", "1", aws=["aaaaaaaaaaaaaaaaaaaaaaaaa"])])
        adapter = FakeAdapter(
            {
                "aaaaaaaaaaaaaaaaaaaaaaaaa": offer_listing.Outcome(
                    offer_listing.NOT_FOUND, "gone"
                )
            }
        )
        with mock.patch.dict(offer_listing.ADAPTERS, {"aws": adapter}):
            result = self.runner.invoke(
                cli,
                ["offer-codes", "listing-check", "--offer", str(path),
                 "--platform", "aws", "--json"],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["summary"]["not_found"], 1)

    def test_command_strict_fails_on_not_found(self):
        path = self.write([entry("a", "1", aws=["aaaaaaaaaaaaaaaaaaaaaaaaa"])])
        adapter = FakeAdapter(
            {
                "aaaaaaaaaaaaaaaaaaaaaaaaa": offer_listing.Outcome(
                    offer_listing.NOT_FOUND, "gone"
                )
            }
        )
        with mock.patch.dict(offer_listing.ADAPTERS, {"aws": adapter}):
            result = self.runner.invoke(
                cli,
                ["offer-codes", "listing-check", "--offer", str(path),
                 "--platform", "aws", "--strict"],
            )
        self.assertEqual(result.exit_code, 1)

    def test_command_prints_unsupported_platform(self):
        path = self.write([entry("a", "1", aws=["aaaaaaaaaaaaaaaaaaaaaaaaa"])])
        adapter = FakeAdapter(available=False, reason="no adapter", supported=False)
        with mock.patch.dict(offer_listing.ADAPTERS, {"aws": adapter}):
            result = self.runner.invoke(
                cli,
                ["offer-codes", "listing-check", "--offer", str(path),
                 "--platform", "aws"],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("unsupported", result.output)


if __name__ == "__main__":
    unittest.main()
