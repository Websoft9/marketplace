"""Click test-runner helpers compatible with click 8.1 and 8.2+.

Click 8.1 takes ``mix_stderr``; click 8.2 replaced it with independent
``output``, ``stdout`` and ``stderr`` streams. Both modes expose separated
``stdout``/``stderr`` on the result, which is what the tests assert on.
"""

from __future__ import annotations

from click.testing import CliRunner


def separated_runner(**kwargs: object) -> CliRunner:
    try:
        return CliRunner(mix_stderr=False, **kwargs)  # type: ignore[call-arg]
    except TypeError:
        return CliRunner(**kwargs)
