"""
基于标准轮子 `openpyxl` 与标准库 `csv` 的 `.xlsx` / `.csv` 表格读写服务。
替代原有的 300+ 行手写 OpenXML ZIP 拼装代码，完整兼容多数据类型、公式计算值与样式表头。
"""
import csv
import io
from pathlib import Path
import re
from typing import Any
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException
from app.core.errors import PlatformError


def _decode_csv_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _cell_to_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    return str(val).strip()


def _rows_to_dicts(rows: list[list[str]]) -> list[dict[str, str]]:
    if not rows:
        return []
    headers = [h.strip() for h in rows[0]]
    results: list[dict[str, str]] = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        item = {
            headers[i]: (row[i].strip() if i < len(row) else "")
            for i in range(len(headers))
            if headers[i]
        }
        if any(item.values()):
            results.append(item)
    return results


def _parse_xlsx_bytes(raw_bytes: bytes) -> list[dict[str, str]]:
    try:
        wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    except (InvalidFileException, ValueError, OSError) as exc:
        raise PlatformError("INVALID_XLSX_FILE", "损坏或非法的 .xlsx 文件格式", status_code=400) from exc

    try:
        if not wb.sheetnames:
            raise PlatformError("INVALID_XLSX_WORKSHEET", "未在 .xlsx 文件中找到工作表", status_code=400)
        ws = wb.active or wb[wb.sheetnames[0]]
        matrix = [
            [_cell_to_str(cell) for cell in row]
            for row in ws.iter_rows(values_only=True)
            if row and any(cell is not None and str(cell).strip() for cell in row)
        ]
        return _rows_to_dicts(matrix)
    finally:
        wb.close()


def _parse_csv_bytes(raw_bytes: bytes) -> list[dict[str, str]]:
    text = _decode_csv_bytes(raw_bytes).strip()
    if not text:
        return []
    raw_rows = [r for r in csv.reader(io.StringIO(text)) if r and any(c.strip() for c in r)]
    return _rows_to_dicts(raw_rows)


def parse_tabular_upload(filename: str, raw_bytes: bytes) -> list[dict[str, str]]:
    """自动识别并解析 `.xlsx` 或 `.csv` 表格字节流，首行为表头映射为字典列表。"""
    if not raw_bytes:
        raise PlatformError("EMPTY_FILE", "上传的表格文件为空", status_code=400)

    ext = Path(filename or "").suffix.lower()
    if ext == ".xlsx" or raw_bytes[:4] == b"PK\x03\x04":
        return _parse_xlsx_bytes(raw_bytes)
    if ext in (".csv", ".txt", ""):
        return _parse_csv_bytes(raw_bytes)
    raise PlatformError("UNSUPPORTED_TABULAR_FORMAT", f"不支持的表格文件格式 '{ext}'，请上传 .xlsx 或 .csv", status_code=400)


def build_xlsx_bytes(sheet_name: str, headers: list[str], rows: list[list[Any]]) -> bytes:
    """使用 `openpyxl` 生成标准 `.xlsx` 字节流（可在 Excel / WPS / LibreOffice 直接打开）。"""
    safe_sheet = re.sub(r"[\[\]\*\?/\\:]", "_", (sheet_name or "Sheet1")).strip()[:31] or "Sheet1"
    wb = Workbook()
    ws = wb.active
    ws.title = safe_sheet

    ws.append(list(headers))
    header_font = Font(name="Calibri", size=11, bold=True)
    header_fill = PatternFill(fill_type="solid", start_color="FFF3F4F6", end_color="FFF3F4F6")
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill

    for row in rows:
        ws.append(["" if v is None else v for v in row])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
