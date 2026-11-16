from __future__ import annotations

from pathlib import Path

import click

from . import __version__, env_file
from .commands.catalog import catalog_command
from .commands.list_apps import list_command
from .commands.versions import versions_command
from .commands.offer import offer_command
from .commands.offer_codes import offer_codes_command


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--env-file", "env_file_path",
    type=click.Path(dir_okay=False, path_type=Path),
    envvar="MK_ENV_FILE", default=None, metavar="PATH",
    help="Load KEY=VALUE pairs from a local file before running a command. "
         "Existing environment variables win; values are never printed.",
)
@click.version_option(__version__, prog_name="mk")
def cli(env_file_path: Path | None) -> None:
    """Websoft9 marketplace business CLI."""
    if env_file_path is not None:
        try:
            loaded = env_file.load(env_file_path)
        except ValueError as error:
            raise click.ClickException(str(error)) from None
        if not loaded:
            click.echo(f"No new variables loaded from {env_file_path}", err=True)


@click.command("help")
@click.pass_context
def help_command(ctx: click.Context) -> None:
    """Show this help message."""
    click.echo(ctx.parent.get_help() if ctx.parent else cli.get_help(click.Context(cli, info_name="mk")))


cli.add_command(list_command, "list")
cli.add_command(versions_command, "versions")
cli.add_command(offer_command, "offer")
cli.add_command(offer_codes_command, "offer-codes")
cli.add_command(catalog_command, "catalog")
cli.add_command(help_command, "help")


def main() -> None:
    cli(prog_name="mk")
