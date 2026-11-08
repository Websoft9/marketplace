"""Push English application metadata to Contentful through the Management API."""

from __future__ import annotations

import copy
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .contentful import ContentfulError, USER_AGENT, _field_matcher, load_target_catalog, select_target_keys
from .marketplace import CATALOG_URL

DEFAULT_API_URL = "https://api.contentful.com"
ALLOWED_INPUT_FIELDS = ("trademark", "overview", "keywords", "usecases", "relatedApps", "summary", "hasenterprise")
IGNORED_INPUT_FIELDS = ("offerCodes",)
Progress = Callable[[str], None]


def _print(progress: Progress | None, message: str) -> None:
    if progress:
        progress(message)


def _base_url(api_url: str, space_id: str, environment: str) -> str:
    return (f"{api_url.rstrip('/')}/spaces/{urllib.parse.quote(space_id, safe='')}"
            f"/environments/{urllib.parse.quote(environment, safe='')}")


def _error_message(error: urllib.error.HTTPError) -> str:
    if error.code in {401, 403}:
        return f"Contentful Management API authentication/permission failed (HTTP {error.code}). Check CONTENTFUL_TOKEN (CFPAT-) and space access."
    if error.code == 404:
        return "Contentful Management API resource not found (HTTP 404). Check space, environment, content type and entry."
    if error.code == 409:
        return "Contentful version conflict (HTTP 409). The entry changed after it was read; rerun push."
    message = ""
    try:
        detail = json.loads(error.read(2000).decode("utf-8", errors="replace"))
        message = str(detail.get("message") or "").strip()
    except (AttributeError, OSError, ValueError, UnicodeError):
        message = ""
    suffix = f" {message}" if message else ""
    return f"Contentful Management API request failed (HTTP {error.code}).{suffix}".strip()


def _request(
    method: str,
    url: str,
    token: str,
    *,
    payload: Any = None,
    version: int | str | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/vnd.contentful.management.v1+json",
        "User-Agent": USER_AGENT,
        "X-Contentful-User-Agent": "app websoft9-manufacture/1.0",
    }
    if version is not None:
        headers["X-Contentful-Version"] = str(version)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(3):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as error:
            if error.code in {429, 500, 502, 503, 504} and attempt < 2:
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    delay = min(float(retry_after), 10) if retry_after else 2**attempt
                except ValueError:
                    delay = 2**attempt
                time.sleep(delay)
                continue
            raise ContentfulError(_error_message(error)) from None
        except urllib.error.URLError as error:
            raise ContentfulError(f"Contentful Management API request failed: {error.reason}") from None
        except (TimeoutError, OSError) as error:
            raise ContentfulError(f"Contentful Management API request failed: {error}") from None
        except (ValueError, UnicodeError):
            raise ContentfulError("Contentful Management API returned an invalid JSON response.") from None
    raise ContentfulError("Contentful Management API request failed after retries.")


def fetch_schema(
    *, token: str, space_id: str, environment: str, content_type: str,
    api_url: str = DEFAULT_API_URL, timeout: float = 30,
) -> dict[str, dict[str, Any]]:
    url = f"{_base_url(api_url, space_id, environment)}/content_types/{urllib.parse.quote(content_type, safe='')}"
    payload = _request("GET", url, token, timeout=timeout)
    fields = payload.get("fields") or []
    if not isinstance(fields, list):
        raise ContentfulError("Contentful content-type schema is malformed.")
    result: dict[str, dict[str, Any]] = {}
    for field in fields:
        if not isinstance(field, dict) or not field.get("id"):
            continue
        result[str(field["id"])] = {
            "type": str(field.get("type", "")),
            "localized": bool(field.get("localized")),
            "items": field.get("items") if isinstance(field.get("items"), dict) else {},
        }
    return result


MAX_FILTER_CHARS = 6000


