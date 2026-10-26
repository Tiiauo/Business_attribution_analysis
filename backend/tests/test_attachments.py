import io
import zipfile

import pytest

from app.attachment_parser import ParseFailure, parse
from app.runtime import ParseLimits

LIMITS = ParseLimits().model_dump()


def workbook(rows, *, epoch="0", extra=None, hidden=False):
    buffer = io.BytesIO()
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        "xl/workbook.xml": f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><workbookPr date1904="{epoch}"/><sheets><sheet name="数据" sheetId="1" r:id="r1" state="{"hidden" if hidden else "visible"}"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>',
        "xl/styles.xml": '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="22"/></cellXfs></styleSheet>',
        **(extra or {}),
    }
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def test_csv_preserves_values_header_options_and_original_positions():
    content = '\ufeff\n id ,value,note\r\n00123,,"甲,乙\n丙"\r\nNULL,NA,"引号""保留"\r\n,,\r\n'.encode()
    result = parse(content, "csv", {"has_header": True}, LIMITS)
    table = result["files"][0]
    assert table["rows"] == [
        {"id": "00123", "value": None, "note": "甲,乙\n丙"},
        {"id": "NULL", "value": "NA", "note": '引号"保留'},
    ]
    assert table["locations"][0] == {"record": 3, "line_start": 3, "line_end": 4}
    assert result["summary"][0]["skipped_empty_rows"] == 2
    no_header = parse(b"00123,01\n00001,02", "csv", {"has_header": False}, LIMITS)
    assert no_header["files"][0]["rows"][0] == {"column_1": "00123", "column_2": "01"}


@pytest.mark.parametrize(
    "content,code",
    [
        (b"a,a\n1,2", "TABLE_HEADER_INVALID"),
        (b"a,\n1,2", "TABLE_HEADER_INVALID"),
        (b"a,b\n1,2,3", "TABLE_SHAPE_INVALID"),
        (b"a,b\n", "TABLE_EMPTY"),
        (b"a\n\xff", "FILE_PARSE_INVALID"),
        (b"a\x00b", "FILE_TYPE_INVALID"),
    ],
)
def test_csv_rejects_ambiguous_or_invalid_tables(content, code):
    with pytest.raises(ParseFailure, match=code):
        parse(content, "csv", {"has_header": True}, LIMITS)


def test_xlsx_exact_types_dates_sparse_cells_and_header():
    rows = '<row r="2"><c r="A2" t="inlineStr"><is><t>id</t></is></c><c r="B2" t="inlineStr"><is><t>money</t></is></c><c r="C2" t="inlineStr"><is><t>date</t></is></c></row><row r="5"><c r="A5" t="s"><v>0</v></c><c r="B5"><v>123456789012345.001</v></c><c r="C5" s="1"><v>61</v></c></row><row r="8"><c r="A8" t="inlineStr"><is><t>00002</t></is></c></row>'
    extra = {
        "xl/sharedStrings.xml": '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>00123</t></si></sst>'
    }
    result = parse(workbook(rows, extra=extra), "xlsx", {"has_header": True}, LIMITS)
    file = result["files"][0]
    assert file["rows"][0] == {"id": "00123", "money": "123456789012345.001", "date": "61"}
    assert file["rows"][1] == {"id": "00002", "money": None, "date": None}
    assert file["locations"] == [{"sheet": "数据", "row": 5}, {"sheet": "数据", "row": 8}]
    assert file["cells"][0][-1]["date"]["converted"] == "1900-03-01"
    assert file["cells"][0][-1]["date_system"] == "1900"
    no_header = parse(workbook('<row r="3"><c r="B3"><v>2.001</v></c></row>'), "xlsx", {"has_header": False}, LIMITS)
    assert no_header["files"][0]["rows"] == [{"column_1": None, "column_2": "2.001"}]
    date1904 = parse(
        workbook('<row r="1"><c r="A1" s="2"><v>0.5</v></c></row>', epoch="1"), "xlsx", {"has_header": False}, LIMITS
    )
    assert date1904["files"][0]["cells"][0][0]["date"]["converted"] == "1904-01-01T12:00:00+08:00"


@pytest.mark.parametrize(
    "cell,code",
    [
        ("<f>SUM(A1:A2)</f><v>3</v>", "XLSX_FORMULA_UNSUPPORTED"),
        ('<f t="shared" si="0"/><v>3</v>', "XLSX_FORMULA_UNSUPPORTED"),
        ('<f t="array" ref="A1:A2">1+2</f>', "XLSX_FORMULA_UNSUPPORTED"),
        ("<v>60</v>", "TABLE_DATE_INVALID"),
    ],
)
def test_hidden_xlsx_formulas_and_invalid_1900_date(cell, code):
    with pytest.raises(ParseFailure, match=code):
        parse(
            workbook(f'<row r="7"><c r="A7" s="1">{cell}</c></row>', hidden=True), "xlsx", {"has_header": False}, LIMITS
        )


def test_xlsx_shape_header_external_entities_and_real_expansion_limits():
    for rows, code in [
        ('<row r="1"><c r="A1"><v>1</v></c></row>', "TABLE_HEADER_INVALID"),
        (
            '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row><row r="2"><c r="B2"><v>1</v></c></row>',
            "TABLE_SHAPE_INVALID",
        ),
    ]:
        with pytest.raises(ParseFailure, match=code):
            parse(workbook(rows), "xlsx", {"has_header": True}, LIMITS)
    with pytest.raises(ParseFailure, match="XLSX_EXTERNAL_UNSUPPORTED"):
        parse(
            workbook(
                "",
                extra={
                    "xl/_rels/link.rels": '<Relationships><Relationship TargetMode="External" Target="https://example.com"/></Relationships>'
                },
            ),
            "xlsx",
            {"has_header": False},
            LIMITS,
        )
    with pytest.raises(ParseFailure, match="XLSX_MAX_UNCOMPRESSED_BYTES"):
        parse(
            workbook("", extra={"padding.txt": "x" * 5000}),
            "xlsx",
            {"has_header": False},
            {**LIMITS, "xlsx_max_uncompressed_bytes": 4000},
        )
    with pytest.raises(ParseFailure, match="XLSX_UNSAFE_XML"):
        parse(
            workbook("", extra={"xl/workbook.xml": '<!DOCTYPE a [<!ENTITY x "bad">]><a>&x;</a>'}),
            "xlsx",
            {"has_header": False},
            LIMITS,
        )


def test_text_and_row_limits():
    assert parse("甲😀".encode(), "md", {}, LIMITS)["files"][0]["text"] == "甲😀"
    with pytest.raises(ParseFailure, match="TEXT_MAX_CHARS"):
        parse("甲😀".encode(), "txt", {}, {**LIMITS, "text_max_chars": 1})
    with pytest.raises(ParseFailure, match="PARSE_MAX_ROWS"):
        parse(b"v\n1\n2\n", "csv", {"has_header": True}, {**LIMITS, "parse_max_rows": 1})
