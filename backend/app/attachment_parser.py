"""Static values only; this module is copied into the isolated parser container."""

import csv
import io
import json
import posixpath
import re
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
DATE_FORMATS = {
    14: "mm-dd-yy",
    15: "d-mmm-yy",
    16: "d-mmm",
    17: "mmm-yy",
    18: "h:mm AM/PM",
    19: "h:mm:ss AM/PM",
    20: "h:mm",
    21: "h:mm:ss",
    22: "m/d/yy h:mm",
    45: "mm:ss",
    46: "[h]:mm:ss",
    47: "mmss.0",
}


class ParseFailure(ValueError):
    pass


def fail(code, detail):
    raise ParseFailure(f"{code}: {detail}")


def table(records, name, has_header, budget, *, xlsx=False):
    blank, data, locations, cells = 0, [], [], []
    nonempty = []
    for values, location, metadata in records:
        if not any(value is not None and value != "" for value in values):
            blank += 1
        else:
            nonempty.append((values, location, metadata))
    if not nonempty:
        return None
    first, origin, _ = nonempty[0]
    width = len(first) if has_header else max(len(row[0]) for row in nonempty)
    if has_header:
        if any(not isinstance(v, str) or not v.strip() for v in first):
            fail("TABLE_HEADER_INVALID", f"{name} {origin} 表头必须为非空文本")
        columns = [value.strip() for value in first]
        if len(set(columns)) != width:
            fail("TABLE_HEADER_INVALID", f"{name} {origin} 表头重复")
        nonempty = nonempty[1:]
    else:
        columns = [f"column_{i + 1}" for i in range(width)]
    for values, location, metadata in nonempty:
        if (not xlsx and len(values) != width) or (xlsx and len(values) > width):
            fail("TABLE_SHAPE_INVALID", f"{name} {location} 列数不一致")
        budget[0] -= 1
        if budget[0] < 0:
            fail("PARSE_MAX_ROWS", "超过 parse_max_rows，请拆分文件")
        values = values + [None] * (width - len(values))
        data.append(dict(zip(columns, values, strict=True)))
        locations.append(location)
        if xlsx:
            cells.append(metadata)
    return {
        "rows": data,
        "position": {"table": name, "columns": columns},
        "locations": locations,
        "cells": cells,
        "summary": {"name": name, "columns": columns, "row_count": len(data), "skipped_empty_rows": blank},
    }


def csv_table(content, options, limits):
    text = content.decode("utf-8-sig")
    csv.field_size_limit(20_000_000)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"', strict=True)
    records, line = [], 0
    for index, row in enumerate(reader, 1):
        records.append(
            (
                [v if v != "" else None for v in row],
                {"record": index, "line_start": line + 1, "line_end": reader.line_num},
                [],
            )
        )
        line = reader.line_num
    value = table(records, "CSV", options["has_header"], [limits["parse_max_rows"]])
    return [value] if value else []


def xml(content):
    # Reject declarations before parsing, including UTF-16/32 encoded XML.
    probe = content.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in probe or b"<!ENTITY" in probe:
        fail("XLSX_UNSAFE_XML", "XML 不得包含 DTD 或实体")
    return ET.fromstring(content)


def excel_date(raw, fmt, epoch, location):
    number = Decimal(raw)
    stripped = re.sub(r'"[^"]*"|\\.|\[[^]]*\]', "", fmt).lower()
    is_date = bool(re.search(r"[yd]", stripped))
    is_time = bool(re.search(r"[hs]", stripped))
    if not (is_date or is_time):
        return None
    if not number.is_finite() or number < 0:
        fail("TABLE_DATE_INVALID", f"{location} 日期序号无效")
    if epoch == "1900" and int(number) == 60:
        fail("TABLE_DATE_INVALID", f"{location} 虚构的 1900-02-29")
    base = datetime(1904, 1, 1) if epoch == "1904" else datetime(1899, 12, 31)
    adjusted = number - (1 if epoch == "1900" and number >= 61 else 0)
    value = base + timedelta(microseconds=int(adjusted * Decimal(86400000000)))
    if is_date and not is_time:
        return {"converted": value.date().isoformat(), "kind": "date"}
    if not is_date:
        return {"converted": value.time().isoformat(), "kind": "time", "elapsed_days": str(number)}
    return {
        "converted": value.replace(tzinfo=timezone(timedelta(hours=8))).isoformat(),
        "kind": "datetime",
        "timezone": "Asia/Shanghai",
    }