def _chunk_filter(values: tuple[str, ...], limit: int = MAX_FILTER_CHARS):
    """Yield tuples of values whose comma-joined encoded length stays under limit."""
    chunk: list[str] = []
    size = 0
    for value in values:
        encoded = urllib.parse.quote(str(value), safe="")
        extra = len(encoded) + (1 if chunk else 0)
        if chunk and size + extra > limit:
            yield tuple(chunk)
            chunk = []
            size = 0
            extra = len(encoded)
        chunk.append(str(value))
        size += extra
    if chunk:
        yield tuple(chunk)


def _entry_filters(
    keys: tuple[str, ...] | None, ids: tuple[str, ...] | None,
) -> list[dict[str, str]]:
    """Split entry filters into URL-safe chunks to avoid HTTP 414 responses."""
    if ids:
        chunks = [{"sys.id[in]": ",".join(part)} for part in _chunk_filter(tuple(ids))]
        if keys:
            key_filter = _key_filter(keys)
            chunks = [{**chunk, **key_filter} for chunk in chunks]
        return chunks
    if keys:
        if len(keys) == 1:
            return [{"fields.key": keys[0]}]
        return [{"fields.key[in]": ",".join(part)} for part in _chunk_filter(tuple(keys))]
    return [{}]


def _key_filter(keys: tuple[str, ...]) -> dict[str, str]:
    if len(keys) == 1:
        return {"fields.key": keys[0]}
    return {"fields.key[in]": ",".join(keys)}


def fetch_entries(
    *, token: str, space_id: str, environment: str, content_type: str | None = None,
    keys: tuple[str, ...] | None = None, ids: tuple[str, ...] | None = None,
    api_url: str = DEFAULT_API_URL, page_size: int = 1000, timeout: float = 30,
    select: str | None = None, progress: Progress | None = None,
) -> list[dict[str, Any]]:
    if not 1 <= page_size <= 1000:
        raise ContentfulError("page size must be between 1 and 1000.")
    filters = _entry_filters(keys, ids)
    entries: list[dict[str, Any]] = []
    base = f"{_base_url(api_url, space_id, environment)}/entries"
    for index, extra in enumerate(filters, start=1):
        label = f"[{index}/{len(filters)}] " if len(filters) > 1 else ""
        skip = 0
        total: int | None = None
        while True:
            params = {"limit": page_size, "skip": skip}
            if content_type:
                params["content_type"] = content_type
            if select:
                params["select"] = select
            params.update(extra)
            query = urllib.parse.urlencode(params)
            payload = _request("GET", f"{base}?{query}", token, timeout=timeout)
            page = payload.get("items") or []
            if total is None:
                total = int(payload.get("total") or len(page))
                _print(progress, f"{label}Contentful entries reported: {total}")
            entries.extend(page)
            skip += len(page)
            _print(progress, f"{label}Fetched {min(skip, total)}/{total} Contentful entries")
            if not page:
                if skip < total:
                    raise ContentfulError("Contentful returned an empty page before the reported total.")
                break
            if skip >= total:
                break
            if skip > total + page_size:
                raise ContentfulError("Contentful pagination did not converge.")
    return entries


def update_entry(
    *, token: str, space_id: str, environment: str, entry_id: str, fields: dict[str, Any],
    version: int | str, api_url: str = DEFAULT_API_URL, timeout: float = 30,
) -> dict[str, Any]:
    url = f"{_base_url(api_url, space_id, environment)}/entries/{urllib.parse.quote(entry_id, safe='')}"
    return _request("PUT", url, token, payload={"fields": fields}, version=version, timeout=timeout)


def publish_entry(
    *, token: str, space_id: str, environment: str, entry_id: str,
    version: int | str, api_url: str = DEFAULT_API_URL, timeout: float = 30,
) -> dict[str, Any]:
    url = f"{_base_url(api_url, space_id, environment)}/entries/{urllib.parse.quote(entry_id, safe='')}/published"
    return _request("PUT", url, token, payload={}, version=version, timeout=timeout)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False


def _localized_text(value: Any, locale: str) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        if isinstance(value.get(locale), str):
            return value[locale].strip() or None
        for candidate in value.values():
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def _link_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        sys = item.get("sys") or {}
        if sys.get("type") == "Link" and sys.get("linkType") == "Entry" and isinstance(sys.get("id"), str):
            ids.append(sys["id"])
    return ids


