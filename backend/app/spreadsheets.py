from __future__ import annotations

import csv
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


MAX_ROWS = 5_000
MAX_COLUMNS = 200
SUPPORTED_SPREADSHEET_EXTENSIONS = {".ods", ".xlsx", ".csv"}

ODF_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}
XLSX_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}


class SpreadsheetError(ValueError):
    pass


def _q(namespace: dict[str, str], prefix: str, name: str) -> str:
    return f"{{{namespace[prefix]}}}{name}"


def _trim(rows: list[list[str]]) -> list[list[str]]:
    while rows and not any(value.strip() for value in rows[-1]):
        rows.pop()
    if not rows:
        return []
    populated_columns = [
        index
        for index in range(max(len(row) for row in rows))
        if any(index < len(row) and row[index].strip() for row in rows)
    ]
    if not populated_columns:
        return []
    first, last = populated_columns[0], populated_columns[-1]
    return [(row + [""] * (last + 1 - len(row)))[first : last + 1] for row in rows]


def _odf_text(element: ET.Element) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        if child.tag == _q(ODF_NS, "text", "s"):
            parts.append(" " * int(child.attrib.get(_q(ODF_NS, "text", "c"), "1")))
        elif child.tag == _q(ODF_NS, "text", "tab"):
            parts.append("\t")
        elif child.tag == _q(ODF_NS, "text", "line-break"):
            parts.append("\n")
        else:
            content = _odf_text(child)
            href = child.attrib.get(_q(ODF_NS, "xlink", "href"))
            parts.append(f"[{content}]({href})" if href and content else content)
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _odf_cell(cell: ET.Element) -> str:
    paragraphs = [_odf_text(item).strip() for item in cell.findall(".//text:p", ODF_NS)]
    paragraphs = [value for value in paragraphs if value]
    if paragraphs:
        return "\n".join(paragraphs)
    for attribute in ("string-value", "date-value", "time-value", "boolean-value", "value"):
        value = cell.attrib.get(_q(ODF_NS, "office", attribute))
        if value is not None:
            return value
    return ""


def _read_ods(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("content.xml"))
    sheets: list[dict[str, Any]] = []
    for table in root.findall(".//table:table", ODF_NS):
        rows: list[list[str]] = []
        truncated = False
        for row_element in table.iter(_q(ODF_NS, "table", "table-row")):
            values: list[str] = []
            for cell in row_element:
                if cell.tag not in {
                    _q(ODF_NS, "table", "table-cell"),
                    _q(ODF_NS, "table", "covered-table-cell"),
                }:
                    continue
                value = (
                    _odf_cell(cell)
                    if cell.tag == _q(ODF_NS, "table", "table-cell")
                    else ""
                )
                repeat = int(
                    cell.attrib.get(_q(ODF_NS, "table", "number-columns-repeated"), "1")
                )
                remaining = MAX_COLUMNS - len(values)
                values.extend([value] * min(repeat, max(0, remaining)))
                truncated = truncated or (bool(value.strip()) and repeat > remaining)
            row_repeat = int(
                row_element.attrib.get(_q(ODF_NS, "table", "number-rows-repeated"), "1")
            )
            remaining_rows = MAX_ROWS - len(rows)
            if not any(value.strip() for value in values) and row_repeat > remaining_rows:
                break
            rows.extend([values.copy() for _ in range(min(row_repeat, max(0, remaining_rows)))])
            if row_repeat > remaining_rows or len(rows) >= MAX_ROWS:
                truncated = truncated or any(value.strip() for value in values)
                break
        sheets.append(
            {
                "name": table.attrib.get(_q(ODF_NS, "table", "name"), "Sheet"),
                "rows": _trim(rows),
                "truncated": truncated,
            }
        )
    return sheets


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    if not letters:
        return 0
    value = 0
    for letter in letters.group(0):
        value = value * 26 + ord(letter) - ord("A") + 1
    return value - 1


def _xlsx_text(element: ET.Element) -> str:
    return "".join(item.text or "" for item in element.findall(".//main:t", XLSX_NS))


