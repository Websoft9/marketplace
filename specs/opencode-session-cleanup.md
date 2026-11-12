# OpenCode session cleanup

`make opencode-clear` lists sessions and asks before deleting them through the
OpenCode V2 service API. It requires the `opencode` executable on `PATH` and
uses `opencode api` for service discovery and authentication. It never opens or
modifies SQLite directly; the former `--db` option is no longer supported.

```sh
# Preview sessions in the current repository, without deleting anything.
make opencode-clear ARGS="--dry-run"

# Delete after interactive confirmation.
make opencode-clear

# Delete without interactive confirmation.
make opencode-clear ARGS="--yes"

# Preview a specific directory and its subdirectories.
make opencode-clear ARGS="--directory /path/to/repo --dry-run"

# Preview every directory on the selected service, not just this repository.
make opencode-clear ARGS="--all --dry-run"
```

## Scope and safety

- The default scope is the current Git repository root and its subdirectories;
  outside Git, it is the current directory. Path-component matching excludes
  similarly named sibling directories. Other worktrees outside this path are
  not included by default.
- `--directory PATH` replaces the default scope; `--all` selects all directories.
  These options are mutually exclusive.
- V2 parent deletion also deletes child sessions. All descendants, including
  children moved outside the selected path, are included in the preview and
  confirmation. Only selected roots are sent to DELETE, avoiding duplicate
  deletes of children.
- Every session-list page is read before selecting sessions. Archived and child
  sessions are not intentionally filtered out.
- `--dry-run` never deletes, even when combined with `--yes`. Non-interactive
  deletion requires `--yes`.
- Deletion is irreversible and can include the session currently being used.
  Run cleanup from a separate terminal when ready to discard those sessions.
- After deleting, the script reads all pages again and fails if any sessions
  remain in scope. Newly created sessions during cleanup can cause this check
  to fail; inspect them before retrying. Requests are not transactional, so a
  failure can occur after some sessions have already been deleted.
- For an explicit server, pass `--server URL` to use the same server for listing,
  deletion, and verification. Keep authentication in OpenCode's configuration
  or environment; do not put credentials in the URL.

## Validation

```sh
python3 -m unittest discover -s ci/tests -p 'test_opencode_clear.py'
```

Tests mock the service API / CLI and never delete real sessions. A live
`make opencode-clear ARGS="--dry-run"` checks listing integration only.

References: [V2 API](https://opencode.ai/docs/api),
[V1 migration](https://opencode.ai/docs/migrate-v1),
[CLI](https://opencode.ai/docs/cli).
