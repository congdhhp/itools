"""itools entry point: service -> resource -> action."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from itools import __version__
from itools.core.config import (
    ConfigurationError, config_path, initialize_config, jira_settings, load_config,
)
from itools.core.http import JiraAPIError, JiraClient
from itools.jira.importer import ImportStateError, run_import, write_report
from itools.jira.models import read_workbook
from itools.jira.service import PreflightError
from itools.jira.template import generate_template

app = typer.Typer(no_args_is_help=True, help="Internal developer service tools")
jira_app = typer.Typer(no_args_is_help=True, help="Jira Server/Data Center operations")
issue_app = typer.Typer(no_args_is_help=True, help="Jira issue operations")
field_app = typer.Typer(no_args_is_help=True, help="Jira field metadata")
link_type_app = typer.Typer(no_args_is_help=True, help="Jira link type metadata")
config_app = typer.Typer(no_args_is_help=True, help="Shared profiles and configuration")
app.add_typer(jira_app, name="jira")
app.add_typer(config_app, name="config")
jira_app.add_typer(issue_app, name="issue")
jira_app.add_typer(field_app, name="field")
jira_app.add_typer(link_type_app, name="link-type")
console = Console()


@app.callback()
def main(
    ctx: typer.Context,
    profile: str = typer.Option("internal", "--profile", help="Configured connection profile"),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml"),
    version: bool = typer.Option(False, "--version", is_eager=True, help="Print version"),
):
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    ctx.ensure_object(dict)
    ctx.obj.update(profile=profile, config=config)


def _error(exc: Exception):
    console.print(f"[red]Error:[/red] {exc}", highlight=False)
    raise typer.Exit(1) from exc


def _client(ctx: typer.Context) -> JiraClient:
    return JiraClient(jira_settings(ctx.obj["profile"], ctx.obj["config"]))


def _print_json(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _check_local(file: Path):
    report = read_workbook(file)
    if report.errors:
        for message in report.errors:
            console.print(f"[red]✗[/red] {message}", highlight=False)
        raise typer.Exit(1)
    return report


@config_app.command("init")
def config_init(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
):
    """Create a safe sample config with environment-based authentication."""
    try:
        path = initialize_config(ctx.obj["config"], force=force)
        console.print(f"Configuration created: {path}")
    except (ConfigurationError, OSError) as exc:
        _error(exc)


@config_app.command("show")
def config_show(ctx: typer.Context):
    """Show non-secret config; environment variable contents are never printed."""
    try:
        path = config_path(ctx.obj["config"])
        config = load_config(ctx.obj["config"])
        # Redact any accidental inline secrets from the TOML config.
        def redact(value):
            if isinstance(value, dict):
                return {
                    key: ("***" if any(term in key.lower() for term in
                            ("password", "secret", "token", "credential")) and
                            not key.lower().endswith("_env") else redact(item))
                    for key, item in value.items()
                }
            return value
        _print_json({"file": str(path), "profile": ctx.obj["profile"], "config": redact(config)})
    except ConfigurationError as exc:
        _error(exc)


@jira_app.command("ping")
def jira_ping(ctx: typer.Context):
    """Test Jira authentication and REST connectivity."""
    try:
        with _client(ctx) as client:
            data = client.server_info()
        console.print(f"[green]Connected[/green] · {data.get('serverTitle', 'Jira')} · "
                      f"{data.get('version', 'unknown version')}")
    except (ConfigurationError, JiraAPIError) as exc:
        _error(exc)


@jira_app.command("doctor")
def jira_doctor(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "-p", "--project", help="Optional project key"),
):
    """Check API connectivity and discover Jira Epic fields."""
    try:
        with _client(ctx) as client:
            info = client.server_info()
            fields = client.fields()
            link_types = client.link_types()
            project_data = client.project(project) if project else {}
        epic = [f"{f.get('name')}: {f.get('id')}" for f in fields
                if f.get("name", "").casefold() in ("epic link", "epic name")]
        table = Table(title="Jira diagnostics")
        table.add_column("Check")
        table.add_column("Result")
        for key, value in (
            ("Server", info.get("serverTitle", "Jira")),
            ("Version", info.get("version", "unknown")),
            ("Project", project_data.get("name", "not selected")),
            ("Epic fields", ", ".join(epic) or "Not found"),
            ("Issue link types", str(len(link_types))),
        ):
            table.add_row(str(key), str(value))
        console.print(table)
        if not epic:
            console.print("[yellow]Warning: Jira Software Epic fields were not discovered.[/yellow]")
    except (ConfigurationError, JiraAPIError) as exc:
        _error(exc)


@field_app.command("list")
def jira_fields(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", help="Filter by field name"),
    format_: str = typer.Option("table", "--format", help="table or json"),
):
    """List Jira system and custom fields."""
    try:
        with _client(ctx) as client:
            fields = client.fields()
        if name:
            fields = [f for f in fields if name.casefold() in f.get("name", "").casefold()]
        if format_ == "json":
            _print_json(fields)
        elif format_ == "table":
            table = Table("ID", "Name", "Custom", title="Jira fields")
            for f in fields:
                table.add_row(f.get("id", ""), f.get("name", ""), str(f.get("custom", False)))
            console.print(table)
        else:
            raise ValueError("--format must be table or json")
    except (ConfigurationError, JiraAPIError, ValueError) as exc:
        _error(exc)


@link_type_app.command("list")
def jira_link_types(
    ctx: typer.Context,
    format_: str = typer.Option("table", "--format", help="table or json"),
):
    """List link relationship labels in both directions."""
    try:
        with _client(ctx) as client:
            types = client.link_types()
        if format_ == "json":
            _print_json(types)
        elif format_ == "table":
            table = Table("Type", "Outward", "Inward", title="Jira issue link relationships")
            for entry in types:
                table.add_row(entry.get("name", ""), entry.get("outward", ""), entry.get("inward", ""))
            console.print(table)
        else:
            raise ValueError("--format must be table or json")
    except (ConfigurationError, JiraAPIError, ValueError) as exc:
        _error(exc)


@issue_app.command("template")
def issue_template(
    output: Path = typer.Option(Path("jira-planning.xlsx"), "-o", "--output"),
    example: bool = typer.Option(False, "--example", help="Include sample Epic / Task / Sub-task"),
):
    """Create the styled WBS planning Excel workbook."""
    try:
        saved = generate_template(output, example=example)
        console.print(f"[green]Template created:[/green] {saved}")
    except (OSError, FileExistsError) as exc:
        _error(exc)


@issue_app.command("validate")
def issue_validate(
    file: Path = typer.Argument(..., help="11-column WBS .xlsx file"),
    format_: str = typer.Option("table", "--format", help="table or json"),
):
    """Validate workbook and issue hierarchy locally (no Jira connection)."""
    report = read_workbook(file)
    if format_ == "json":
        _print_json(report.as_dict())
    elif format_ == "table":
        for error in report.errors:
            console.print(f"[red]✗[/red] {error}", highlight=False)
        for warning in report.warnings:
            console.print(f"[yellow]![/yellow] {warning}", highlight=False)
        counts = report.counts()
        console.print(
            f"{'Valid' if report.valid else 'Invalid'}: {len(report.issues)} issues "
            f"({counts['Epic']} Epic, {counts['Task']} Task, {counts['Sub-task']} Sub-task), "
            f"{sum(len(i.linked_issues) for i in report.issues)} links"
        )
    else:
        _error(ValueError("--format must be table or json"))
    if not report.valid:
        raise typer.Exit(1)


@issue_app.command("import")
def issue_import(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Excel WBS plan"),
    project: str = typer.Option(..., "-p", "--project", help="Jira project key"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Remote preflight; no Jira writes"),
    resume: bool = typer.Option(False, "--resume", help="Resume from saved checkpoint"),
    yes: bool = typer.Option(False, "-y", "--yes", help="Skip confirmation"),
    state: Path | None = typer.Option(None, "--state", help="Checkpoint file path"),
    output: Path | None = typer.Option(None, "-o", "--output", help="JSON import report path"),
):
    """Import WBS as Epic → Task → Sub-task, then create issue links."""
    report = _check_local(file)
    counts = report.counts()
    console.print(
        f"Plan: {counts['Epic']} Epic, {counts['Task']} Task, "
        f"{counts['Sub-task']} Sub-task; "
        f"{sum(len(i.linked_issues) for i in report.issues)} declared links"
    )
    if not dry_run and not yes:
        typer.confirm(
            f"Create issues in Jira project {project.upper()}? (No automatic rollback)",
            abort=True,
        )
    try:
        with _client(ctx) as client:
            result = run_import(
                client, report, project.upper(), dry_run=dry_run, resume=resume,
                state_path=state,
                on_progress=lambda msg: console.print(f"[green]✓[/green] {msg}", highlight=False),
            )
        if output:
            write_report(output, result)
        _print_json(result.as_dict())
        if dry_run:
            console.print("[yellow]Dry-run: no issues, links or checkpoint were written.[/yellow]")
    except (ConfigurationError, JiraAPIError, PreflightError,
            ImportStateError, OSError, KeyError, ValueError) as exc:
        console.print(
            "[yellow]If any issues were created, rerun with --resume. "
            "No automatic rollback is performed.[/yellow]"
        )
        _error(exc)


if __name__ == "__main__":
    app()
