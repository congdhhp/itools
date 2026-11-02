"""Generate a clean 11-column Jira WBS planning template (no macros)."""

from __future__ import annotations

from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import FormulaRule

COLUMNS = [
    "WBS", "Summary", "Issue Type", "Assignee", "Priority",
    "Original Estimate (h)", "Due Date", "Linked Issues",
    "Component/s", "Labels", "Description",
]
WIDTHS = [13, 49, 15, 18, 14, 21, 18, 36, 23, 24, 56]
BLUE = "203D5B"
LIGHT_BLUE = "DFEEFF"
VERY_LIGHT = "F7FAFD"
PALE_YELLOW = "FFF8DB"
GRID = "CFD8E3"
EXAMPLES = [
    ["1", "Chuẩn hóa CI/CD", "Epic", "pm01", "High", None, None, None,
     "Platform", "ci-cd", "Xây dựng quy trình CI/CD dùng chung"],
    ["1.1", "Xây dựng Jenkins Pipeline", "Task", "devops01", "High", None, None, None,
     "Jenkins", "pipeline", "Thiết kế pipeline dùng chung"],
    ["1.1.1", "Thiết kế Jenkinsfile", "Sub-task", "devops01", "Medium", 8, None,
     "blocks: 1.1.2", "Jenkins", "ci-cd", "Tạo Jenkinsfile"],
    ["1.1.2", "Tích hợp unit test", "Sub-task", "qa01", "Medium", 12, None,
     "is blocked by: 1.1.1", "Jenkins", "test", "Chạy unit test"],
    ["1.2", "Đẩy artifact lên Nexus", "Task", "devops02", "High", None, None,
     "is blocked by: 1.1", "Nexus", "artifact", "Quản lý artifact"],
    ["1.2.1", "Cấu hình Nexus repository", "Sub-task", "devops02", "Medium", 6, None,
     "relates to: 1.1.1", "Nexus", "repository", "Cấu hình repository"],
]


