"""Mechanical validation for the staging application catalog.

The catalog is the JSON dataset produced by ``mk catalog pull`` and consumed by
``mk catalog push``. This module checks the field rules used by the content
skills and, when a baseline snapshot is supplied, that only the selected
applications and fields changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIELDS = (
    "trademark",
    "overview",
    "keywords",
    "usecases",
    "relatedApps",
    "summary",
    "hasenterprise",
)
GENERATED_FIELDS = ("overview", "keywords", "usecases", "relatedApps", "summary", "hasenterprise")

MAX_OVERVIEW_CHARS = 350
MAX_SUMMARY_WORDS = 10
MAX_USECASES = 10
MAX_USECASE_WORDS = 4
MAX_RELATED_APPS = 8
MAX_KEYWORD_CHARS = 50
TRAILING_PUNCTUATION = ".,;:!?"
GENERIC_RUNTIME = {"docker", "nginx", "kubernetes", "linux", "containerd", "podman"}
RELATIONAL_DATA_STORES = {
    "PostgreSQL",
    "MySQL",
    "MariaDB",
    "SQL Server Express",
    "Oracle Database",
}
DB_ADMIN_TOOLS = {"pgAdmin", "phpMyAdmin", "CloudBeaver", "RedisInsight"}
MODEL_RUNTIMES = {"Ollama", "LocalAI"}
VECTOR_STORES = {"Chroma", "Weaviate", "Milvus", "Vespa"}
SCM_TOOLS = {"GitLab", "Gitea"}
HYPE_WORDS = (
    "leading",
    "best",
    "world-class",
    "world class",
    "cutting-edge",
    "cutting edge",
    "revolutionary",
    "game-changing",
    "trusted by",
    "number one",
)


def load_dataset(path: str) -> dict[str, Any]:
    """Read and shape-check a catalog dataset."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"failed to read catalog {path!r}: {error}") from None
    except ValueError as error:
        raise ValueError(f"catalog {path!r} is not valid JSON: {error}") from None
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("catalog must be a JSON array or an object with an items array")
    return data


def verify(
    data: dict[str, Any],
    *,
    app_ids: list[str] | tuple[str, ...] | None = None,
    fields: list[str] | tuple[str, ...] | None = None,
    baseline: dict[str, Any] | None = None,
    require_non_empty: bool = False,
) -> dict[str, Any]:
    """Validate selected fields for selected applications.

    ``app_ids`` defaults to every item and ``fields`` defaults to every
    generated field. When ``baseline`` is supplied, every item outside the
    selection and every field outside the selection must be unchanged.
    """
    issues: list[dict[str, str]] = []
    selected_fields = tuple(dict.fromkeys(fields or GENERATED_FIELDS))
    unknown_fields = [name for name in selected_fields if name not in FIELDS]
    if unknown_fields:
        raise ValueError(f"unknown field(s): {', '.join(unknown_fields)}")
    if not selected_fields:
        raise ValueError("at least one field is required")

    items = data["items"]
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            _issue(issues, "error", None, None, f"item {index} is not an object")
            continue
        app_id = item.get("id")
        if not isinstance(app_id, str) or not app_id.strip():
            _issue(issues, "error", None, None, f"item {index} has no string id")
            continue
        app_id = app_id.strip()
        if app_id in by_id:
            _issue(issues, "error", app_id, None, "duplicate application id")
            continue
        by_id[app_id] = item
        order.append(app_id)

    selected = list(app_ids) if app_ids is not None else list(order)
    if len(set(selected)) != len(selected):
        _issue(issues, "error", None, None, "duplicate application id in selection")
    if app_ids is not None:
        for app_id in selected:
            if app_id not in by_id:
                _issue(issues, "error", app_id, None, "not present in catalog")

    trademarks = {
        item["trademark"].strip()
        for item in by_id.values()
        if isinstance(item.get("trademark"), str) and item["trademark"].strip()
    }

    checked: dict[str, dict[str, str]] = {}
    for app_id in selected:
        item = by_id.get(app_id)
        if item is None:
            continue
        checked[app_id] = {}
        for name in selected_fields:
            _check_field(item, name, trademarks, issues, require_non_empty)
            checked[app_id][name] = "ok"

    if data.get("count") is not None and data.get("count") != len(items):
        _issue(issues, "error", None, None, f"count is {data.get('count')}, items has {len(items)}")

    if baseline is not None:
        _compare_baseline(data, baseline, set(selected), set(selected_fields), issues)

    errors = [issue for issue in issues if issue["severity"] == "error"]
    return {
        "selected": selected,
        "fields": list(selected_fields),
        "checked": checked,
        "issues": issues,
        "errors": len(errors),
        "warnings": len(issues) - len(errors),
        "baseline": baseline is not None,
    }