def _read_xlsx(path: Path) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [_xlsx_text(item) for item in shared_root.findall("main:si", XLSX_NS)]

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in relationships.findall("pkg:Relationship", XLSX_NS)
        }
        sheets: list[dict[str, Any]] = []
        for sheet in workbook.findall("main:sheets/main:sheet", XLSX_NS):
            relation_id = sheet.attrib.get(_q(XLSX_NS, "rel", "id"), "")
            target = targets.get(relation_id, "")
            target = target.lstrip("/")
            archive_path = target if target.startswith("xl/") else f"xl/{target}"
            if archive_path not in archive.namelist():
                continue
            worksheet = ET.fromstring(archive.read(archive_path))
            rows: list[list[str]] = []
            truncated = False
            for row_element in worksheet.findall("main:sheetData/main:row", XLSX_NS):
                if len(rows) >= MAX_ROWS:
                    truncated = True
                    break
                values: list[str] = []
                for cell in row_element.findall("main:c", XLSX_NS):
                    column = _column_index(cell.attrib.get("r", "A1"))
                    if column >= MAX_COLUMNS:
                        truncated = True
                        continue
                    while len(values) <= column:
                        values.append("")
                    kind = cell.attrib.get("t", "")
                    raw = cell.findtext("main:v", default="", namespaces=XLSX_NS)
                    if kind == "s" and raw:
                        try:
                            value = shared[int(raw)]
                        except (ValueError, IndexError):
                            value = raw
                    elif kind == "inlineStr":
                        value = _xlsx_text(cell)
                    elif kind == "b":
                        value = "TRUE" if raw == "1" else "FALSE"
                    else:
                        value = raw
                    if not value:
                        formula = cell.findtext("main:f", default="", namespaces=XLSX_NS)
                        value = f"={formula}" if formula else ""
                    values[column] = value
                rows.append(values)
            sheets.append(
                {
                    "name": sheet.attrib.get("name", "Sheet"),
                    "rows": _trim(rows),
                    "truncated": truncated,
                }
            )
    return sheets


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(16_384)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = []
        truncated = False
        for index, row in enumerate(csv.reader(handle, dialect)):
            if index >= MAX_ROWS:
                truncated = True
                break
            if len(row) > MAX_COLUMNS:
                truncated = True
            rows.append(row[:MAX_COLUMNS])
    return [{"name": "Sheet1", "rows": _trim(rows), "truncated": truncated}]


def read_spreadsheet(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_SPREADSHEET_EXTENSIONS:
        raise SpreadsheetError("Supported spreadsheet types are ODS, XLSX, and CSV")
    try:
        if suffix == ".ods":
            sheets = _read_ods(path)
        elif suffix == ".xlsx":
            sheets = _read_xlsx(path)
        else:
            sheets = _read_csv(path)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as error:
        raise SpreadsheetError(f"Could not read spreadsheet: {path.name}") from error
    return sheets or [{"name": "Sheet1", "rows": [], "truncated": False}]


def _escape_table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def spreadsheet_to_markdown(path: Path) -> str:
    blocks = [f"# {path.stem}"]
    sheets = read_spreadsheet(path)
    show_sheet_names = len(sheets) > 1 or sheets[0]["name"].casefold() != "sheet1"
    for sheet in sheets:
        if show_sheet_names:
            blocks.append(f"## {sheet['name']}")
        rows = sheet["rows"]
        if not rows:
            blocks.append("_Empty sheet._")
            continue
        width = max(len(row) for row in rows)
        padded = [row + [""] * (width - len(row)) for row in rows]
        header = [value.strip() or f"Column {index + 1}" for index, value in enumerate(padded[0])]
        table = ["| " + " | ".join(_escape_table_cell(value) for value in header) + " |"]
        table.append("| " + " | ".join("---" for _ in header) + " |")
        for row in padded[1:]:
            table.append("| " + " | ".join(_escape_table_cell(value) for value in row) + " |")
        blocks.append("\n".join(table))
        if sheet["truncated"]:
            blocks.append("> Preview truncated at the spreadsheet safety limit.")
    return "\n\n".join(blocks) + "\n"