def generate_template(output: Path | str, *, example: bool = False) -> Path:
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "01_Ke_hoach"
    ws.sheet_properties.outlinePr.summaryBelow = False
    ws.sheet_view.showOutlineSymbols = True
    ws.freeze_panes = "D9"
    ws.sheet_view.zoomScale = 85
    ws.sheet_format.defaultRowHeight = 21
    ws.merge_cells("A1:K2")
    ws["A1"] = "JIRA  /  PROJECT PLANNING"
    ws["A1"].fill = PatternFill("solid", fgColor=BLUE)
    ws["A1"].font = Font(name="Aptos", size=17, bold=True, color="FFFFFF")
    ws["A1"].alignment = Alignment(vertical="center", indent=1)
    ws.merge_cells("A3:K3")
    ws["A3"] = "Epic → Task → Sub-task   •   Một dòng = một issue   •   WBS xác định cha/con"
    ws["A3"].font = Font(name="Aptos", size=11, italic=True, color=BLUE)
    ws.row_dimensions[3].height = 25
    ws["A5"] = "TỔNG QUAN"
    for cell, value in (
        ("C5", "Epic"), ("D5", '=COUNTIF(C9:C500,"Epic")'),
        ("E5", "Task"), ("F5", '=COUNTIF(C9:C500,"Task")'),
        ("G5", "Sub-task"), ("H5", '=COUNTIF(C9:C500,"Sub-task")'),
        ("I5", "Ước tính (h)"), ("J5", "=SUM(F9:F500)"),
    ):
        ws[cell] = value
    ws.merge_cells("A6:K6")
    ws["A6"] = "Linked Issues: blocks: 1.2, DEV-123  (Alt+Enter để thêm quan hệ trên dòng mới)"
    ws["A6"].font = Font(name="Aptos", color="465E77", italic=True)
    ws["A6"].alignment = Alignment(indent=1, vertical="center")
    ws.row_dimensions[6].height = 28

    for index, (name, width) in enumerate(zip(COLUMNS, WIDTHS), start=1):
        from openpyxl.utils import get_column_letter
        col = get_column_letter(index)
        ws.column_dimensions[col].width = width
        cell = ws.cell(8, index, name)
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(name="Aptos", bold=True, color="FFFFFF")
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        cell.border = Border(
            top=Side(style="medium", color=BLUE),
            bottom=Side(style="medium", color=BLUE),
            left=Side(style="thin", color="829AB4"),
            right=Side(style="thin", color="829AB4"),
        )
    ws.row_dimensions[8].height = 33
    grid = Side(style="thin", color=GRID)
    rows = EXAMPLES if example else []
    for r in range(9, 9 + max(28, len(rows))):
        example_row = rows[r - 9] if (r - 9) < len(rows) else None
        kind = example_row[2] if example_row else ""
        bg = BLUE if kind == "Epic" else LIGHT_BLUE if kind == "Task" else (
            "FFFFFF" if r % 2 else VERY_LIGHT
        )
        ws.row_dimensions[r].height = 34 if kind in ("Epic", "Task") else 29
        if kind == "Task":
            ws.row_dimensions[r].outlineLevel = 1
        elif kind == "Sub-task":
            ws.row_dimensions[r].outlineLevel = 2
        for c in range(1, 12):
            cell = ws.cell(r, c, example_row[c - 1] if example_row else None)
            cell.fill = PatternFill("solid", fgColor=bg)
            cell.font = Font(
                name="Aptos", size=10, bold=kind in ("Epic", "Task"),
                color="FFFFFF" if kind == "Epic" else BLUE if kind == "Task" else "27384A",
            )
            cell.alignment = Alignment(
                vertical="center", wrap_text=True,
                indent=1 if c == 2 and kind == "Task" else
                2 if c == 2 and kind == "Sub-task" else 0,
            )
            cell.border = Border(
                left=grid, right=grid,
                top=Side(style="medium", color="6685A5") if kind == "Epic" else grid,
                bottom=Side(style="medium", color="6685A5") if kind == "Epic" else grid,
            )
            if c == 1:
                cell.number_format = "@"
            if c == 7:
                cell.number_format = "yyyy-mm-dd"
            if c == 8 and example_row and example_row[7] and kind != "Epic":
                cell.fill = PatternFill("solid", fgColor=PALE_YELLOW)
    ws.conditional_formatting.add(
        "H9:H500",
        FormulaRule(formula=['AND(LEN(H9)>0,$C9<>"Epic")'],
                    fill=PatternFill("solid", fgColor=PALE_YELLOW)),
    )

    type_dv = DataValidation(type="list", formula1='"Epic,Task,Sub-task"')
    priority_dv = DataValidation(type="list", formula1='"Highest,High,Medium,Low,Lowest"')
    ws.add_data_validation(type_dv)
    ws.add_data_validation(priority_dv)
    type_dv.add("C9:C500")
    priority_dv.add("E9:E500")
    ws.print_options.horizontalCentered = True
    ws.print_title_rows = "1:8"

    guide = wb.create_sheet("02_Huong_dan")
    for col, width in (("A", 27), ("B", 105)):
        guide.column_dimensions[col].width = width
    instructions = [
        ("WBS", "Lưu dưới dạng Text. Epic = 1, Task = 1.1, Sub-task = 1.1.1; không dùng 0 ở các segment."),
        ("Issue Type", "Chỉ Epic / Task / Sub-task. Bắt buộc khớp với cấp WBS."),
        ("Linked Issues", "Mỗi quan hệ một dòng trong một cell: blocks: 1.2, ABC-123"),
        ("External Issues", "Tham chiếu Jira Key đang tồn tại, ví dụ DEV-125."),
        ("Original Estimate", "Nhập số giờ (8, 1.5) hoặc duration Jira (1d 2h). Không ghi tổng roll-up."),
        ("Date", "Dùng ô ngày Excel, yyyy-mm-dd hoặc dd/mm/yyyy."),
        ("Grouping", "Excel Outline 1/2/3, +/- nằm bên trái; nếu thêm/xóa dòng hãy Group lại."),
        ("Validate", "itools jira issue validate planning.xlsx"),
        ("Dry-run", "itools jira issue import planning.xlsx -p DEV --dry-run"),
        ("Import", "itools jira issue import planning.xlsx -p DEV"),
        ("Resume", "itools jira issue import planning.xlsx -p DEV --resume"),
        ("Security", "Jira URL qua config TOML, PAT/credentials qua environment variables."),
    ]
    for r, (key, value) in enumerate(instructions, 1):
        a, b = guide.cell(r, 1, key), guide.cell(r, 2, value)
        a.fill = PatternFill("solid", fgColor=BLUE)
        a.font = Font(bold=True, color="FFFFFF")
        b.font = Font(color=BLUE)
        a.alignment = b.alignment = Alignment(wrap_text=True, vertical="center")
        a.border = b.border = Border(bottom=grid)
        guide.row_dimensions[r].height = 32
    wb.save(output)
    return output
