from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ci/scripts/opencode_clear.py"
SPEC = importlib.util.spec_from_file_location("opencode_clear", SCRIPT)
clear = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clear)


def session(session_id: str, directory: str = "/repo", parent: str | None = None) -> dict:
    result = {
        "id": session_id, "title": session_id,
        "location": {"directory": directory}, "time": {"created": 1000},
    }
    if parent:
        result["parentID"] = parent
    return result


def response(data: list[dict], cursor: str | None = None) -> dict:
    return {"data": data, "cursor": {"next": cursor}}


class OpenCodeClearTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = [
            session("root"), session("child", "/elsewhere", "root"),
            session("grandchild", "/elsewhere/sub", "child"),
            session("independent", "/repo/sub"), session("other", "/other"),
            session("similar-prefix", "/repo-other"),
        ]

    def run_main(self, *args: str, pages: list[dict] | None = None):
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            patch.object(clear.OpenCodeAPI, "sessions", side_effect=pages or [self.sessions]),
            patch.object(clear.OpenCodeAPI, "remove") as remove,
            patch.object(sys.stdin, "isatty", return_value=False),
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors),
        ):
            status = clear.main()
        return status, output.getvalue(), errors.getvalue(), remove

    def test_dry_run_does_not_delete(self) -> None:
        status, output, errors, remove = self.run_main("--directory", "/repo", "--dry-run", "--yes")
        self.assertEqual(status, 0, errors)
        self.assertIn("4 session(s)", output)
        remove.assert_not_called()

    def test_scope_includes_subdirectories_and_moved_children_not_similar_prefix(self) -> None:
        selected = clear.selected_sessions(self.sessions, Path("/repo"))
        self.assertEqual({s["id"] for s in selected}, {"root", "child", "grandchild", "independent"})

    def test_all_includes_every_directory(self) -> None:
        self.assertEqual(clear.selected_sessions(self.sessions, None), self.sessions)

    def test_delete_roots_only_then_verify(self) -> None:
        status, output, errors, remove = self.run_main(
            "--directory", "/repo", "--yes", pages=[self.sessions, self.sessions[-2:]],
        )
        self.assertEqual(status, 0, errors)
        self.assertEqual([c.args[0] for c in remove.call_args_list], ["root", "independent"])
        self.assertIn("Deleted 4 session(s). Verified", output)

    def test_non_interactive_delete_requires_confirmation_flag(self) -> None:
        status, _, errors, remove = self.run_main("--directory", "/repo")
        self.assertEqual(status, 1)
        self.assertIn("without --yes", errors)
        remove.assert_not_called()

    def test_declined_confirmation_does_not_delete(self) -> None:
        with (
            patch.object(sys, "argv", [str(SCRIPT), "--directory", "/repo"]),
            patch.object(clear.OpenCodeAPI, "sessions", return_value=self.sessions),
            patch.object(clear.OpenCodeAPI, "remove") as remove,
            patch.object(sys.stdin, "isatty", return_value=True),
            patch("builtins.input", return_value="no"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(clear.main(), 0)
        remove.assert_not_called()

    def test_remaining_sessions_fail_verification(self) -> None:
        status, output, errors, _ = self.run_main(
            "--directory", "/repo", "--yes", pages=[self.sessions, [session("new")]],
        )
        self.assertEqual(status, 1)
        self.assertIn("Cleanup incomplete", errors)
        self.assertNotIn("Verified no sessions", output)

    def test_empty_scope_never_deletes(self) -> None:
        status, output, errors, remove = self.run_main("--directory", "/missing", "--yes")
        self.assertEqual(status, 0, errors)
        self.assertIn("No sessions found", output)
        remove.assert_not_called()

    def test_verification_checks_moved_children_even_after_parent_is_gone(self) -> None:
        status, _, errors, _ = self.run_main(
            "--directory", "/repo", "--yes", pages=[self.sessions, [self.sessions[1]]],
        )
        self.assertEqual(status, 1)
        self.assertIn("Cleanup incomplete", errors)

    def test_default_directory_uses_git_root(self) -> None:
        result = subprocess.CompletedProcess([], 0, "/repo\n", "")
        with patch.object(clear.subprocess, "run", return_value=result):
            self.assertEqual(clear.default_directory(), Path("/repo"))

    def test_default_directory_falls_back_to_cwd(self) -> None:
        with patch.object(clear.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(clear.default_directory(), Path.cwd().resolve())

    def test_old_db_argument_is_rejected(self) -> None:
        with (
            patch.object(sys, "argv", [str(SCRIPT), "--db", "/old/opencode.db"]),
            contextlib.redirect_stderr(io.StringIO()) as errors,
            self.assertRaises(SystemExit),
        ):
            clear.parse_args()
        self.assertIn("--db is no longer supported", errors.getvalue())

    def test_scope_arguments_are_mutually_exclusive(self) -> None:
        with (
            patch.object(sys, "argv", [str(SCRIPT), "--all", "--directory", "/repo"]),
            contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit),
        ):
            clear.parse_args()

    def test_pagination_includes_all_pages_and_deduplicates(self) -> None:
        api = clear.OpenCodeAPI()
        with patch.object(api, "request", side_effect=[
            response([session("one")], "a+b/&"), response([session("one"), session("two")]),
        ]) as request:
            self.assertEqual([s["id"] for s in api.sessions()], ["one", "two"])
        self.assertEqual(request.call_args_list[0].args, ("get", "/api/session?limit=100"))
        self.assertEqual(request.call_args_list[1].args, ("get", "/api/session?cursor=a%2Bb%2F%26"))

    def test_repeated_cursor_is_rejected(self) -> None:
        api = clear.OpenCodeAPI()
        with (
            patch.object(api, "request", return_value=response([], "same")),
            self.assertRaises(clear.OpenCodeError),
        ):
            api.sessions()

    def test_malformed_api_response_is_rejected(self) -> None:
        invalid_records = [
            {"id": "one"}, session(""), session("one", "relative/path"),
            {**session("one"), "parentID": {}}, {**session("one"), "time": None},
        ]
        for page in [[], {}, {"data": []}, response([], ""), *[response([s]) for s in invalid_records]]:
            with self.subTest(page=page):
                api = clear.OpenCodeAPI()
                with patch.object(api, "request", return_value=page), self.assertRaises(clear.OpenCodeError):
                    api.sessions()

    def test_cycle_is_rejected_before_any_deletes(self) -> None:
        api = clear.OpenCodeAPI()
        sessions = [session("root"), session("a", parent="b"), session("b", parent="a")]
        with patch.object(api, "remove") as remove, self.assertRaises(clear.OpenCodeError):
            clear.delete_sessions(api, sessions)
        remove.assert_not_called()

    def test_cli_request_preserves_server_and_uses_empty_delete_response(self) -> None:
        result = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(clear.subprocess, "run", return_value=result) as run:
            clear.OpenCodeAPI("http://localhost:4096").remove("ses/one")
        self.assertEqual(run.call_args.args[0], [
            "opencode", "api", "delete", "/api/session/ses%2Fone", "--server", "http://localhost:4096",
        ])

    def test_cli_failure_does_not_expose_raw_output(self) -> None:
        result = subprocess.CompletedProcess([], 1, "private response", "secret token")
        with patch.object(clear.subprocess, "run", return_value=result), self.assertRaises(clear.OpenCodeError) as error:
            clear.OpenCodeAPI().sessions()
        self.assertNotIn("private response", str(error.exception))
        self.assertNotIn("secret token", str(error.exception))

    def test_missing_cli_and_timeout_are_reported(self) -> None:
        for error in [FileNotFoundError(), subprocess.TimeoutExpired("opencode", 60)]:
            with patch.object(clear.subprocess, "run", side_effect=error), self.assertRaises(clear.OpenCodeError):
                clear.OpenCodeAPI().sessions()

    def test_invalid_json_is_reported(self) -> None:
        result = subprocess.CompletedProcess([], 0, "not JSON", "")
        with patch.object(clear.subprocess, "run", return_value=result), self.assertRaises(clear.OpenCodeError):
            clear.OpenCodeAPI().sessions()

    def test_delete_failure_is_reported_without_success_message(self) -> None:
        with (
            patch.object(sys, "argv", [str(SCRIPT), "--directory", "/repo", "--yes"]),
            patch.object(clear.OpenCodeAPI, "sessions", return_value=self.sessions),
            patch.object(clear.OpenCodeAPI, "remove", side_effect=clear.OpenCodeError("request failed")),
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(clear.main(), 1)
        self.assertNotIn("Deleted", output.getvalue())
        self.assertIn("request failed", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
