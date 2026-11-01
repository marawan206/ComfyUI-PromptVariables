import locale
import zipfile

import pytest

from pack.spreadsheet import parse_rows, read_spreadsheet, select_rows

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return str(path)


def inline(ref, text):
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def make_xlsx(path, sheets, shared):
    workbook = "".join(f'<sheet name="{name}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, name in enumerate(sheets))
    # One relative and one absolute target, both occur in real files.
    targets = ["worksheets/sheet1.xml", "/xl/worksheets/sheet2.xml"]
    rels = "".join(f'<Relationship Id="rId{i + 1}" Type="{REL}/worksheet" Target="{targets[i]}"/>' for i in range(len(sheets)))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{MAIN}" xmlns:r="{REL}"><sheets>{workbook}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{PKG}">{rels}</Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{MAIN}">{"".join(shared)}</sst>')
        for i, rows in enumerate(sheets.values()):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", f'<worksheet xmlns="{MAIN}"><sheetData>{rows}</sheetData></worksheet>')
    return str(path)


@pytest.fixture
def workbook(tmp_path):
    people = (
        f'<row r="1"><c r="A1" t="s"><v>0</v></c>{inline("B1", "Town")}<c r="C1" t="str"><v>Count</v></c>{inline("D1", "Flag")}</row>'
        '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" t="s"><v>2</v></c><c r="C2"><v>3</v></c><c r="D2" t="b"><v>1</v></c></row>'
        f'<row r="3">{inline("A3", "blue")}<c r="C3"><v>2.5</v></c></row>'
        '<row r="4"><c r="A4"/></row>'
        f'<row r="5">{inline("A5", "green")}<c r="C5"><v>1E-3</v></c><c r="D5" t="b"><v>0</v></c></row>'
    )
    places = '<row r="1"><c t="inlineStr"><is><t>Place</t></is></c></row><row r="2"><c t="inlineStr"><is><t>Rome</t></is></c></row>'
    shared = [
        "<si><t>Clothing</t></si>",
        "<si><r><t>red </t></r><r><t>coat</t></r></si>",
        '<si><t>Paris</t><rPh sb="0" eb="1"><t>パリ</t></rPh></si>',
    ]
    return make_xlsx(tmp_path / "book.xlsx", {"People": people, "Places": places}, shared)


def test_csv_bom_quotes_and_blank_lines(tmp_path):
    path = write(tmp_path, "a.csv", '﻿Clothing,Town\r\n"red, wet coat",Paris\r\n\r\n"two\nlines",Rome\r\n')
    table = read_spreadsheet(path)
    assert table["columns"] == ["Clothing", "Town"]
    assert table["rows"] == [["red, wet coat", "Paris"], ["two\nlines", "Rome"]]
    assert table["numbers"] == [2, 4]
    assert table["sheets"] == []


def test_csv_semicolon_in_system_code_page(tmp_path, monkeypatch):
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252")
    path = write(tmp_path, "b.csv", "Mood;Town\ncalm;Café\n".encode("cp1252"))
    assert read_spreadsheet(path)["rows"] == [["calm", "Café"]]


def test_csv_in_other_encoding_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "utf-8")
    path = write(tmp_path, "b.csv", "服装,町\n赤いコート,京都\n".encode("cp932"))
    with pytest.raises(ValueError, match="CSV UTF-8"):
        read_spreadsheet(path)


def test_single_column_csv_keeps_commas(tmp_path):
    path = write(tmp_path, "prompts.csv", 'subject\na cat, sitting on a chair\n"a dog, running"\n')
    assert read_spreadsheet(path)["rows"] == [["a cat, sitting on a chair"], ["a dog, running"]]


def test_tsv(tmp_path):
    path = write(tmp_path, "c.tsv", "a\tb\n1\t2, 3\n")
    assert read_spreadsheet(path)["rows"] == [["1", "2, 3"]]


def test_blank_header_columns_dropped_and_short_rows_padded(tmp_path):
    path = write(tmp_path, "d.csv", " Clothing ,,Town\nred,note\n,,\nblue,x,Rome\n")
    table = read_spreadsheet(path)
    assert table["columns"] == ["Clothing", "Town"]
    assert table["rows"] == [["red", ""], ["blue", "Rome"]]
    assert table["numbers"] == [2, 4]


@pytest.mark.parametrize("name,data,message", [
    ("e.csv", "", "is empty"),
    ("f.csv", "a,b\n,\n", "only has the header row"),
    ("g.txt", "a\n1\n", "is not a .csv, .tsv or .xlsx file"),
    ("h.xlsx", "not a zip", "could not be read"),
])
def test_friendly_errors(tmp_path, name, data, message):
    with pytest.raises(ValueError, match=message):
        read_spreadsheet(write(tmp_path, name, data))


def test_missing_file(tmp_path):
    with pytest.raises(ValueError, match="was not found"):
        read_spreadsheet(str(tmp_path / "nope.csv"))


def test_xlsx(workbook):
    table = read_spreadsheet(workbook)
    assert table["sheets"] == ["People", "Places"]
    assert table["columns"] == ["Clothing", "Town", "Count", "Flag"]
    assert table["rows"] == [["red coat", "Paris", "3", "TRUE"], ["blue", "", "2.5", ""], ["green", "", "0.001", "FALSE"]]
    assert table["numbers"] == [2, 3, 5]


def test_xlsx_escaped_characters(tmp_path):
    rows = '<row r="1"><c r="A1" t="s"><v>0</v></c></row><row r="2"><c r="A2" t="s"><v>1</v></c></row>'
    path = make_xlsx(tmp_path / "esc.xlsx", {"Sheet1": rows}, ["<si><t>Text</t></si>", "<si><t>one_x000D_\ntwo</t></si>"])
    assert read_spreadsheet(path)["rows"] == [["one\r\ntwo"]]


def test_xlsx_sheet_by_name(workbook):
    assert read_spreadsheet(workbook, " places ")["rows"] == [["Rome"]]
    with pytest.raises(ValueError, match="Tabs: People, Places"):
        read_spreadsheet(workbook, "Nope")


def test_parse_rows():
    assert parse_rows("all") is None
    assert parse_rows("") is None
    assert parse_rows(" 2-5, 9, 12-, ") == [(2, 5), (9, 9), (12, None)]
    with pytest.raises(ValueError, match="not a row selection"):
        parse_rows("two")


def test_select_rows(workbook):
    table = read_spreadsheet(workbook)
    assert select_rows(table, "all") is table
    assert select_rows(table, "3-")["numbers"] == [3, 5]
    assert select_rows(table, "2, 5")["rows"] == [table["rows"][0], table["rows"][2]]
    with pytest.raises(ValueError, match="No rows match"):
        select_rows(table, "9")
