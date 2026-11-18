"""Markdown reports for offer preflight, plan and apply documents.

The workflow appends these reports to ``$GITHUB_STEP_SUMMARY`` and they are also
usable locally. Rendering stays a pure function of the JSON document so it can
be tested without GitHub Actions.
"""

from __future__ import annotations

import html
import json
from typing import Any


def _display(value: Any) -> str:
    if value is None:
        return "(not set)"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _cell(value: Any, fallback: str = "-") -> str:
    text = fallback if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def resolve_targets(value: dict[str, Any]) -> str:
    """Render fact provenance, resolved targets and unresolved declared offers."""
    lines = ["## Resolved offer targets", ""]
    inventory = value.get("inventory")
    if isinstance(inventory, dict):
        owner = inventory.get("store") or {}
        scope = inventory.get("scope") or {}
        lines.extend([
            f"- Store: {_cell(owner.get('provider'))} (`{_cell(owner.get('id'))}`)",
            f"- Inventory source: `{_cell(inventory.get('source'))}`; queried at `{_cell(inventory.get('queriedAt'))}`",
            f"- Scope: `{_cell(scope.get('visibility'))}` `{_cell(scope.get('productType'))}`; complete: `{str(inventory.get('complete')).lower()}`",
            "",
        ])
    targets = value.get("targets") or []
    if targets:
        lines.extend(
            [
                "| Code | Offer | Match | Visibility | Title |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for target in targets:
            lines.append(
                _table_row(
                    [
                        f"`{_cell(target.get('code'))}`",
                        _cell(target.get("offer"), "base"),
                        _cell(target.get("match")),
                        _cell(target.get("visibility")),
                        _cell(target.get("title")),
                    ]
                )
            )
    else:
        lines.append("No live offer target was resolved.")
    unresolved = value.get("unresolved") or []
    for item in unresolved:
        lines.extend(
            ["", f"- Unresolved offer `{_cell(item.get('offer'))}`: {_cell(item.get('reason'))}"]
        )
    return "\n".join(lines) + "\n"


def preflight(value: dict[str, Any]) -> str:
    """Render the preflight summary, including forced codes."""
    codes = value.get("codes") or []
    unlisted = value.get("unlistedCodes") or []
    if value.get("codeSource") == "skipped":
        return "\n".join(
            [
                f"## Offer apply: `{value.get('app')}`",
                "",
                f"- Cloud: `{value.get('cloud')}`",
                "- Offer codes: skipped (live resolution follows)",
            ]
        ) + "\n"
    lines = [
        f"## Offer apply: `{value.get('app')}`",
        "",
        f"- Cloud: `{value.get('cloud')}`",
        f"- Offer code source: {value.get('codeSource')}",
        f"- Targets: {len(codes)}",
    ]
    if unlisted:
        quoted = ", ".join(f"`{code}`" for code in unlisted)
        lines.append(f"- :warning: Codes outside the offer-code matrix (forced): {quoted}")
    lines.extend(["", "| # | Offer code |", "| --- | --- |"])
    lines.extend(
        _table_row([str(index), f"`{code}`"]) for index, code in enumerate(codes, start=1)
    )
    return "\n".join(lines) + "\n"


def plan(
    value: dict[str, Any],
    *,
    cloud: str | None = None,
    app_name: str | None = None,
    account_id: str | None = None,
) -> str:
    """Render a reviewed plan, with before/after details per pending field."""
    app = str(value.get("key") or "")
    title = app if not app_name or app_name == app else f"{app_name} (`{app}`)"
    targets = value.get("targets") or [
        {
            "code": None,
            "entityId": str(value.get("baseRevision") or "").split("@")[0],
            "plan": value,
        }
    ]
    pending = sum(1 for target in targets if target["plan"].get("request"))
    lines = [f"## Offer plan: {title}", ""]
    if cloud:
        lines.append(f"- Cloud: `{cloud}`")
    lines.extend(
        [
            f"- Source hash: `{value.get('sourceHash')}`",
            f"- Plan hash: `{value.get('planHash', '')}`",
            "- Publish blockers: "
            + (", ".join(value.get("publishBlockers") or []) or "none"),
            f"- Targets: {len(targets)} ({pending} with pending changes)",
            "",
        ]
    )
    use_arn = cloud == "aws" and bool(account_id)
    lines.extend(
        [
            f"| Offer code | {'Entity ARN' if use_arn else 'EntityId'} "
            "| Base revision | Pending fields |",
            "| --- | --- | --- | --- |",
        ]
    )
    for target in targets:
        entity_id = str(target.get("entityId") or "")
        entity = (
            f"arn:aws:aws-marketplace:us-east-1:{account_id}:"
            f"AWSMarketplace/AmiProduct/{entity_id}"
            if use_arn
            else entity_id
        )
        lines.append(
            _table_row(
                [
                    f"`{target.get('code') or '-'}`",
                    f"`{entity}`",
                    f"`{target['plan'].get('baseRevision')}`",
                    str(len(target["plan"].get("diff") or {})),
                ]
            )
        )
    lines.append("")
    if not pending:
        lines.append("No cloud change is required.")
        return "\n".join(lines) + "\n"

    lines.extend(["### Pending updates", ""])
    for target in targets:
        diff = target["plan"].get("diff") or {}
        if not diff:
            continue
        lines.extend([f"#### {target.get('code') or target.get('entityId')}", ""])
        for field, change in diff.items():
            before = html.escape(_display(change.get("before")))
            after = html.escape(_display(change.get("after")))
            lines.extend(
                [
                    "<details>",
                    f"<summary><code>{html.escape(field)}</code></summary>",
                    "",
                    "**Before**",
                    "",
                    f"<pre>{before}</pre>",
                    "",
                    "**After**",
                    "",
                    f"<pre>{after}</pre>",
                    "",
                    "</details>",
                    "",
                ]
            )
    lines.append("The complete request is stored in the plan document.")
    return "\n".join(lines) + "\n"


def apply(value: dict[str, Any], *, cloud: str | None = None) -> str:
    """Render the ChangeSet submission result."""
    targets = value.get("targets") or []
    lines = [
        "### ChangeSet submission",
        "",
        f"- Status: `{value.get('status', 'unknown')}`",
        "",
    ]
    if targets:
        lines.extend(
            [
                "| Offer code | EntityId | Status | ChangeSetId |",
                "| --- | --- | --- | --- |",
            ]
        )
        for target in targets:
            lines.append(
                _table_row(
                    [
                        f"`{target.get('code') or '-'}`",
                        f"`{target.get('entityId')}`",
                        f"`{target.get('status')}`",
                        f"`{target.get('changeSetId', '-')}`",
                    ]
                )
            )
        lines.append("")
    elif value.get("changeSetId"):
        lines.extend(
            [
                f"- ChangeSetId: `{value['changeSetId']}`",
                f"- Plan hash: `{value.get('planHash', '')}`",
                "",
            ]
        )
    if value.get("skipped"):
        skipped = ", ".join(str(code) for code in value["skipped"])
        lines.extend([f"Skipped offer codes: {skipped}", ""])
    if value.get("status") == "PARTIAL":
        lines.extend(
            [
                "At least one target was not submitted. Check each submitted ChangeSet with "
                "`mk offer status <change-set-id> --wait-seconds 600`, then re-run the workflow; "
                "already-updated targets plan to NO_CHANGES and are skipped.",
                "",
            ]
        )
    accepted = f"{cloud} accepted" if cloud else "the marketplace accepted"
    lines.append(
        f"Success confirms {accepted} every ChangeSet. "
        "It does not await review, approval, or publication."
    )
    return "\n".join(lines) + "\n"
