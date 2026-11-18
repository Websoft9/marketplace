from __future__ import annotations

import importlib.util
import subprocess
import sys

import click


@click.group("tools")
def tools_command():
    """Prepare supporting tools for the CLI."""


@tools_command.command("install-browser")
def install_browser_command():
    """Install Playwright's Chromium browser for this Python environment."""
    if importlib.util.find_spec("playwright") is None:
        raise click.ClickException("Playwright is missing; run 'make install' first.")
    click.echo("Installing Playwright Chromium browser...", err=True)
    try:
        result = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
    except OSError:
        raise click.ClickException("Could not start Playwright; reinstall dependencies with 'make install'.") from None
    if result.returncode:
        raise click.ClickException("Playwright Chromium installation failed; see the installer output above.")
    click.echo("Playwright Chromium is ready.")