def _related_app_links(
    value: Any,
    related_app_ids: dict[str, str] | None,
    allowed_related_apps: set[str] | None = None,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(name, str) for name in value):
        raise ContentfulError("relatedApps must be an array of application Trademarks.")
    names: list[str] = []
    seen: set[str] = set()
    for name in value:
        name = name.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if allowed_related_apps is not None and name not in allowed_related_apps:
            continue
        names.append(name)
    if names and not related_app_ids:
        raise ContentfulError("relatedApps resolution requires the Contentful product entries.")
    links: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in names:
        entry_id = related_app_ids.get(name) if related_app_ids else None
        if not entry_id:
            missing.append(name)
            continue
        links.append({"sys": {"type": "Link", "linkType": "Entry", "id": entry_id}})
    if missing:
        raise ContentfulError(f"relatedApps not found in Contentful: {', '.join(missing)}")
    return links


def _related_app_id_map(
    *, token: str, space_id: str, environment: str, content_type: str, locale: str,
    api_url: str, page_size: int, timeout: float, progress: Progress | None,
    keys: tuple[str, ...] | None = None,
) -> dict[str, str]:
    entries = fetch_entries(
        token=token, space_id=space_id, environment=environment,
        content_type=content_type, keys=keys, ids=None,
        api_url=api_url, page_size=page_size, timeout=timeout,
        select="sys.id,fields.trademark", progress=progress,
    )
    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for entry in entries:
        fields = entry.get("fields") or {}
        trademark = _localized_text(fields.get("trademark"), locale)
        entry_id = (entry.get("sys") or {}).get("id")
        if not trademark or not isinstance(entry_id, str):
            continue
        if trademark in mapping and mapping[trademark] != entry_id:
            ambiguous.add(trademark)
            continue
        mapping[trademark] = entry_id
    if ambiguous:
        raise ContentfulError(f"Ambiguous Trademarks in Contentful: {', '.join(sorted(ambiguous))}")
    return mapping


def _validate_value(name: str, value: Any, meta: dict[str, Any]) -> None:
    field_type = meta.get("type")
    if field_type in {"Symbol", "Text"}:
        if value is not None and not isinstance(value, str):
            raise ContentfulError(f"{name} must be a string.")
        return
    if field_type == "Array":
        if value is not None and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
            raise ContentfulError(f"{name} must be an array of strings.")
        if meta.get("items", {}).get("type") not in {None, "", "Symbol"}:
            raise ContentfulError(f"{name} uses an unsupported array item type.")
        return
    if field_type == "Boolean":
        if value is not None and not isinstance(value, bool):
            raise ContentfulError(f"{name} must be a boolean.")
        return
    raise ContentfulError(f"{name} uses unsupported Contentful field type {field_type!r}.")


