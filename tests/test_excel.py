from __future__ import annotations

from datetime import date
from openpyxl import Workbook, load_workbook
from typer.testing import CliRunner

from itools.cli import app
from itools.jira.models import read_workbook
from itools.jira.template import generate_template


def test_template_example_has_11_columns_and_outline(tmp_path):
    path = tmp_path / "plan.xlsx"
    generate_template(path, example=True)
    report = read_workbook(path)
    assert report.valid, report.errors
    assert report.counts() == {"Epic": 1, "Task": 2, "Sub-task": 3}
    assert report.issues[0].wbs == "1"
    assert next(i for i in report.issues if i.wbs == "1.1.1").estimate == "8h"
    assert sum(len(issue.linked_issues) for issue in report.issues) == 4
    book = load_workbook(path)
    ws = book["01_Ke_hoach"]
    assert [ws.cell(8, c).value for c in range(1, 4)] == [
        "WBS", "Summary", "Issue Type"
    ]
    assert ws.row_dimensions[10].outlineLevel == 1
    assert ws.row_dimensions[11].outlineLevel == 2
    assert ws.freeze_panes == "D9"
    assert ws["B9"].border.bottom.style is not None
    book.close()


def test_blank_template_not_importable(tmp_path):
    path = tmp_path / "blank.xlsx"
    generate_template(path)
    report = read_workbook(path)
    assert not report.valid
    assert any("no planning issues" in err for err in report.errors)


def _simple_book(tmp_path, rows):
    path = tmp_path / "invalid.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["WBS", "Summary", "Issue Type", "Linked Issues"])
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def test_rejects_missing_parent_and_unknown_link(tmp_path):
    path = _simple_book(tmp_path, [
        ["1.1", "Orphan", "Task", "blocks: 2.1"],
    ])
    report = read_workbook(path)
    assert not report.valid
    assert any("missing parent" in err for err in report.errors)
    assert any("unknown linked WBS" in err for err in report.errors)


def test_rejects_duplicate_wbs_and_level_mismatch(tmp_path):
    path = _simple_book(tmp_path, [
        ["1", "Root", "Task", None],
        ["1", "Duplicated", "Epic", None],
    ])
    report = read_workbook(path)
    assert any("duplicate WBS" in err for err in report.errors)
    assert any("expected Epic" in err for err in report.errors)


def test_date_estimate_and_multiline_links(tmp_path):
    path = tmp_path / "date.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["WBS", "Summary", "Issue Type", "Original Estimate", "Due Date", "Linked Issues"])
    ws.append(["1", "Epic", "Epic", None, None, None])
    ws.append(["1.1", "Task", "Task", 1.5, date(2026, 11, 20),
               "blocks: 1.2, DEV-123\nis blocked by: DEV-124"])
    ws.append(["1.2", "Task", "Task", None, None, None])
    wb.save(path)
    report = read_workbook(path)
    assert report.valid, report.errors
    task = next(issue for issue in report.issues if issue.wbs == "1.1")
    assert task.estimate == "1h 30m"
    assert task.due_date == "2026-11-20"
    assert len(task.linked_issues) == 3


def test_cli_help_and_local_validation(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["jira", "issue", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "import" in result.stdout
    path = tmp_path / "example.xlsx"
    generate_template(path, example=True)
    result = runner.invoke(app, ["jira", "issue", "validate", str(path), "--format", "json"])
    assert result.exit_code == 0, result.stdout
    assert '"valid": true' in result.stdout


def test_template_does_not_overwrite(tmp_path):
    path = tmp_path / "plan.xlsx"
    generate_template(path)
    try:
        generate_template(path)
        assert False, "Expected FileExistsError"
    except FileExistsError:
        pass
