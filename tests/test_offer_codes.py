from __future__ import annotations

import json
import os
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


class OfferCodesReplaceTests(unittest.TestCase):
    CODE = "a" * 25
    OTHER = "b" * 25

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.runner = CliRunner()

    def tearDown(self):
        self.temp.cleanup()

    def empty(self, app: str) -> dict:
        return {
            "id": app,
            "productID": {platform: [] for platform in PLATFORMS},
            "type": "app",
            "applications": [app],
        }

    def write(self, entries: list[dict], catalog=("old", "new")) -> tuple[Path, Path]:
        offer = self.directory / "offer.json"
        offer.write_text(json.dumps(entries), encoding="utf-8")
        product = self.directory / "catalog.json"
        product.write_text(
            json.dumps({"items": [{"id": app} for app in catalog]}),
            encoding="utf-8",
        )
        return offer, product

    def invoke(self, offer: Path, product: Path, *args: str):
        return self.runner.invoke(
            cli,
            [
                "offer-codes", "replace",
                "--offer", str(offer),
                "--product-catalog", str(product),
                *args,
            ],
        )

    def load(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_moves_code_and_label_to_an_existing_entry(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        old["variants"] = {"aws": {self.CODE: "Ubuntu"}}
        offer, product = self.write([old, self.empty("new")])

        result = self.invoke(
            offer, product, "--from", "old", "--to", "new", "--platform", "aws"
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Moved aws", result.output)
        data = {item["id"]: item for item in self.load(offer)}
        self.assertEqual(data["old"]["productID"]["aws"], [])
        self.assertEqual(data["new"]["productID"]["aws"], [self.CODE])
        self.assertNotIn("variants", data["old"])
        self.assertEqual(data["new"]["variants"]["aws"][self.CODE], "Ubuntu")

    def test_creates_the_target_entry_when_missing(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        offer, product = self.write([old])

        result = self.invoke(
            offer, product, "--from", "old", "--to", "new", "--platform", "aws"
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Created offer entry", result.output)
        data = {item["id"]: item for item in self.load(offer)}
        self.assertEqual(set(data["new"]["productID"]), set(PLATFORMS))
        self.assertEqual(data["new"]["applications"], ["new"])
        self.assertEqual(data["new"]["productID"]["aws"], [self.CODE])

    def test_defaults_to_every_platform_that_has_codes(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        old["productID"]["azure"] = ["publisher.offer"]
        offer, product = self.write([old, self.empty("new")])

        result = self.invoke(
            offer, product, "--from", "old", "--to", "new"
        )

        self.assertEqual(result.exit_code, 0, result.output)
        data = {item["id"]: item for item in self.load(offer)}
        self.assertEqual(data["new"]["productID"]["aws"], [self.CODE])
        self.assertEqual(
            data["new"]["productID"]["azure"], ["publisher.offer"]
        )
        self.assertEqual(data["old"]["productID"]["aws"], [])
        self.assertEqual(data["old"]["productID"]["azure"], [])

    def test_target_must_exist_in_the_catalog(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        offer, product = self.write([old], catalog=("old",))
        before = offer.read_text(encoding="utf-8")

        result = self.invoke(
            offer, product, "--from", "old", "--to", "new", "--platform", "aws"
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("is not present in", result.output)
        self.assertEqual(offer.read_text(encoding="utf-8"), before)

    def test_refuses_a_non_app_target_entry(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        target = self.empty("websoft9")
        target["type"] = "websoft9"
        offer, product = self.write([old, target], catalog=("old", "websoft9"))

        result = self.invoke(
            offer, product, "--from", "old", "--to", "websoft9", "--platform", "aws"
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("only app entries", result.output)

    def test_resolves_application_aliases(self):
        old = self.empty("old")
        old["applications"] = ["old", "old-alias"]
        old["productID"]["aws"] = [self.CODE]
        target = self.empty("new")
        target["applications"] = ["new", "new-alias"]
        offer, product = self.write([old, target], catalog=("old", "new"))

        result = self.invoke(
            offer, product,
            "--from", "old-alias", "--to", "new-alias", "--platform", "aws",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("is an alias of entry", result.output)
        data = {item["id"]: item for item in self.load(offer)}
        self.assertEqual(data["new"]["productID"]["aws"], [self.CODE])
        self.assertEqual(data["old"]["productID"]["aws"], [])

    def test_rejects_two_aliases_of_the_same_entry(self):
        item = self.empty("app")
        item["applications"] = ["app", "alias"]
        item["productID"]["aws"] = [self.CODE]
        offer, product = self.write([item], catalog=("app", "alias"))

        result = self.invoke(
            offer, product, "--from", "app", "--to", "alias", "--platform", "aws"
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("same offer entry", result.output)

    def test_refuses_a_code_already_owned_by_the_target(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        new = self.empty("new")
        new["productID"]["aws"] = [self.CODE]
        offer, product = self.write([old, new])

        result = self.invoke(
            offer, product, "--from", "old", "--to", "new", "--platform", "aws"
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("already assigned", result.output)

    def test_dry_run_shows_the_diff_without_writing(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        offer, product = self.write([old, self.empty("new")])
        before = offer.read_text(encoding="utf-8")

        result = self.invoke(
            offer, product,
            "--from", "old", "--to", "new", "--platform", "aws", "--dry-run",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("---", result.output)
        self.assertIn("Dry run", result.output)
        self.assertEqual(offer.read_text(encoding="utf-8"), before)

    def test_moves_codes_across_files_in_a_directory(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        new = self.empty("new")
        source = self.directory / "offers"
        source.mkdir()
        (source / "01-old.json").write_text(json.dumps([old]), encoding="utf-8")
        (source / "02-new.json").write_text(json.dumps([new]), encoding="utf-8")
        product = self.directory / "catalog.json"
        product.write_text(
            json.dumps({"items": [{"id": "old"}, {"id": "new"}]}),
            encoding="utf-8",
        )

        result = self.invoke(
            source, product, "--from", "old", "--to", "new", "--platform", "aws"
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Wrote", result.output)
        old_data = json.loads((source / "01-old.json").read_text(encoding="utf-8"))
        new_data = json.loads((source / "02-new.json").read_text(encoding="utf-8"))
        self.assertEqual(old_data[0]["productID"]["aws"], [])
        self.assertEqual(new_data[0]["productID"]["aws"], [self.CODE])

    def test_delete_empty_removes_the_old_entry(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        offer, product = self.write([old, self.empty("new")])

        result = self.invoke(
            offer, product,
            "--from", "old", "--to", "new", "--platform", "aws", "--delete-empty",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual([item["id"] for item in self.load(offer)], ["new"])

    def test_code_filter_rejects_a_code_the_old_app_does_not_own(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        offer, product = self.write([old, self.empty("new")])

        result = self.invoke(
            offer, product,
            "--from", "old", "--to", "new",
            "--platform", "aws", "--code", self.OTHER,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("does not belong", result.output)

    def test_platform_filter_must_cover_the_requested_code(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        offer, product = self.write([old, self.empty("new")])

        result = self.invoke(
            offer, product,
            "--from", "old", "--to", "new",
            "--platform", "azure", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("outside the selected platform", result.output)

    def test_old_app_may_be_absent_from_the_catalog(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        offer, product = self.write([old, self.empty("new")], catalog=("new",))

        result = self.invoke(
            offer, product, "--from", "old", "--to", "new", "--platform", "aws"
        )

        self.assertEqual(result.exit_code, 0, result.output)
        data = {item["id"]: item for item in self.load(offer)}
        self.assertEqual(data["new"]["productID"]["aws"], [self.CODE])

    def test_json_output_is_machine_readable(self):
        old = self.empty("old")
        old["productID"]["aws"] = [self.CODE]
        offer, product = self.write([old, self.empty("new")])

        result = self.invoke(
            offer, product,
            "--from", "old", "--to", "new",
            "--platform", "aws", "--dry-run", "--json",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertFalse(payload["written"])
        self.assertEqual(payload["result"]["moves"][0]["code"], self.CODE)
        self.assertIn("diff", payload)


class OfferCodesMaintenanceBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.runner = CliRunner()

    def tearDown(self):
        self.temp.cleanup()

    def empty(self, app: str) -> dict:
        return {
            "id": app,
            "productID": {platform: [] for platform in PLATFORMS},
            "type": "app",
            "applications": [app],
        }

    def write(self, entries: list[dict], catalog=("app",)) -> tuple[Path, Path]:
        offer = self.directory / "offer.json"
        offer.write_text(json.dumps(entries), encoding="utf-8")
        product = self.directory / "catalog.json"
        product.write_text(
            json.dumps({"items": [{"id": app} for app in catalog]}),
            encoding="utf-8",
        )
        return offer, product

    def load(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def invoke(self, command: str, offer: Path, product: Path, *args: str):
        argv = ["offer-codes", command, "--offer", str(offer)]
        if command in {"verify", "replace", "add"}:
            argv += ["--product-catalog", str(product)]
        argv += list(args)
        return self.runner.invoke(cli, argv)


class OfferCodesFindTests(OfferCodesMaintenanceBase):
    def test_find_by_app_reports_codes_and_labels(self):
        item = self.empty("app")
        item["productID"]["aws"] = ["a" * 25]
        item["variants"] = {"aws": {"a" * 25: "Ubuntu"}}
        offer, product = self.write([item])

        result = self.invoke("find", offer, product, "--app", "app")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(f"app aws {'a' * 25} (Ubuntu)", result.output)
        self.assertIn("1 code(s)", result.output)

    def test_find_by_code_reports_every_owner(self):
        first = self.empty("first")
        first["productID"]["aws"] = ["a" * 25]
        second = self.empty("second")
        second["productID"]["aws"] = ["a" * 25]
        offer, product = self.write([first, second])

        result = self.invoke(
            "find", offer, product, "--code", "a" * 25, "--json"
        )

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(
            [row["app"] for row in payload["codes"]], ["first", "second"]
        )

    def test_find_filters_by_platform(self):
        item = self.empty("app")
        item["productID"]["aws"] = ["a" * 25]
        item["productID"]["azure"] = ["publisher.offer"]
        offer, product = self.write([item])

        result = self.invoke(
            "find", offer, product, "--platform", "azure", "--json"
        )

        payload = json.loads(result.output)
        self.assertEqual([row["platform"] for row in payload["codes"]], ["azure"])

    def test_platform_all_disables_the_platform_filter(self):
        item = self.empty("app")
        item["productID"]["aws"] = ["a" * 25]
        item["productID"]["azure"] = ["publisher.offer"]
        offer, product = self.write([item])

        result = self.invoke("find", offer, product, "--platform", "all", "--json")

        payload = json.loads(result.output)
        self.assertEqual(
            sorted(row["platform"] for row in payload["codes"]), ["aws", "azure"]
        )

    def test_find_reports_no_match(self):
        offer, product = self.write([self.empty("app")])

        result = self.invoke("find", offer, product, "--app", "absent")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("0 code(s)", result.output)

    def test_find_json_includes_the_entry_type(self):
        item = self.empty("app")
        item["productID"]["aws"] = ["a" * 25]
        offer, product = self.write([item])

        result = self.invoke("find", offer, product, "--app", "app", "--json")

        payload = json.loads(result.output)
        self.assertEqual(payload["codes"][0]["type"], "app")

    def test_find_marks_non_app_entries_in_text_output(self):
        item = self.empty("websoft9")
        item["type"] = "websoft9"
        item["productID"]["aws"] = ["a" * 25]
        offer, product = self.write([item])

        result = self.invoke("find", offer, product, "--app", "websoft9")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("[websoft9]", result.output)


class OfferCodesAddTests(OfferCodesMaintenanceBase):
    CODE = "a" * 25
    OTHER = "b" * 25

    def test_adds_code_and_label_to_an_existing_entry(self):
        offer, product = self.write([self.empty("app")])

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws",
            "--code", self.CODE, "--label", "Ubuntu",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        item = self.load(offer)[0]
        self.assertEqual(item["productID"]["aws"], [self.CODE])
        self.assertEqual(item["variants"]["aws"][self.CODE], "Ubuntu")

    def test_creates_an_entry_for_a_new_application(self):
        offer, product = self.write([], catalog=("app",))

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Created offer entry", result.output)
        item = self.load(offer)[0]
        self.assertEqual(set(item["productID"]), set(PLATFORMS))
        self.assertEqual(item["productID"]["aws"], [self.CODE])
        self.assertEqual(item["applications"], ["app"])

    def test_creates_an_entry_in_a_single_file_directory(self):
        source = self.directory / "offers"
        source.mkdir()
        (source / "matrix.json").write_text(
            json.dumps([self.empty("first")]), encoding="utf-8"
        )
        product = self.directory / "catalog.json"
        product.write_text(json.dumps({"items": [{"id": "app"}]}), encoding="utf-8")

        result = self.invoke(
            "add", source, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 0, result.output)
        data = self.load(source / "matrix.json")
        self.assertEqual([item["id"] for item in data], ["first", "app"])
        self.assertEqual(data[1]["productID"]["aws"], [self.CODE])

    def test_new_application_needs_a_single_source_file(self):
        source = self.directory / "offers"
        source.mkdir()
        (source / "01-a.json").write_text(
            json.dumps([self.empty("first")]), encoding="utf-8"
        )
        (source / "02-b.json").write_text(
            json.dumps([self.empty("second")]), encoding="utf-8"
        )
        product = self.directory / "catalog.json"
        product.write_text(json.dumps({"items": [{"id": "app"}]}), encoding="utf-8")

        result = self.invoke(
            "add", source, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("single JSON file", result.output)

    def test_rejects_a_code_owned_by_another_application(self):
        other = self.empty("other")
        other["productID"]["aws"] = [self.CODE]
        offer, product = self.write([self.empty("app"), other])

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("already assigned to 'other'", result.output)

    def test_rejects_a_code_already_on_the_same_application(self):
        item = self.empty("app")
        item["productID"]["aws"] = [self.CODE]
        offer, product = self.write([item])

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("already recorded", result.output)

    def test_multiple_codes_require_code_equals_label(self):
        offer, product = self.write([self.empty("app")])

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws",
            "--code", f"{self.CODE},{self.OTHER}", "--label", "Ubuntu",
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("CODE=LABEL", result.output)

    def test_dry_run_writes_nothing(self):
        offer, product = self.write([self.empty("app")])
        before = offer.read_text(encoding="utf-8")

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws",
            "--code", self.CODE, "--dry-run",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Dry run", result.output)
        self.assertEqual(offer.read_text(encoding="utf-8"), before)

    def test_target_application_must_be_in_the_catalog(self):
        offer, product = self.write([], catalog=("other",))

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("is not present in", result.output)

    def test_rejects_unknown_platforms(self):
        offer, product = self.write([self.empty("app")])

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "awss", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("unknown platform", result.output)

    def test_rejects_non_app_entries(self):
        item = self.empty("websoft9")
        item["type"] = "websoft9"
        offer, product = self.write([item], catalog=("websoft9",))

        result = self.invoke(
            "add", offer, product,
            "--app", "websoft9", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("only app entries", result.output)

    def test_alias_adds_to_the_owning_entry(self):
        item = self.empty("app")
        item["applications"] = ["app", "alias"]
        offer, product = self.write([item], catalog=("app",))

        result = self.invoke(
            "add", offer, product,
            "--app", "alias", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("alias (entry app)", result.output)
        self.assertEqual(self.load(offer)[0]["id"], "app")
        self.assertEqual(self.load(offer)[0]["productID"]["aws"], [self.CODE])

    def test_alias_may_carry_the_current_catalog_id(self):
        item = self.empty("old-entry")
        item["applications"] = ["old-entry", "current-app"]
        offer, product = self.write([item], catalog=("current-app",))

        result = self.invoke(
            "add", offer, product,
            "--app", "current-app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.load(offer)[0]["productID"]["aws"], [self.CODE])

    def test_allow_shared_permits_bundle_ownership(self):
        other = self.empty("other")
        other["productID"]["aws"] = [self.CODE]
        offer, product = self.write([self.empty("app"), other])

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
            "--allow-shared",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("new warning", result.output)
        self.assertIn("assigned to 2 applications", result.output)
        data = {item["id"]: item for item in self.load(offer)}
        self.assertEqual(data["app"]["productID"]["aws"], [self.CODE])
        self.assertEqual(data["other"]["productID"]["aws"], [self.CODE])

    def test_adds_several_codes_with_code_equals_label(self):
        offer, product = self.write([self.empty("app")])

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws",
            "--code", f"{self.CODE},{self.OTHER}",
            "--label", f"{self.CODE}=Ubuntu",
            "--label", f"{self.OTHER}=Debian",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        item = self.load(offer)[0]
        self.assertEqual(item["variants"]["aws"][self.CODE], "Ubuntu")
        self.assertEqual(item["variants"]["aws"][self.OTHER], "Debian")

    def test_json_output_reports_written_and_warnings(self):
        offer, product = self.write([self.empty("app")])

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws", "--code", "abc", "--json",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertTrue(payload["written"])
        self.assertEqual(payload["result"]["added"][0]["code"], "abc")
        self.assertTrue(payload["verification"]["newWarnings"])
        self.assertIn("diff", payload)

    def test_reports_new_warnings(self):
        offer, product = self.write([self.empty("app")])

        result = self.invoke(
            "add", offer, product,
            "--app", "app", "--platform", "aws", "--code", "abc",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("new warning", result.output)
        self.assertIn("does not match the expected aws format", result.output)


class OfferCodesRemoveTests(OfferCodesMaintenanceBase):
    CODE = "a" * 25
    OTHER = "b" * 25

    def test_removes_code_and_label(self):
        item = self.empty("app")
        item["productID"]["aws"] = [self.CODE, self.OTHER]
        item["variants"] = {"aws": {self.CODE: "Ubuntu", self.OTHER: "Debian"}}
        offer, product = self.write([item])

        result = self.invoke(
            "remove", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 0, result.output)
        item = self.load(offer)[0]
        self.assertEqual(item["productID"]["aws"], [self.OTHER])
        self.assertNotIn(self.CODE, item["variants"]["aws"])

    def test_rejects_a_code_the_application_does_not_own(self):
        offer, product = self.write([self.empty("app")])

        result = self.invoke(
            "remove", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("do not belong", result.output)

    def test_delete_empty_removes_the_entry(self):
        item = self.empty("app")
        item["productID"]["aws"] = [self.CODE]
        offer, product = self.write([item])

        result = self.invoke(
            "remove", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
            "--delete-empty",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.load(offer), [])

    def test_keeps_other_platform_codes(self):
        item = self.empty("app")
        item["productID"]["aws"] = [self.CODE]
        item["productID"]["azure"] = ["publisher.offer"]
        offer, product = self.write([item])

        result = self.invoke(
            "remove", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 0, result.output)
        item = self.load(offer)[0]
        self.assertEqual(item["productID"]["aws"], [])
        self.assertEqual(item["productID"]["azure"], ["publisher.offer"])

    def test_dry_run_writes_nothing(self):
        item = self.empty("app")
        item["productID"]["aws"] = [self.CODE]
        offer, product = self.write([item])
        before = offer.read_text(encoding="utf-8")

        result = self.invoke(
            "remove", offer, product,
            "--app", "app", "--platform", "aws", "--code", self.CODE,
            "--dry-run",
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Dry run", result.output)
        self.assertEqual(offer.read_text(encoding="utf-8"), before)

    def test_alias_removes_from_the_owning_entry(self):
        item = self.empty("app")
        item["applications"] = ["app", "alias"]
        item["productID"]["aws"] = [self.CODE]
        offer, product = self.write([item], catalog=("app", "alias"))

        result = self.invoke(
            "remove", offer, product,
            "--app", "alias", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("alias (entry app)", result.output)
        self.assertEqual(self.load(offer)[0]["productID"]["aws"], [])

    def test_rejects_non_app_entries(self):
        item = self.empty("websoft9")
        item["type"] = "websoft9"
        item["productID"]["aws"] = [self.CODE]
        offer, product = self.write([item], catalog=("websoft9",))

        result = self.invoke(
            "remove", offer, product,
            "--app", "websoft9", "--platform", "aws", "--code", self.CODE,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("only app entries", result.output)


class OfferCodesModuleTests(unittest.TestCase):
    def test_normalize_ignores_non_strings(self):
        self.assertEqual(
            offer_codes._normalize([None, 1, " a ", "a", " "]), ["a"]
        )

    def test_add_codes_leaves_documents_unchanged_on_error(self):
        app = entry("app", "1")
        other = entry("other", "2")
        documents = [("mock", [app, other])]
        before = json.loads(json.dumps(documents))
        shared = other["productID"]["aws"][0]

        with self.assertRaisesRegex(ValueError, "already assigned"):
            offer_codes.add_codes(
                documents, app="app", platform="aws", codes=[shared]
            )

        self.assertEqual(json.loads(json.dumps(documents)), before)
        self.assertNotIn("variants", app)

    def test_replace_code_leaves_documents_unchanged_on_error(self):
        old = entry("old", "1")
        new = entry("new", "2")
        code = old["productID"]["aws"][0]
        old["variants"] = {"aws": {code: 123}}
        documents = [("mock", [old, new])]
        before = json.loads(json.dumps(documents))

        with self.assertRaisesRegex(ValueError, "not a string"):
            offer_codes.replace_code(
                documents, old_app="old", new_app="new", platforms=["aws"]
            )

        self.assertEqual(json.loads(json.dumps(documents)), before)

    def test_write_documents_preserves_mode(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "offer.json"
            path.write_text("[]", encoding="utf-8")
            os.chmod(path, 0o640)
            documents = [(str(path), [entry("a", "1")])]

            offer_codes.write_documents(documents, [str(path)])

            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), [entry("a", "1")])

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
        self.assertIsInstance(result.exception, SystemExit)
        self.assertIn("not-found code(s)", result.output)

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