def _issue(
    issues: list[dict[str, str]],
    severity: str,
    app_id: str | None,
    field: str | None,
    message: str,
) -> None:
    issues.append(
        {
            "severity": severity,
            "app": app_id or "",
            "field": field or "",
            "message": message,
        }
    )


def _check_field(
    item: dict[str, Any],
    name: str,
    trademarks: set[str],
    issues: list[dict[str, str]],
    require_non_empty: bool,
) -> None:
    app_id = item["id"]
    if name not in item:
        _issue(issues, "error", app_id, name, "field is missing")
        return
    value = item[name]
    if name == "keywords":
        _check_keywords(app_id, value, issues, require_non_empty)
    elif name == "usecases":
        _check_usecases(app_id, value, issues, require_non_empty)
    elif name == "relatedApps":
        _check_related_apps(app_id, item, value, trademarks, issues)
    elif name == "overview":
        _check_overview(app_id, value, item.get("summary"), issues)
    elif name == "summary":
        _check_summary(app_id, value, issues)
    elif name == "hasenterprise":
        if not isinstance(value, bool):
            _issue(issues, "error", app_id, name, "must be a JSON Boolean")
    elif name == "trademark":
        _check_string(app_id, name, value, issues)


def _check_string(app_id: str, name: str, value: Any, issues: list[dict[str, str]]) -> None:
    if not isinstance(value, str):
        _issue(issues, "error", app_id, name, "must be a string")
        return
    if value != value.strip():
        _issue(issues, "error", app_id, name, "has surrounding whitespace")
    if not value.strip():
        _issue(issues, "error", app_id, name, "must not be empty")


def _check_keywords(
    app_id: str,
    value: Any,
    issues: list[dict[str, str]],
    require_non_empty: bool,
) -> None:
    if not isinstance(value, list):
        _issue(issues, "error", app_id, "keywords", "must be an array")
        return
    if not value:
        _issue(
            issues,
            "error" if require_non_empty else "warning",
            app_id,
            "keywords",
            "is empty; generated content should contain at least one keyword",
        )
        return
    seen: set[str] = set()
    for index, keyword in enumerate(value):
        if not isinstance(keyword, str):
            _issue(issues, "error", app_id, "keywords", f"[{index}] must be a string")
            continue
        if keyword != keyword.strip():
            _issue(issues, "error", app_id, "keywords", f"[{index}] has surrounding whitespace")
        if not keyword.strip():
            _issue(issues, "error", app_id, "keywords", f"[{index}] is empty")
            continue
        if len(keyword) > MAX_KEYWORD_CHARS:
            _issue(
                issues,
                "error",
                app_id,
                "keywords",
                f"[{index}] is {len(keyword)} characters, maximum {MAX_KEYWORD_CHARS}",
            )
        if keyword in seen:
            _issue(issues, "error", app_id, "keywords", f"[{index}] duplicates {keyword!r}")
        seen.add(keyword)
        if keyword != keyword.lower():
            _issue(issues, "warning", app_id, "keywords", f"[{index}] is not lowercase")
        if keyword.endswith(tuple(TRAILING_PUNCTUATION)):
            _issue(issues, "warning", app_id, "keywords", f"[{index}] ends with punctuation")