def build_changes(
    item: dict[str, Any],
    entry: dict[str, Any],
    schema: dict[str, dict[str, Any]],
    locale: str = "en-US",
    clear: bool = False,
    related_app_ids: dict[str, str] | None = None,
    allowed_related_apps: set[str] | None = None,
    field_names: set[str] | None = None,
) -> tuple[dict[str, Any], list[str], list[str], list[str]]:
    """Merge only target fields/locale. Returns fields, changed, skipped-empty, unchanged."""
    unknown = set(item) - {"id"} - set(ALLOWED_INPUT_FIELDS) - set(IGNORED_INPUT_FIELDS)
    if unknown:
        raise ContentfulError(f"Unsupported input field(s): {', '.join(sorted(unknown))}")
    if field_names is not None:
        unknown_fields = field_names - set(ALLOWED_INPUT_FIELDS)
        if unknown_fields:
            raise ContentfulError(f"Unsupported push field(s): {', '.join(sorted(unknown_fields))}")
    fields = copy.deepcopy(entry.get("fields") or {})
    changed: list[str] = []
    skipped: list[str] = []
    unchanged: list[str] = []
    for name in ALLOWED_INPUT_FIELDS:
        if name not in item:
            continue
        if field_names is not None and name not in field_names:
            continue
        schema_field = _field_matcher(schema, name)
        if schema_field is None:
            raise ContentfulError(f"Contentful content type has no field matching {name!r}.")
        meta = schema[schema_field]
        value = item[name]
        if name == "relatedApps":
            items_meta = meta.get("items") or {}
            if meta.get("type") != "Array" or items_meta.get("linkType") != "Entry":
                raise ContentfulError("Contentful relatedApps field is not a linked Entry array.")
            value = _related_app_links(value, related_app_ids, allowed_related_apps)
        else:
            _validate_value(name, value, meta)
        empty = _is_empty(value)
        if empty and not clear:
            skipped.append(name)
            continue
        if empty:
            field_type = meta.get("type")
            value = [] if field_type == "Array" else False if field_type == "Boolean" else ""
        localized = bool(meta.get("localized"))
        raw = fields.get(schema_field)
        # CMA returns fields as locale-keyed dictionaries, including some
        # non-localized fields. Preserve that shape and other locales.
        if isinstance(raw, dict):
            current_container = dict(raw)
            current = current_container.get(locale)
            if current is None and not localized and current_container:
                current = next(iter(current_container.values()), None)
        else:
            current_container = {}
            current = raw
        if name == "relatedApps":
            same = _link_ids(current) == _link_ids(value)
        else:
            same = current == value
        if same:
            unchanged.append(name)
            continue
        current_container[locale] = value
        fields[schema_field] = current_container
        changed.append(name)
    return fields, changed, skipped, unchanged


