from __future__ import annotations

from pathlib import Path

import click

from .. import app_screenshots
from ..marketplace import CATALOG_URL, REPO_ROOT


@click.group("asset")
def asset_command():
    """Produce and upload application media assets."""


@asset_command.command("screenshots")
@click.option("--apps", default=None, help="Comma-separated application IDs; omit for all applications.")
@click.option("--catalog", default=CATALOG_URL, help="Product catalog URL or local JSON file.")
@click.option("--output-dir", type=click.Path(file_okay=False, path_type=Path),
              default=REPO_ROOT / "data" / ".work" / "screenshots", show_default=True,
              help="Screenshot and report directory; defaults to the repository's work directory.")
@click.option("--no-upload", is_flag=True, help="Save local screenshots without contacting AWS.")
@click.option("--profile", default=None, help="Optional local AWS profile; Actions uses OIDC credentials.")
@click.option("--timeout", type=click.IntRange(5, 120), default=30, show_default=True,
              help="Maximum wait per browser operation, in seconds.")
@click.option("--summary", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Append a safe Markdown report, e.g. to GITHUB_STEP_SUMMARY.")
@click.pass_context
def screenshots_command(ctx, apps, catalog, output_dir, no_upload, profile, timeout, summary):
    """Capture AppStore details at 1920x1040 and overwrite offers/{appid}-installation.png."""
    if no_upload and profile:
        raise click.UsageError("--profile cannot be combined with --no-upload")
    try:
        applications = app_screenshots.select_applications(catalog, apps)
        credentials = app_screenshots.Credentials.from_environment()
        report = app_screenshots.run_screenshots(
            applications, credentials=credentials, output_dir=output_dir,
            upload=not no_upload, profile=profile, timeout=timeout,
            progress=lambda message: click.echo(message, err=True),
        )
        if summary is not None:
            with summary.open("a", encoding="utf-8") as stream:
                stream.write(app_screenshots.render_summary(report))
    except app_screenshots.ScreenshotError as error:
        raise click.ClickException(str(error)) from None
    except OSError:
        raise click.ClickException("Unable to write screenshot output or summary.") from None
    click.echo(f"{report['succeeded']} succeeded, {report['failed']} failed; report: {output_dir / 'report.json'}")
    if report["failed"] or report.get("error"):
        ctx.exit(1)