def _check_usecases(
    app_id: str,
    value: Any,
    issues: list[dict[str, str]],
    require_non_empty: bool,
) -> None:
    if not isinstance(value, list):
        _issue(issues, "error", app_id, "usecases", "must be an array")
        return
    if not value:
        _issue(
            issues,
            "error" if require_non_empty else "warning",
            app_id,
            "usecases",
            "is empty; generated content should contain at least one use case",
        )
        return
    if len(value) > MAX_USECASES:
        _issue(
            issues,
            "error",
            app_id,
            "usecases",
            f"has {len(value)} items, maximum {MAX_USECASES}",
        )
    seen: set[str] = set()
    for index, usecase in enumerate(value):
        if not isinstance(usecase, str):
            _issue(issues, "error", app_id, "usecases", f"[{index}] must be a string")
            continue
        if usecase != usecase.strip():
            _issue(issues, "error", app_id, "usecases", f"[{index}] has surrounding whitespace")
        words = usecase.split()
        if not words:
            _issue(issues, "error", app_id, "usecases", f"[{index}] is empty")
            continue
        if len(words) > MAX_USECASE_WORDS:
            _issue(
                issues,
                "error",
                app_id,
                "usecases",
                f"[{index}] has {len(words)} words, maximum {MAX_USECASE_WORDS}",
            )
        if usecase.endswith(tuple(TRAILING_PUNCTUATION)):
            _issue(issues, "error", app_id, "usecases", f"[{index}] has trailing punctuation")
        if usecase in seen:
            _issue(issues, "error", app_id, "usecases", f"[{index}] duplicates {usecase!r}")
        seen.add(usecase)
        if usecase[:1].islower():
            _issue(issues, "warning", app_id, "usecases", f"[{index}] is not capitalized")


def _check_related_apps(
    app_id: str,
    item: dict[str, Any],
    value: Any,
    trademarks: set[str],
    issues: list[dict[str, str]],
) -> None:
    if not isinstance(value, list):
        _issue(issues, "error", app_id, "relatedApps", "must be an array")
        return
    if len(value) > MAX_RELATED_APPS:
        _issue(
            issues,
            "warning",
            app_id,
            "relatedApps",
            f"has {len(value)} items; AI-generated lists must stay at "
            f"{MAX_RELATED_APPS}, existing human lists are allowed",
        )
    own = item.get("trademark")
    own = own.strip() if isinstance(own, str) else None
    seen: set[str] = set()
    for index, related in enumerate(value):
        if not isinstance(related, str):
            _issue(issues, "error", app_id, "relatedApps", f"[{index}] must be a string")
            continue
        if related != related.strip():
            _issue(issues, "error", app_id, "relatedApps", f"[{index}] has surrounding whitespace")
        if not related.strip():
            _issue(issues, "error", app_id, "relatedApps", f"[{index}] is empty")
            continue
        if related in seen:
            _issue(issues, "error", app_id, "relatedApps", f"[{index}] duplicates {related!r}")
        seen.add(related)
        if related == own:
            _issue(issues, "error", app_id, "relatedApps", "must not reference itself")
        elif related not in trademarks:
            _issue(
                issues,
                "error",
                app_id,
                "relatedApps",
                f"[{index}] {related!r} is not a trademark in this catalog",
            )
        if related.lower() in GENERIC_RUNTIME:
            _issue(issues, "warning", app_id, "relatedApps", f"[{index}] is generic runtime")
    _check_layer_conflicts(app_id, value, issues)


