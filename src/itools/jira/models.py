"""Parse and validate the human-friendly 11-column WBS workbook."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook

HEADERS = (
    "WBS", "Summary", "Issue Type", "Assignee", "Priority",
    "Original Estimate", "Due Date", "Linked Issues",
    "Component/s", "Labels", "Description",
)
WBS_RE = re.compile(r"^[1-9]\d*(?:\.[1-9]\d*){0,2}$")
JIRA_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-[1-9]\d*$")
TIME_RE = re.compile(r"^(?:\s*\d+\s*[wdhm]\s*)+$", re.I)
ISSUE_TYPES = {"epic": "Epic", "task": "Task", "sub-task": "Sub-task", "subtask": "Sub-task"}


@dataclass(frozen=True)
class LinkSpec:
    relationship: str
    target: str


@dataclass(frozen=True)
class PlanningIssue:
    row: int
    wbs: str
    issue_type: str
    summary: str
    assignee: str = ""
    priority: str = ""
    estimate: str = ""
    due_date: str = ""
    linked_issues: tuple[LinkSpec, ...] = ()
    components: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    description: str = ""

    @property
    def depth(self) -> int:
        return self.wbs.count(".") + 1

    @property
    def parent_wbs(self) -> str | None:
        return self.wbs.rpartition(".")[0] if self.depth > 1 else None

    @property
    def sort_key(self) -> tuple[int, ...]:
        return tuple(int(part) for part in self.wbs.split("."))


@dataclass
class ValidationReport:
    source: Path
    issues: list[PlanningIssue] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def counts(self) -> dict[str, int]:
        return {kind: sum(i.issue_type == kind for i in self.issues)
                for kind in ("Epic", "Task", "Sub-task")}

    def as_dict(self) -> dict:
        return {
            "source": str(self.source), "valid": self.valid, "counts": self.counts(),
            "issues": len(self.issues),
            "links": sum(len(i.linked_issues) for i in self.issues),
            "errors": self.errors, "warnings": self.warnings,
        }


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _header_name(value) -> str:
    text = re.sub(r"\s+", " ", _text(value).lower()).strip()
    if text.startswith("original estimate"):
        return "original estimate"
    if text in ("components", "component/s", "component"):
        return "component/s"
    return text


def _split(value) -> tuple[str, ...]:
    return tuple(part.strip() for part in _text(value).split(",") if part.strip())


def _estimate(value) -> str:
    if value is None or _text(value) == "":
        return ""
    if isinstance(value, bool):
        raise ValueError("Original Estimate must be hours or a Jira duration")
    if isinstance(value, (float, int, Decimal)):
        hours = Decimal(str(value))
        if hours < 0:
            raise ValueError("Original Estimate cannot be negative")
        minutes = hours * 60
        if minutes != minutes.to_integral_value():
            raise ValueError("Original Estimate supports minute precision")
        total = int(minutes)
        return " ".join(part for part in (
            f"{total // 60}h" if total // 60 else "",
            f"{total % 60}m" if total % 60 else "",
        ) if part)
    text = _text(value)
    try:
        return _estimate(Decimal(text))
    except InvalidOperation:
        pass
    if not TIME_RE.fullmatch(text):
        raise ValueError("Original Estimate must be hours (e.g. 8, 1.5) or Jira duration (2d 4h)")
    return text


def _date(value) -> str:
    if value is None or _text(value) == "":
        return ""
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(_text(value), fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("Due Date must be an Excel date, YYYY-MM-DD or DD/MM/YYYY")


def _links(value, row: int, errors: list[str]) -> tuple[LinkSpec, ...]:
    if not _text(value):
        return ()
    links = []
    for line in _text(value).splitlines():
        if not line.strip():
            continue
        relation, marker, targets = line.partition(":")
        if not marker or not relation.strip() or not targets.strip():
            errors.append(f"Row {row}: invalid Linked Issues line {line!r}; expected 'blocks: 1.2, ABC-123'")
            continue
        for target in targets.split(","):
            target = target.strip()
            if not (WBS_RE.fullmatch(target) or JIRA_KEY_RE.fullmatch(target)):
                errors.append(f"Row {row}: invalid Linked Issues reference {target!r}")
            else:
                links.append(LinkSpec(relation.strip(), target))
    return tuple(links)


def read_workbook(path: Path | str) -> ValidationReport:
    source = Path(path)
    report = ValidationReport(source)
    if not source.is_file():
        report.errors.append(f"Excel file not found: {source}")
        return report
    try:
        book = load_workbook(source, read_only=True, data_only=True)
    except Exception as exc:
        report.errors.append(f"Cannot read Excel workbook: {exc}")
        return report
    try:
        sheets = ([book["01_Ke_hoach"]] if "01_Ke_hoach" in book.sheetnames else
                  [book[name] for name in book.sheetnames])
        sheet = None
        header = None
        header_row = 0
        for candidate in sheets:
            for cells in candidate.iter_rows(max_row=min(candidate.max_row, 35), values_only=False):
                names = {_header_name(cell.value): idx for idx, cell in enumerate(cells)
                         if _header_name(cell.value)}
                if {"wbs", "summary", "issue type"} <= names.keys():
                    sheet, header, header_row = candidate, names, cells[0].row
                    break
            if sheet is not None:
                break
        if sheet is None:
            report.errors.append("Missing header row containing WBS, Summary and Issue Type")
            return report
        for row, cells in enumerate(sheet.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
            def value(name):
                index = header.get(name.lower())
                return cells[index] if index is not None and index < len(cells) else None
            wbs, summary, kind = _text(value("WBS")), _text(value("Summary")), _text(value("Issue Type"))
            if not wbs and not summary and not kind:
                continue
            if not wbs or not WBS_RE.fullmatch(wbs):
                report.errors.append(f"Row {row}: invalid WBS {wbs!r}; use text 1, 1.1 or 1.1.1")
                continue
            canon = ISSUE_TYPES.get(kind.casefold())
            if canon is None:
                report.errors.append(f"Row {row} ({wbs}): unknown Issue Type {kind!r}")
                continue
            if not summary:
                report.errors.append(f"Row {row} ({wbs}): Summary is required")
                continue
            try:
                estimate = _estimate(value("Original Estimate"))
                due = _date(value("Due Date"))
            except ValueError as exc:
                report.errors.append(f"Row {row} ({wbs}): {exc}")
                continue
            report.issues.append(PlanningIssue(
                row=row, wbs=wbs, issue_type=canon, summary=summary,
                assignee=_text(value("Assignee")), priority=_text(value("Priority")),
                estimate=estimate, due_date=due,
                linked_issues=_links(value("Linked Issues"), row, report.errors),
                components=_split(value("Component/s")), labels=_split(value("Labels")),
                description=_text(value("Description")),
            ))
        _check_hierarchy(report)
        return report
    finally:
        book.close()


def _check_hierarchy(report: ValidationReport) -> None:
    by_wbs: dict[str, PlanningIssue] = {}
    expected = {1: "Epic", 2: "Task", 3: "Sub-task"}
    for issue in report.issues:
        if issue.wbs in by_wbs:
            report.errors.append(f"Row {issue.row}: duplicate WBS {issue.wbs}")
        by_wbs[issue.wbs] = issue
        if issue.issue_type != expected[issue.depth]:
            report.errors.append(
                f"Row {issue.row} ({issue.wbs}): expected {expected[issue.depth]}, "
                f"got {issue.issue_type}"
            )
    for issue in report.issues:
        if issue.parent_wbs and issue.parent_wbs not in by_wbs:
            report.errors.append(f"Row {issue.row} ({issue.wbs}): missing parent {issue.parent_wbs}")
        for link in issue.linked_issues:
            if link.target == issue.wbs:
                report.errors.append(f"Row {issue.row} ({issue.wbs}): cannot link an issue to itself")
            if WBS_RE.fullmatch(link.target) and link.target not in by_wbs:
                report.errors.append(f"Row {issue.row} ({issue.wbs}): unknown linked WBS {link.target}")
    if not report.issues:
        report.errors.append("Workbook contains no planning issues")
    report.issues.sort(key=lambda issue: (issue.depth, issue.sort_key))