def load_input(path: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    try:
        if path == "-":
            data = json.load(sys.stdin)
        else:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ContentfulError(f"failed to read input {path!r}: {error}") from None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items")
    else:
        raise ContentfulError("input must be a JSON object with items or a JSON array.")
    if not isinstance(items, list) or not items:
        raise ContentfulError("input contains no application items.")
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ContentfulError(f"input item {index} is not an object.")
        app_id = item.get("id")
        if not isinstance(app_id, str) or not app_id.strip():
            raise ContentfulError(f"input item {index} has no string id.")
        app_id = app_id.strip()
        if app_id in by_id:
            raise ContentfulError(f"duplicate input application id: {app_id}")
        unknown = set(item) - {"id"} - set(ALLOWED_INPUT_FIELDS) - set(IGNORED_INPUT_FIELDS)
        if unknown:
            raise ContentfulError(f"{app_id}: unsupported input field(s): {', '.join(sorted(unknown))}")
        by_id[app_id] = item
        order.append(app_id)
    return by_id, order


def _entry_key(
    entry: dict[str, Any],
    schema: dict[str, dict[str, Any]],
    id_field: str = "key",
    locale: str = "en-US",
) -> str | None:
    fields = entry.get("fields") or {}
    schema_field = _field_matcher(schema, id_field)
    if schema_field is None:
        raise ContentfulError(f"Contentful content type has no {id_field!r} field for application matching.")
    value = fields.get(schema_field)
    if isinstance(value, dict):
        value = value.get(locale)
        if not isinstance(value, str):
            value = next((item for item in value.values() if isinstance(item, str)), None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def push(
    *,
    token: str,
    space_id: str,
    environment: str,
    input_path: str,
    content_type: str = "product",
    locale: str = "en-US",
    id_field: str = "key",
    api_url: str = DEFAULT_API_URL,
    app_ids: tuple[str, ...] | None = None,
    page_size: int = 1000,
    timeout: float = 30,
    dry_run: bool = False,
    publish: bool = False,
    clear: bool = False,
    allow_missing: bool = False,
    verbose: bool = False,
    allowed_related_apps: set[str] | None = None,
    catalog_keys: tuple[str, ...] | None = None,
    catalog_source: str | None = None,
    progress: Progress | None = None,
    field_names: set[str] | None = None,
) -> dict[str, Any]:
    if not token or not token.strip():
        raise ContentfulError("a Contentful Management API token is required.")
    if not space_id or not space_id.strip():
        raise ContentfulError("a Contentful space id is required.")
    if dry_run and publish:
        raise ContentfulError("--publish cannot be combined with --dry-run.")
    by_id, order = load_input(input_path)
    selected = select_target_keys(order, app_ids)
    _print(progress, f"Selected applications: {len(selected)}")
    schema = fetch_schema(token=token.strip(), space_id=space_id.strip(), environment=environment.strip(),
                          content_type=content_type.strip(), api_url=api_url, timeout=timeout)
    entries = fetch_entries(token=token.strip(), space_id=space_id.strip(), environment=environment.strip(),
                            content_type=content_type.strip(), keys=tuple(selected),
                            api_url=api_url, page_size=page_size, timeout=timeout, progress=progress)
    by_key: dict[str, dict[str, Any]] = {}
    for entry in entries:
        key = _entry_key(entry, schema, id_field, locale)
        if key:
            if key in by_key:
                raise ContentfulError(f"duplicate Contentful application key: {key}")
            by_key[key] = entry
    related_app_ids: dict[str, str] | None = None
    resolved_allowed = allowed_related_apps
    related_apps_selected = field_names is None or "relatedApps" in field_names
    if related_apps_selected and any(by_id[app].get("relatedApps") for app in selected):
        if resolved_allowed is None or catalog_keys is None:
            catalog = load_target_catalog(catalog_source or CATALOG_URL, timeout, progress)
            if catalog_keys is None:
                catalog_keys = tuple(item["key"].strip() for item in catalog)
            if resolved_allowed is None:
                resolved_allowed = {
                    item["trademark"].strip()
                    for item in catalog
                    if isinstance(item.get("trademark"), str) and item["trademark"].strip()
                }
        _print(progress, "Resolving relatedApps to Contentful product entries")
        related_app_ids = _related_app_id_map(
            token=token.strip(), space_id=space_id.strip(), environment=environment.strip(),
            content_type=content_type.strip(), locale=locale, api_url=api_url,
            page_size=page_size, timeout=timeout, progress=progress,
            keys=tuple(catalog_keys) if catalog_keys else None)
    summary = {"selected": len(selected), "matched": 0, "updated": 0, "published": 0,
               "unchanged": 0, "skippedEmpty": 0, "missing": [], "errors": []}
    for app_id in selected:
        entry = by_key.get(app_id)
        if entry is None:
            if allow_missing:
                summary["missing"].append(app_id)
                _print(progress, f"Missing in Contentful, skipped: {app_id}")
                continue
            summary["errors"].append(f"{app_id}: not found in Contentful")
            continue
        summary["matched"] += 1
        try:
            fields, changed, skipped, unchanged = build_changes(
                by_id[app_id], entry, schema, locale=locale, clear=clear,
                related_app_ids=related_app_ids, allowed_related_apps=resolved_allowed,
                field_names=field_names)
        except ContentfulError as error:
            summary["errors"].append(f"{app_id}: {error}")
            continue
        summary["skippedEmpty"] += len(skipped)
        if not changed:
            summary["unchanged"] += 1
            if verbose:
                _print(progress, f"Unchanged: {app_id}")
            continue
        if dry_run:
            summary["updated"] += 1
            if verbose:
                _print(progress, f"Would update {app_id}: {', '.join(changed)}")
            continue
        try:
            updated = update_entry(
                token=token.strip(), space_id=space_id.strip(), environment=environment.strip(),
                entry_id=entry["sys"]["id"], fields=fields, version=entry["sys"]["version"],
                api_url=api_url, timeout=timeout)
            summary["updated"] += 1
            if publish:
                new_version = (updated.get("sys") or {}).get("version")
                if new_version is None:
                    raise ContentfulError("Contentful update response has no entry version.")
                publish_entry(
                    token=token.strip(), space_id=space_id.strip(), environment=environment.strip(),
                    entry_id=entry["sys"]["id"], version=new_version, api_url=api_url, timeout=timeout)
                summary["published"] += 1
            if verbose:
                _print(progress, f"Updated {app_id}: {', '.join(changed)}")
        except ContentfulError as error:
            summary["errors"].append(f"{app_id}: {error}")
    return summary