def _check_layer_conflicts(
    app_id: str,
    value: list[Any],
    issues: list[dict[str, str]],
) -> None:
    valid = [item for item in value if isinstance(item, str) and item.strip()]
    for layer, members in (
        ("relational data stores", RELATIONAL_DATA_STORES),
        ("database admin tools", DB_ADMIN_TOOLS),
        ("model runtimes", MODEL_RUNTIMES),
        ("vector stores", VECTOR_STORES),
        ("source-control tools", SCM_TOOLS),
    ):
        found = [item for item in valid if item in members]
        if len(found) > 1:
            _issue(
                issues,
                "warning",
                app_id,
                "relatedApps",
                f"lists multiple {layer} ({', '.join(found)}); choose one per layer",
            )


def _check_overview(
    app_id: str,
    value: Any,
    summary: Any,
    issues: list[dict[str, str]],
) -> None:
    _check_string(app_id, "overview", value, issues)
    if not isinstance(value, str):
        return
    text = value.strip()
    if len(value) > MAX_OVERVIEW_CHARS:
        _issue(
            issues,
            "error",
            app_id,
            "overview",
            f"is {len(value)} characters, maximum {MAX_OVERVIEW_CHARS}",
        )
    if text and text[-1] not in ".!?":
        _issue(issues, "warning", app_id, "overview", "does not end with sentence punctuation")
    if isinstance(summary, str) and text == summary.strip():
        _issue(issues, "warning", app_id, "overview", "duplicates the summary")
    _check_hype(app_id, "overview", text, issues)


def _check_summary(app_id: str, value: Any, issues: list[dict[str, str]]) -> None:
    _check_string(app_id, "summary", value, issues)
    if not isinstance(value, str):
        return
    text = value.strip()
    words = text.split()
    if not words:
        return
    if len(words) > MAX_SUMMARY_WORDS:
        _issue(
            issues,
            "error",
            app_id,
            "summary",
            f"has {len(words)} words, maximum {MAX_SUMMARY_WORDS}",
        )
    if text[-1] in TRAILING_PUNCTUATION:
        _issue(issues, "warning", app_id, "summary", "has trailing punctuation")
    lowered = text.lower()
    if "open source" in lowered or "open-source" in lowered:
        _issue(
            issues,
            "error",
            app_id,
            "summary",
            "must not use open source/open-source; use the words for a differentiator",
        )
    _check_hype(app_id, "summary", text, issues)


def _check_hype(app_id: str, name: str, text: str, issues: list[dict[str, str]]) -> None:
    lowered = f" {text.lower()} "
    for phrase in HYPE_WORDS:
        if f" {phrase} " in lowered:
            _issue(issues, "warning", app_id, name, f"contains hype wording {phrase!r}")
            break


def _compare_baseline(
    current: dict[str, Any],
    baseline: dict[str, Any],
    selected: set[str],
    selected_fields: set[str],
    issues: list[dict[str, str]],
) -> None:
    current_items = current.get("items") or []
    baseline_items = baseline.get("items") or []
    current_order = [item.get("id") for item in current_items if isinstance(item, dict)]
    baseline_order = [item.get("id") for item in baseline_items if isinstance(item, dict)]
    if current_order != baseline_order:
        _issue(issues, "error", None, None, "item order or item set changed")

    current_by_id = {
        item.get("id"): item for item in current_items if isinstance(item, dict) and item.get("id")
    }
    baseline_by_id = {
        item.get("id"): item for item in baseline_items if isinstance(item, dict) and item.get("id")
    }
    for app_id, before in baseline_by_id.items():
        after = current_by_id.get(app_id)
        if after is None:
            _issue(issues, "error", app_id, None, "item was removed")
            continue
        if app_id in selected:
            keys = (set(before) | set(after)) - selected_fields - {"id"}
            for key in sorted(keys):
                if before.get(key) != after.get(key):
                    _issue(issues, "error", app_id, key, "unselected field changed")
        elif before != after:
            _issue(issues, "error", app_id, None, "unselected item changed")

    for key in sorted((set(current) | set(baseline)) - {"items"}):
        if current.get(key) != baseline.get(key):
            _issue(issues, "error", None, key, "dataset metadata changed")
