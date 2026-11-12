#!/usr/bin/env python3
"""List and safely clear OpenCode V2 sessions through the running service API."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode


class OpenCodeError(RuntimeError):
    """A service request or response failed; never expose raw API output."""


class OpenCodeAPI:
    def __init__(self, server: str | None = None) -> None:
        self.server = server

    def request(self, method: str, path: str) -> object:
        command = ["opencode", "api", method, path]
        if self.server:
            command.extend(["--server", self.server])
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=60
            )
        except FileNotFoundError as error:
            raise OpenCodeError("opencode is not installed or not on PATH") from error
        except subprocess.TimeoutExpired as error:
            raise OpenCodeError(f"OpenCode API {method} request timed out") from error
        if result.returncode:
            # stderr / response bodies may contain credentials or private session data.
            raise OpenCodeError(
                f"OpenCode API {method} request failed (exit {result.returncode}); "
                "check `opencode service status` and your server configuration"
            )
        if method == "delete":
            return None  # V2 DELETE responds with 204 No Content.
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise OpenCodeError("OpenCode API did not return valid V2 JSON") from error

    def sessions(self) -> list[dict]:
        sessions: dict[str, dict] = {}
        cursor = None
        seen_cursors: set[str] = set()
        while True:
            query = {"cursor": cursor} if cursor else {"limit": 100}
            page = self.request("get", "/api/session?" + urlencode(query))
            if (
                not isinstance(page, dict)
                or not isinstance(page.get("data"), list)
                or not isinstance(page.get("cursor"), dict)
            ):
                raise OpenCodeError("Unexpected V2 session list response")
            for session in page["data"]:
                if (
                    not isinstance(session, dict)
                    or not isinstance(session.get("id"), str)
                    or not session["id"]
                    or not isinstance(session.get("location"), dict)
                    or not isinstance(session["location"].get("directory"), str)
                    or not Path(session["location"]["directory"]).is_absolute()
                    or ("parentID" in session and not isinstance(session["parentID"], str))
                    or ("time" in session and not isinstance(session["time"], dict))
                ):
                    raise OpenCodeError("Unexpected V2 session record")
                sessions[session["id"]] = session
            cursor = page["cursor"].get("next")
            if cursor is None:
                return list(sessions.values())
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise OpenCodeError("Invalid or repeated V2 pagination cursor")
            seen_cursors.add(cursor)

    def remove(self, session_id: str) -> None:
        self.request("delete", "/api/session/" + quote(session_id, safe=""))


def default_directory() -> Path:
    """Use the current repository root, even when invoked from a subdirectory."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Path.cwd().resolve()
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return Path.cwd().resolve()


def selected_sessions(sessions: list[dict], directory: Path | None) -> list[dict]:
    ids = {
        session["id"]
        for session in sessions
        if directory is None
        or Path(session["location"]["directory"]).is_relative_to(directory)
    }
    # DELETE cascades to children, including children moved outside this directory.
    # Include them in the preview / confirmation rather than silently deleting them.
    while True:
        descendants = {
            session["id"] for session in sessions if session.get("parentID") in ids
        }
        expanded = ids | descendants
        if expanded == ids:
            return [session for session in sessions if session["id"] in ids]
        ids = expanded


def format_created(value: object) -> str:
    try:
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown"


def display(sessions: list[dict], directory: Path | None) -> None:
    print(f"Scope: {directory or 'all directories'} (including child sessions)")
    if not sessions:
        print("No sessions found.")
        return
    print(f"{len(sessions)} session(s)")
    for session in sessions:
        created = format_created(session.get("time", {}).get("created"))
        print(
            f"{created}  {session['id']}  {session.get('title') or '(untitled)'}  "
            f"{session['location']['directory']}"
        )


def delete_sessions(api: OpenCodeAPI, sessions: list[dict]) -> int:
    ids = {session["id"] for session in sessions}
    roots = [session for session in sessions if session.get("parentID") not in ids]
    reachable = {session["id"] for session in roots}
    while True:
        expanded = reachable | {
            session["id"] for session in sessions if session.get("parentID") in reachable
        }
        if expanded == reachable:
            break
        reachable = expanded
    if reachable != ids:
        raise OpenCodeError("Invalid session parent graph; refusing to delete")
    # A parent delete also removes descendants. Do not delete them again (404).
    for session in roots:
        api.remove(session["id"])
    return len(sessions)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true", help="include sessions from all directories")
    scope.add_argument(
        "--directory", type=Path,
        help="include this directory and subdirectories (default: current repo root)",
    )
    parser.add_argument("--server", help="explicit OpenCode V2 server URL (default: shared service)")
    parser.add_argument("--dry-run", action="store_true", help="list selected sessions without deleting")
    parser.add_argument("--yes", action="store_true", help="skip confirmation (required in non-interactive use)")
    parser.add_argument("--db", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.db is not None:
        parser.error("--db is no longer supported; V2 cleanup uses the service API, not SQLite")
    return args


def main() -> int:
    args = parse_args()
    directory = None if args.all else (
        args.directory.expanduser().resolve() if args.directory else default_directory()
    )
    api = OpenCodeAPI(args.server)
    try:
        sessions = selected_sessions(api.sessions(), directory)
        display(sessions, directory)
        if not sessions or args.dry_run:
            return 0
        if not args.yes:
            if not sys.stdin.isatty():
                print("error: refusing to delete without --yes in a non-interactive shell", file=sys.stderr)
                return 1
            try:
                answer = input(f"Delete these {len(sessions)} session(s) and their data? [y/N] ")
            except (EOFError, KeyboardInterrupt):
                print("\nAborted; nothing was deleted.")
                return 0
            if answer.strip().lower() not in {"y", "yes"}:
                print("Aborted; nothing was deleted.")
                return 0

        deleted = delete_sessions(api, sessions)
        after = api.sessions()
        expected_ids = {session["id"] for session in sessions}
        scope_ids = {session["id"] for session in selected_sessions(after, directory)}
        remaining = [session for session in after if session["id"] in expected_ids | scope_ids]
        if remaining:
            display(remaining, directory)
            raise OpenCodeError(
                f"Cleanup incomplete: {len(remaining)} session(s) remain in scope "
                "(possibly created during cleanup); review and retry"
            )
        print(f"Deleted {deleted} session(s). Verified no sessions remain in scope.")
        return 0
    except (OpenCodeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
