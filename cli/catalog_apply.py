"""Validate and commit agent-generated candidates without trusting agent edits."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from . import catalog_file, catalog_verify, contentful


def apply(
    candidate: Path, baseline: Path, output: Path, *,
    app_ids: tuple[str, ...] | None, fields: tuple[str, ...], skip_invalid: bool = False,
) -> dict[str, Any]:
    if not fields or set(fields) - set(catalog_verify.GENERATED_FIELDS):
        raise ValueError("apply requires a non-empty subset of generated fields")
    if candidate.resolve() == output.resolve() or baseline.resolve() == output.resolve():
        raise ValueError("candidate and baseline must be separate from the output catalog")
    expected = catalog_file.read_bytes(output)
    current = catalog_verify.load_dataset(str(output))
    before = catalog_verify.load_dataset(str(baseline))
    if current != before:
        raise ValueError("catalog differs from baseline; regenerate from a fresh snapshot")
    data = catalog_verify.load_dataset(str(candidate))
    # Isolation/shape violations are never recoverable by dropping an app.
    isolation = catalog_verify.verify(data, app_ids=app_ids, fields=fields,
                                      baseline=before, check_content=False)
    if isolation["errors"]:
        raise ValueError("candidate isolation/structure failed: " + "; ".join(
            i["message"] for i in isolation["issues"] if i["severity"] == "error"))
    validation = catalog_verify.verify(data, app_ids=app_ids, fields=fields, profile="generated")
    failed = {i["app"] for i in validation["issues"] if i["severity"] == "error"}
    if failed and not skip_invalid:
        raise ValueError("candidate validation failed: " + "; ".join(
            f"{i['app']} {i['field']}: {i['message']}"
            for i in validation["issues"] if i["severity"] == "error"))
    selected = validation["selected"]
    succeeded = [app for app in selected if app not in failed]
    originals = {item["id"]: item for item in before["items"]}
    data["items"] = [copy.deepcopy(originals[item["id"]]) if item["id"] in failed else item
                     for item in data["items"]]
    final = catalog_verify.verify(data, app_ids=succeeded, fields=fields,
                                  baseline=before, profile="generated")
    if final["errors"]:
        raise ValueError("restored candidate failed final validation")
    changed = {}
    for item in data["items"]:
        app_id = item["id"]
        if app_id in succeeded:
            changed[app_id] = [f for f in fields if (f in item) != (f in originals[app_id])
                               or item.get(f) != originals[app_id].get(f)]
    backup = None
    if data != current:
        backup = catalog_file.replace(output, contentful.dumps_dataset(data) + "\n", expected=expected)
    if catalog_verify.load_dataset(str(output)) != data:
        raise ValueError("persisted catalog does not match validated candidate")
    return {
        "succeeded": succeeded, "skipped": [app for app in selected if app in failed],
        "changed": changed, "issues": validation["issues"],
        "backup": str(backup) if backup else None, "output": str(output),
    }
