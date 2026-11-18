from __future__ import annotations

from pathlib import Path

import click

from . import __version__, env_file
from .commands.catalog import catalog_command
from .commands.list_apps import list_command
from .commands.versions import versions_command
from .commands.offer import offer_command
from .commands.offer_codes import offer_codes_command
from .commands.store import store_command
from .commands.asset import asset_command
from .commands.tools import tools_command


class SectionedGroup(click.Group):
    """Render commands under prominent section headings."""

    SECTION_ORDER = ("Catalog", "Marketplace", "Tools", "Help")

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        sections: dict[str, list[tuple[str, click.Command]]] = {}
        for name in self.list_commands(ctx):
            command = self.get_command(ctx, name)
            if command is None or command.hidden:
                continue
            section = getattr(command, "section", "Commands")
            sections.setdefault(section, []).append((name, command))
        ordered = [name for name in self.SECTION_ORDER if name in sections]
        ordered += [name for name in sections if name not in ordered]
        for section in ordered:
            with formatter.section(section):
                formatter.write_dl([
                    (name, command.get_short_help_str(limit=55))
                    for name, command in sections[section]
                ])


@click.group(
    cls=SectionedGroup,
    context_settings={"help_option_names": ["-h", "--help"], "max_content_width": 100},
    epilog="For details, run 'mk <command> --help' (for example, 'mk store --help').",
)
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


catalog_command.section = "Catalog"
asset_command.section = "Catalog"
tools_command.section = "Tools"
for _command in (list_command, versions_command, offer_command, offer_codes_command, store_command):
    _command.section = "Marketplace"
help_command.section = "Help"

cli.add_command(list_command, "list")
cli.add_command(versions_command, "versions")
cli.add_command(offer_command, "offer")
cli.add_command(offer_codes_command, "offer-codes")
cli.add_command(store_command, "store")
cli.add_command(catalog_command, "catalog")
cli.add_command(asset_command, "asset")
cli.add_command(tools_command, "tools")
cli.add_command(help_command, "help")


def main() -> None:
    cli(prog_name="mk")
