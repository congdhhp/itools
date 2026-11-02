"""Resume-safe issue import: all parents first, then links."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from itools.core.http import JiraClient
from itools.jira.models import PlanningIssue, ValidationReport
from itools.jira.service import (
    JiraMetadata, canonical_links, discover_metadata, issue_payload, PreflightError,
)


class ImportStateError(ValueError):
    pass


@dataclass
class ImportState:
    project: str
    jira_url: str
    fingerprints: dict[str, str] = field(default_factory=dict)
    created: dict[str, str] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def state_file_for(source: Path) -> Path:
    return source.with_name(source.name + ".itools-state.json")


def _fingerprint(issue: PlanningIssue) -> str:
    data = json.dumps(asdict(issue), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def load_state(path: Path, project: str, url: str, resume: bool) -> ImportState:
    if path.exists() and not resume:
        raise ImportStateError(
            f"Import state already exists: {path}. Use --resume, or inspect it before starting anew."
        )
    if resume and not path.exists():
        raise ImportStateError(f"No previous import state: {path}")
    if not path.exists():
        return ImportState(project=project, jira_url=url)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = ImportState(
            project=raw["project"], jira_url=raw["jira_url"],
            fingerprints=raw.get("fingerprints", {}), created=raw.get("created", {}),
            links=raw.get("links", []),
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ImportStateError(f"Invalid import state file {path}: {exc}") from exc
    if result.project != project or result.jira_url != url:
        raise ImportStateError("State belongs to a different Jira instance or project")
    return result


def save_state(path: Path, state: ImportState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(state.as_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temp.chmod(0o600)
    except OSError:
        pass
    temp.replace(path)


@dataclass
class ImportSummary:
    project: str
    source: str
    state_file: str
    created: dict[str, str]
    issue_created_now: int
    issue_reused: int
    links_created_now: int
    links_reused: int
    dry_run: bool = False

    def as_dict(self):
        return asdict(self)


def run_import(
    client: JiraClient,
    report: ValidationReport,
    project: str,
    *,
    dry_run: bool = False,
    resume: bool = False,
    state_path: Path | None = None,
    on_progress=None,
) -> ImportSummary:
    if not report.valid:
        raise PreflightError("Workbook failed validation")
    path = state_path or state_file_for(report.source)
    state = load_state(path, project, client.settings.url, resume)
    metadata: JiraMetadata = discover_metadata(client, project, report)

    issues = {i.wbs: i for i in report.issues}
    for wbs, key in state.created.items():
        if wbs not in issues:
            raise ImportStateError(f"Previously created WBS {wbs} is missing from Excel")
        if state.fingerprints.get(wbs) != _fingerprint(issues[wbs]):
            raise ImportStateError(
                f"WBS {wbs} was already imported but its planning fields changed; "
                "import cannot update existing issues (future 'sync' command)"
            )
        try:
            client.get_issue(key)
        except Exception as exc:
            raise ImportStateError(f"Previously created {wbs} → {key} is unavailable: {exc}") from exc

    if dry_run:
        # Preview remote preflight without writing to Jira or a state file.
        return ImportSummary(
            project, str(report.source), str(path), dict(state.created),
            len(report.issues) - len(state.created), len(state.created),
            sum(len(i.linked_issues) for i in report.issues), 0, True,
        )

    created_now = 0
    reused = 0
    for issue in report.issues:  # already sorted Epic -> Task -> Sub-task
        if issue.wbs in state.created:
            reused += 1
            continue
        fields = issue_payload(issue, metadata, state.created)
        jira_key = client.create_issue(fields)
        state.created[issue.wbs] = jira_key
        state.fingerprints[issue.wbs] = _fingerprint(issue)
        save_state(path, state)
        created_now += 1
        if on_progress:
            on_progress(f"{issue.wbs}: {jira_key} ({issue.issue_type})")

    linked_now = 0
    linked_reused = 0
    for link_type, outward, inward in canonical_links(report, metadata, state.created):
        marker = f"{link_type.casefold()}|{outward}|{inward}"
        if marker in state.links:
            linked_reused += 1
            continue
        # Avoid duplicate links after a crash between Jira write and local checkpoint.
        if not client.has_link(link_type, outward, inward):
            client.create_link(link_type, outward, inward)
            linked_now += 1
            if on_progress:
                on_progress(f"Link: {outward} --{link_type}--> {inward}")
        else:
            linked_reused += 1
        state.links.append(marker)
        save_state(path, state)

    return ImportSummary(
        project, str(report.source), str(path), dict(state.created),
        created_now, reused, linked_now, linked_reused,
    )


def write_report(path: Path, summary: ImportSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