def xlsx_tables(content, options, limits):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        total, parts = 0, {}
        for item in archive.infolist():
            name = item.filename
            if name in parts or name.startswith("/") or "\\" in name or ".." in name.split("/"):
                fail("XLSX_INVALID", "压缩包路径或重复条目无效")
            if item.flag_bits & 1:
                fail("XLSX_ENCRYPTED", "请移除工作簿加密后重传")
            if "vbaproject" in name.lower() or "externallinks/" in name.lower() or name.lower().endswith(".bin"):
                fail("XLSX_EXTERNAL_UNSUPPORTED", "不接受宏或外部链接，请移除后重传")
            chunks = []
            with archive.open(item) as stream:
                while chunk := stream.read(65536):
                    total += len(chunk)
                    if total > limits["xlsx_max_uncompressed_bytes"]:
                        fail("XLSX_MAX_UNCOMPRESSED_BYTES", "实际展开超过 xlsx_max_uncompressed_bytes，请拆分文件")
                    chunks.append(chunk)
            parts[name] = b"".join(chunks)
        if "xl/workbook.xml" not in parts or "[Content_Types].xml" not in parts:
            fail("XLSX_INVALID", "不是有效的 XLSX 工作簿")
        types = xml(parts["[Content_Types].xml"])
        if any("macroEnabled" in n.get("ContentType", "") for n in types):
            fail("XLSX_EXTERNAL_UNSUPPORTED", "不接受宏格式")
        for name, value in parts.items():
            if name.endswith(".rels"):
                for rel in xml(value):
                    if rel.get("TargetMode") == "External" or "externalLink" in rel.get("Type", ""):
                        fail("XLSX_EXTERNAL_UNSUPPORTED", "外部链接不受支持，请移除后重传")
        workbook = xml(parts["xl/workbook.xml"])
        props = workbook.find("s:workbookPr", NS)
        epoch = "1904" if props is not None and props.get("date1904") in {"1", "true"} else "1900"
        relations = {n.get("Id"): n.get("Target") for n in xml(parts["xl/_rels/workbook.xml.rels"])}
        strings = []
        if "xl/sharedStrings.xml" in parts:
            strings = [
                "".join(t.text or "" for t in n.findall(".//s:t", NS)) for n in xml(parts["xl/sharedStrings.xml"])
            ]
        formats, styles = dict(DATE_FORMATS), [0]
        if "xl/styles.xml" in parts:
            style = xml(parts["xl/styles.xml"])
            formats.update(
                {int(n.get("numFmtId")): n.get("formatCode") for n in style.findall("s:numFmts/s:numFmt", NS)}
            )
            styles = [int(n.get("numFmtId", "0")) for n in style.findall("s:cellXfs/s:xf", NS)] or [0]
        tables, budget = [], [limits["parse_max_rows"]]
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            name = sheet.get("name")
            target = relations[sheet.get(REL)]
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            if not target.startswith("xl/") or target not in parts:
                fail("XLSX_INVALID", "工作表关系无效")
            sheet_xml = xml(parts[target])
            records, previous = [], 0
            for row in sheet_xml.findall("s:sheetData/s:row", NS):
                number = int(row.get("r", str(previous + 1)))
                if number <= previous or number > 1048576:
                    fail("TABLE_SHAPE_INVALID", f"{name} 原始行号无效")
                previous = number
                values, metadata, used = {}, [], set()
                for cell in row.findall("s:c", NS):
                    address = cell.get("r", "")
                    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]*)", address)
                    if not match or int(match[2]) != number:
                        fail("TABLE_SHAPE_INVALID", f"{name}!{address} 定位无效")
                    column = 0
                    for letter in match[1]:
                        column = column * 26 + ord(letter) - 64
                    if column > 16384 or column in used:
                        fail("TABLE_SHAPE_INVALID", f"{name}!{address} 列号重复或越界")
                    used.add(column)
                    location = f"{name}!{address}"
                    if cell.find("s:f", NS) is not None:
                        fail("XLSX_FORMULA_UNSUPPORTED", f"{location} 包含公式，请转为静态值后重传")
                    kind, raw = cell.get("t", "n"), cell.findtext("s:v", default="", namespaces=NS)
                    fmt = formats.get(styles[int(cell.get("s", "0"))], "General")
                    date = None
                    if kind == "s":
                        value = strings[int(raw)]
                    elif kind == "inlineStr":
                        value = "".join(n.text or "" for n in cell.findall("s:is//s:t", NS))
                    elif kind == "b":
                        if raw not in {"0", "1"}:
                            fail("XLSX_INVALID", f"{location} 布尔值无效")
                        value = raw == "1"
                    elif kind == "n":
                        value = raw or None
                        if value is not None:
                            if not Decimal(raw).is_finite():
                                fail("XLSX_INVALID", f"{location} 数值无效")
                            date = excel_date(raw, fmt, epoch, location)
                    elif kind == "d":
                        value = raw
                        parsed = datetime.fromisoformat(raw)
                        date = (
                            {"converted": parsed.date().isoformat(), "kind": "date"}
                            if len(raw) == 10
                            else {
                                "converted": parsed.replace(
                                    tzinfo=parsed.tzinfo or timezone(timedelta(hours=8))
                                ).isoformat(),
                                "kind": "datetime",
                                "timezone": "Asia/Shanghai",
                            }
                        )
                    elif kind == "str":
                        value = raw
                    elif kind == "e":
                        fail("XLSX_CELL_ERROR", f"{location} 包含错误值 {raw}")
                    else:
                        fail("XLSX_INVALID", f"{location} 单元格类型不支持")
                    if value is not None and value != "":
                        # Numeric headers remain numbers for the strict header check.
                        values[column] = (
                            Decimal(value) if kind == "n" and options["has_header"] and not records else value
                        )
                    metadata.append(
                        {
                            "cell": address,
                            "type": kind,
                            "raw": raw if kind != "inlineStr" else value,
                            "number_format": fmt,
                            "date_system": epoch,
                            "date": date,
                        }
                    )
                width = max(values, default=0)
                row_values = [values.get(i) for i in range(1, width + 1)]
                if any(isinstance(v, Decimal) for v in row_values) and records:
                    row_values = [str(v) if isinstance(v, Decimal) else v for v in row_values]
                records.append((row_values, {"sheet": name, "row": number}, metadata))
            # Validate header types from their raw cells, even after leading empty rows.
            if options["has_header"]:
                for values, location, metadata in records:
                    if any(v is not None and v != "" for v in values):
                        if any(m["type"] not in {"s", "inlineStr", "str"} and m["raw"] != "" for m in metadata):
                            fail("TABLE_HEADER_INVALID", f"{name} {location} 表头必须为字符串")
                        break
            value = table(records, name, options["has_header"], budget, xlsx=True)
            if value:
                value["position"].update(date_system=epoch, timezone="Asia/Shanghai")
                tables.append(value)
        return tables


