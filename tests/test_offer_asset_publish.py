from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cli import asset_storage, aws_marketplace, catalog_data, offer, offer_assets
from cli.cli import cli
from cli.commands import offer as commands
from tests.click_support import separated_runner


class AssetPublishTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = self.root / "catalog.json"
        self.assets = self.root / "assets.json"
        self.plan = self.root / "plan.json"
        self.item = {"id": "odoo", "entityId": "prod-a", "targets": [
            {"code": "code-a", "entityId": "prod-a", "offer": "Ubuntu"},
        ]}
        self.write_catalog()
        self.document = offer_assets.render_assets(self.item)
        self.assets.write_text(json.dumps(self.document))
        self.live = {"prod-a": self.entity("prod-a")}
        self.aws = mock.Mock()
        self.aws.describe_entity.side_effect = lambda **kw: copy.deepcopy(self.live[kw["EntityId"]])
        self.aws.describe_change_set.return_value = {"Status": "SUCCEEDED"}

        def submit(**kwargs):
            change = kwargs["ChangeSet"][0]
            entity_id = change["Entity"]["Identifier"].split("@")[0]
            for section, fields in aws_marketplace.to_read_document(change["DetailsDocument"]).items():
                self.live[entity_id]["DetailsDocument"].setdefault(section, {}).update(fields)
            self.live[entity_id]["EntityIdentifier"] = entity_id + "@2"
            return {"ChangeSetId": "cs-" + entity_id}

        self.aws.start_change_set.side_effect = submit
        self.runner = separated_runner()

    def write_catalog(self):
        self.catalog.write_text(json.dumps({"items": [self.item]}))

    def entity(self, entity_id, content=None):
        return {"EntityType": "AmiProduct@1.0", "EntityIdentifier": entity_id + "@1", "DetailsDocument": {
            "Description": {"ProductTitle": "Do not modify listing copy"},
            **aws_marketplace.to_read_document(content or {"LogoUrl": "https://old.example/logo.png"}),
        }}

    def invoke(self, command, *args):
        with mock.patch.object(offer, "aws_client", return_value=self.aws), \
                mock.patch.object(asset_storage, "create_s3_client", side_effect=AssertionError("no S3 reads")), \
                mock.patch.object(catalog_data, "load_versions", side_effect=AssertionError("no version fetch")), \
                mock.patch.object(offer, "render", side_effect=AssertionError("no copy rendering")), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("no image downloads")):
            return self.runner.invoke(cli, ["offer", command, "--app", "odoo", "--catalog", str(self.catalog),
                                            "--assets", str(self.assets), *args])

    def make_plan(self, *args):
        result = self.invoke("plan", "--output", str(self.plan), *args)
        self.assertEqual(result.exit_code, 0, result.stderr)
        return json.loads(self.plan.read_text())

    def apply(self, approved=None, *args, wait_seconds=300):
        reviewed = json.loads(self.plan.read_text())
        return self.invoke("apply", "--plan", str(self.plan),
                           "--approve-plan-hash", approved or reviewed["planHash"],
                           "--wait-seconds", str(wait_seconds), *args)

    def test_default_and_explicit_zero_wait_only_submit_without_polling_or_readback(self):
        for flags in ([], ["--wait-seconds", "0"]):
            with self.subTest(flags=flags):
                planned = self.make_plan()
                self.aws.reset_mock()
                result = self.invoke("apply", "--plan", str(self.plan),
                                     "--approve-plan-hash", planned["planHash"], *flags)
                self.assertEqual(result.exit_code, 0, result.stderr)
                document = json.loads(result.stdout)
                self.assertEqual(document["status"], "SUBMITTED")
                self.assertEqual(document["changeSetId"], "cs-prod-a")
                self.assertFalse(document["readBackVerified"])
                self.aws.describe_change_set.assert_not_called()
                self.aws.describe_entity.assert_called_once_with(Catalog="AWSMarketplace", EntityId="prod-a")

    def test_multi_target_zero_wait_returns_submitted_not_succeeded(self):
        self.item["targets"].append({"code": "code-b", "entityId": "prod-b", "offer": "Debian"})
        self.write_catalog()
        self.live["prod-b"] = self.entity("prod-b")
        self.make_plan("--all-codes")
        self.aws.reset_mock()
        result = self.apply(wait_seconds=0)
        self.assertEqual(result.exit_code, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["status"], "SUBMITTED")
        self.assertFalse(document["readBackVerified"])
        self.assertEqual([t["status"] for t in document["targets"]], ["SUBMITTED", "SUBMITTED"])
        self.assertEqual(self.aws.describe_entity.call_count, 2)
        self.aws.describe_change_set.assert_not_called()

    def test_multi_target_partial_submission_is_reported_without_waiting(self):
        self.item["targets"].append({"code": "code-b", "entityId": "prod-b", "offer": "Debian"})
        self.write_catalog()
        self.live["prod-b"] = self.entity("prod-b")
        self.make_plan("--all-codes")
        self.aws.start_change_set.side_effect = [{"ChangeSetId": "cs-first"}, ValueError("submission failed")]
        result = self.apply(wait_seconds=0)
        self.assertNotEqual(result.exit_code, 0)
        document = json.loads(result.stdout)
        self.assertEqual(document["status"], "PARTIAL")
        self.assertEqual(document["targets"][0]["changeSetId"], "cs-first")
        self.assertFalse(document["readBackVerified"])
        self.assertNotIn("NO_CHANGES", document["resumeHint"])
        self.aws.describe_change_set.assert_not_called()

    def test_build_plan_apply_without_staging_reuses_revision_hash_and_readback(self):
        result = self.runner.invoke(cli, ["offer", "assets", "build", "--app", "odoo",
                                          "--catalog", str(self.catalog), "--output", str(self.assets)])
        self.assertEqual(result.exit_code, 0, result.stderr)
        planned = self.make_plan()
        self.assertTrue(planned["refreshAssets"])
        self.assertEqual(planned["fieldScope"], "assets")
        self.assertEqual(planned["baseRevision"], "prod-a@1")
        self.assertEqual(planned["planHash"], offer.digest({k: v for k, v in planned.items() if k != "planHash"}))
        result = self.apply()
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "SUCCEEDED")
        self.assertTrue(json.loads(result.stdout)["readBackVerified"])
        request = self.aws.start_change_set.call_args.kwargs
        self.assertEqual(request["ClientRequestToken"], planned["planHash"])
        self.assertEqual(request["ChangeSet"][0]["Entity"]["Identifier"], "prod-a@1")
        self.assertEqual(set(request["ChangeSet"][0]["DetailsDocument"]), {"LogoUrl", "PromotionalMedia"})
        self.assertEqual(self.live["prod-a"]["DetailsDocument"]["Description"]["ProductTitle"], "Do not modify listing copy")

    def test_equal_urls_are_still_submitted_and_readback_does_not_force_another_update(self):
        desired = offer_assets.as_offer_bundle(self.item, self.document)["content"]
        self.live["prod-a"] = self.entity("prod-a", desired)
        planned = self.make_plan()
        self.assertIsNotNone(planned["request"])
        for field in desired:
            self.assertEqual(planned["diff"][field]["before"], planned["diff"][field]["after"])
        result = self.apply()
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.aws.start_change_set.assert_called_once()
        self.assertTrue(json.loads(result.stdout)["readBackVerified"])

    def test_new_plans_get_new_operations_while_a_reviewed_operation_is_deterministic(self):
        first = self.make_plan()
        second = self.make_plan()
        self.assertNotEqual(first["operationId"], second["operationId"])
        self.assertNotEqual(first["planHash"], second["planHash"])
        self.assertEqual(first["sourceHash"], second["sourceHash"])
        value = offer_assets.as_offer_bundle(self.item, self.document)
        value["operationId"] = second["operationId"]
        self.assertEqual(offer.plan(value, self.live["prod-a"]), second)

    def test_new_wave_uses_a_new_token_even_if_aws_keeps_revision_and_urls(self):
        desired = offer_assets.as_offer_bundle(self.item, self.document)["content"]
        self.live["prod-a"] = self.entity("prod-a", desired)
        self.aws.start_change_set.side_effect = lambda **kw: {"ChangeSetId": "cs-refresh"}
        first = self.make_plan()
        self.assertEqual(self.apply().exit_code, 0)
        self.assertEqual(self.apply().exit_code, 0)
        self.assertEqual(self.aws.start_change_set.call_args.kwargs["ClientRequestToken"], first["planHash"])
        second = self.make_plan()
        self.assertEqual(self.apply().exit_code, 0)
        self.assertEqual(self.aws.start_change_set.call_args.kwargs["ClientRequestToken"], second["planHash"])
        self.assertNotEqual(first["planHash"], second["planHash"])

    def test_selected_fields_are_refreshed_and_omitted_fields_are_preserved(self):
        self.document["content"].pop("PromotionalMedia")
        self.assets.write_text(json.dumps(self.document))
        videos = [{"Type": "Video", "Title": "Keep", "Url": "https://existing.example/video.mp4"}]
        self.live["prod-a"] = self.entity("prod-a", {"PromotionalMedia": videos})
        planned = self.make_plan()
        self.assertEqual(set(planned["diff"]), {"LogoUrl"})
        result = self.apply()
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(self.live["prod-a"]["DetailsDocument"]["PromotionalResources"]["PromotionalMedia"], videos)

    def test_media_refresh_submits_the_entire_authored_list_not_only_different_urls(self):
        desired = offer_assets.as_offer_bundle(self.item, self.document)["content"]
        remote = copy.deepcopy(desired)
        remote["PromotionalMedia"][0]["Url"] = "https://old.example/image.png"
        self.live["prod-a"] = self.entity("prod-a", remote)
        planned = self.make_plan()
        sent = planned["request"]["ChangeSet"][0]["DetailsDocument"]
        self.assertEqual(sent["PromotionalMedia"], desired["PromotionalMedia"])
        self.assertEqual(len(sent["PromotionalMedia"]), 5)
        self.assertIn("LogoUrl", sent)  # Explicit selected-field refresh, not URL diff.

    def test_approval_is_required_even_without_waiting(self):
        self.make_plan()
        self.aws.reset_mock()
        for flags in ([], ["--approve-plan-hash", "0" * 64],
                      ["--wait-seconds", "0", "--approve-plan-hash", "0" * 64]):
            result = self.invoke("apply", "--plan", str(self.plan), *flags)
            self.assertNotEqual(result.exit_code, 0)
            self.aws.describe_entity.assert_not_called()
            self.aws.start_change_set.assert_not_called()

    def test_revision_and_config_changes_make_plan_stale(self):
        for kind in ("revision", "catalog", "assets"):
            with self.subTest(kind=kind):
                self.item.pop("trademark", None)
                self.write_catalog()
                self.assets.write_text(json.dumps(self.document))
                self.live["prod-a"] = self.entity("prod-a")
                self.aws.start_change_set.reset_mock()
                self.make_plan()
                if kind == "revision":
                    self.live["prod-a"]["EntityIdentifier"] = "prod-a@2"
                elif kind == "catalog":
                    self.item["trademark"] = "Changed source"
                    self.write_catalog()
                else:
                    changed = copy.deepcopy(self.document)
                    changed["content"]["PromotionalMedia"][0]["Title"] = "New title"
                    self.assets.write_text(json.dumps(changed))
                result = self.apply()
                self.assertNotEqual(result.exit_code, 0)
                self.assertIn("stale", result.stderr)
                self.aws.start_change_set.assert_not_called()

    def test_modified_reviewed_plan_cannot_inject_copy_fields_even_with_recomputed_hash(self):
        planned = self.make_plan()
        planned["request"]["ChangeSet"][0]["DetailsDocument"]["ProductTitle"] = "Injected"
        planned["planHash"] = offer.digest({k: v for k, v in planned.items() if k != "planHash"})
        self.plan.write_text(json.dumps(planned))
        result = self.apply()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("stale or modified", result.stderr)
        self.aws.start_change_set.assert_not_called()

    def test_edited_plan_cannot_target_another_catalog_entity_or_change_operation(self):
        for field, value in (("baseRevision", "prod-foreign@1"), ("operationId", "0" * 32)):
            with self.subTest(field=field):
                planned = self.make_plan()
                planned[field] = value
                self.plan.write_text(json.dumps(planned))
                result = self.apply()
                self.assertNotEqual(result.exit_code, 0)
                self.aws.start_change_set.assert_not_called()

    def test_malformed_copy_signed_or_legacy_manifest_inputs_cannot_publish(self):
        for mutate in (
            lambda d: d.update(kind="staged-offer-assets", manifestHash="old"),
            lambda d: d.update(key="another-app"),
            lambda d: d["content"].update(ProductTitle="Injected copy"),
            lambda d: d["content"].update(LogoUrl="https://example.com/image?token=SECRET"),
            lambda d: d["content"].update(PromotionalMedia=[]),
            lambda d: d.update(schemaVersion=True),
        ):
            changed = copy.deepcopy(self.document)
            mutate(changed)
            self.assets.write_text(json.dumps(changed))
            result = self.invoke("plan", "--output", str(self.plan))
            self.assertNotEqual(result.exit_code, 0)
            self.assertNotIn("SECRET", result.stderr)
            self.aws.describe_entity.assert_not_called()

    def test_stage_command_is_removed(self):
        result = self.runner.invoke(cli, ["offer", "assets", "stage"])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("No such command", result.stderr)

    def test_existing_videos_are_not_implicitly_removed(self):
        self.live["prod-a"] = self.entity("prod-a", {"PromotionalMedia": [
            {"Type": "Video", "Title": "Keep", "Url": "https://existing.example/video.mp4"},
        ]})
        result = self.invoke("plan", "--output", str(self.plan))
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("non-image PromotionalMedia", result.stderr)

    def test_successful_changeset_with_wrong_readback_is_not_success(self):
        self.make_plan()
        self.aws.start_change_set.side_effect = None
        self.aws.start_change_set.return_value = {"ChangeSetId": "cs-wrong-readback"}
        result = self.apply()
        self.assertNotEqual(result.exit_code, 0)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["status"], "UNVERIFIED")
        self.assertFalse(doc["readBackVerified"])
        self.assertEqual(doc["changeSetId"], "cs-wrong-readback")

    def test_timeout_remains_unverified_without_cancel_or_resubmit(self):
        self.make_plan()
        self.aws.describe_change_set.return_value = {"Status": "PREPARING"}
        with mock.patch.object(commands.time, "monotonic", side_effect=[0, 302]):
            result = self.apply()
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "UNVERIFIED")
        self.assertIn("not cancelled", result.stderr)
        self.aws.cancel_change_set.assert_not_called()
        self.aws.start_change_set.assert_called_once()

    def test_multiple_os_offers_and_single_offer_selection_do_not_generate_titles(self):
        self.item["targets"].append({"code": "code-b", "entityId": "prod-b", "offer": "Debian"})
        self.write_catalog()
        self.live["prod-b"] = self.entity("prod-b")
        planned = self.make_plan("--all-codes")
        self.assertEqual(len(planned["targets"]), 2)
        self.assertTrue(planned["refreshAssets"])
        for target in planned["targets"]:
            self.assertNotIn("ProductTitle", target["plan"]["diff"])
        result = self.apply()
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["readBackVerified"])
        self.aws.start_change_set.reset_mock()
        planned = self.make_plan("--offer-code", "code-b")
        self.assertEqual(planned["baseRevision"], "prod-b@2")
        self.assertEqual(self.apply().exit_code, 0)
        self.assertEqual(self.aws.start_change_set.call_args.kwargs["ChangeSet"][0]["Entity"]["Identifier"], "prod-b@2")

    def test_signed_old_urls_are_redacted_but_still_bound_by_current_hash(self):
        self.live["prod-a"] = self.entity("prod-a", {"LogoUrl": "https://old.example/logo?signature=SECRET"})
        planned = self.make_plan()
        self.assertNotIn("SECRET", self.plan.read_text())
        self.assertIn("parameters redacted", planned["diff"]["LogoUrl"]["before"])
        self.live["prod-a"]["DetailsDocument"]["PromotionalResources"]["LogoUrl"] = "https://old.example/logo?signature=OTHER"
        result = self.apply()
        self.assertNotEqual(result.exit_code, 0)
        self.aws.start_change_set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