def parse(content, kind, options, limits):
    try:
        if kind in {"txt", "md", "markdown"}:
            value = content.decode("utf-8-sig")
            if "\x00" in value:
                fail("FILE_TYPE_INVALID", "文本包含二进制内容，请转换为 UTF-8")
            if len(value) > limits["text_max_chars"]:
                fail("TEXT_MAX_CHARS", "超过 text_max_chars，请拆分文件")
            return {"files": [{"format": "text", "text": value, "position": {"kind": "line"}}], "summary": []}
        if kind == "csv":
            if b"\x00" in content or content.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):
                fail("FILE_TYPE_INVALID", "CSV 必须为 UTF-8 逗号分隔文本")
            tables = csv_table(content, options, limits)
        elif kind == "xlsx":
            tables = xlsx_tables(content, options, limits)
        else:
            fail("FILE_TYPE_INVALID", "文件类型不受支持")
        if not tables or not sum(t["summary"]["row_count"] for t in tables):
            fail("TABLE_EMPTY", "表格没有非空数据记录")
        return {"files": [{"format": "json", **t} for t in tables], "summary": [t["summary"] for t in tables]}
    except ParseFailure:
        raise
    except (
        ValueError,
        KeyError,
        IndexError,
        ET.ParseError,
        zipfile.BadZipFile,
        RuntimeError,
        csv.Error,
        OverflowError,
    ) as error:
        fail("FILE_PARSE_INVALID", f"文件损坏或内容无效（{type(error).__name__}），请转换格式后重传")


if __name__ == "__main__":
    try:
        payload = json.loads(Path("/input/data.json").read_text())
        result = parse(Path("/input/source").read_bytes(), payload["kind"], payload["options"], payload["limits"])
        value = {"result": result}
    except ParseFailure as error:
        value = {"error": str(error)}
    Path("/output/data/result.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
