import csv
import io
import locale
import os
import re
import zipfile
import xml.etree.ElementTree as ET

EXTENSIONS = {".csv", ".tsv", ".xlsx"}


def read_spreadsheet(path, sheet=""):
    """Returns {name, columns, rows, numbers, sheets}. numbers are the row numbers Excel shows (header = 1)."""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if ext not in EXTENSIONS:
        raise ValueError(f"{name} is not a .csv, .tsv or .xlsx file.")
    if not os.path.isfile(path):
        raise ValueError(f"{name} was not found in the ComfyUI input folder.")
    if ext == ".xlsx":
        sheets, rows = read_xlsx(path, sheet)
    else:
        sheets, rows = [], read_csv(path, "\t" if ext == ".tsv" else None)
    return make_table(name, rows) | {"sheets": sheets}


def read_csv(path, delimiter=None):
    with open(path, "rb") as f:
        data = f.read()
    # Excel's plain "CSV (Comma delimited)" uses the system code page, so try that after UTF-8.
    for encoding in ("utf-8-sig", locale.getpreferredencoding(False)):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    else:
        raise ValueError(f'{os.path.basename(path)} is not UTF-8 text. Save it again as "CSV UTF-8".')
    if delimiter is None:
        # European Excel exports use ";". A header without any of these is a single column.
        first_line = text.splitlines()[0] if text else ""
        delimiter = max(",;\t", key=first_line.count) if any(d in first_line for d in ",;\t") else "\t"
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    return [(i + 1, cells) for i, cells in enumerate(reader)]


def read_xlsx(path, sheet=""):
    name = os.path.basename(path)
    try:
        with zipfile.ZipFile(path) as z:
            workbook = ET.fromstring(z.read("xl/workbook.xml"))
            targets = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
            sheets = []
            for s in workbook.iterfind("{*}sheets/{*}sheet"):
                target = targets[next(v for k, v in s.attrib.items() if k.endswith("}id"))]
                sheets.append((s.get("name"), target[1:] if target.startswith("/") else "xl/" + target))
            names = [n for n, _ in sheets]

            part = sheets[0][1]
            if sheet.strip():
                part = next((p for n, p in sheets if n.strip().lower() == sheet.strip().lower()), None)
                if part is None:
                    raise ValueError(f'No tab named "{sheet}" in {name}. Tabs: {", ".join(names)}.')

            shared = []
            if "xl/sharedStrings.xml" in z.namelist():
                shared = [text_of(si) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iterfind("{*}si")]

            rows = []
            number = 0
            for row in ET.fromstring(z.read(part)).iterfind("{*}sheetData/{*}row"):
                number = int(row.get("r", number + 1))
                cells = {}
                col = -1
                for c in row.iterfind("{*}c"):
                    col = column_index(c.get("r")) if c.get("r") else col + 1
                    cells[col] = cell_value(c, shared)
                rows.append((number, [cells.get(i, "") for i in range(max(cells, default=-1) + 1)]))
    except (zipfile.BadZipFile, KeyError, IndexError, StopIteration, ET.ParseError):
        raise ValueError(f"{name} could not be read. Save it again from Excel as .xlsx, or export it as .csv.")
    return names, rows


def text_of(element):
    # Plain <t> or rich text runs <r><t>; skips phonetic <rPh> hints.
    text = "".join(t.text or "" for t in element.findall("{*}t") + element.findall("{*}r/{*}t"))
    # Excel writes characters like \r as _x000D_.
    return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), text)


def column_index(ref):
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def cell_value(c, shared):
    t = c.get("t", "n")
    if t == "inlineStr":
        return text_of(c.find("{*}is"))
    v = c.findtext("{*}v")
    if v is None:
        return ""
    if t == "s":
        return shared[int(v)]
    if t == "b":
        return "TRUE" if v == "1" else "FALSE"
    if t == "n":
        n = float(v)
        return str(int(n)) if n.is_integer() else format(n, ".15g")
    return v


def make_table(name, rows):
    rows = [(n, [c.strip() for c in cells]) for n, cells in rows]
    rows = [(n, cells) for n, cells in rows if any(cells)]
    if not rows:
        raise ValueError(f"{name} is empty. Put variable names in the first row and one set of values in each row below.")
    header = rows[0][1]
    keep = [i for i, h in enumerate(header) if h]
    body = []
    for n, cells in rows[1:]:
        cells = [cells[i] if i < len(cells) else "" for i in keep]
        if any(cells):
            body.append((n, cells))
    if not body:
        raise ValueError(f"{name} only has the header row. Add one row of values below it.")
    return {
        "name": name,
        "columns": [header[i] for i in keep],
        "rows": [cells for _, cells in body],
        "numbers": [n for n, _ in body],
    }


def parse_rows(rows):
    """'all' -> None, '2-5, 9, 12-' -> [(2, 5), (9, 9), (12, None)]."""
    if rows.strip().lower() in ("", "all"):
        return None
    ranges = []
    for part in rows.split(","):
        if not part.strip():
            continue
        first, dash, last = part.partition("-")
        try:
            first = int(first)
            last = int(last) if last.strip() else (None if dash else first)
        except ValueError:
            raise ValueError(f'"{rows}" is not a row selection. Use "all", or row numbers like "2-5, 9".')
        ranges.append((first, last))
    return ranges


def select_rows(table, rows):
    ranges = parse_rows(rows)
    if ranges is None:
        return table
    keep = [i for i, n in enumerate(table["numbers"]) if any(a <= n and (b is None or n <= b) for a, b in ranges)]
    if not keep:
        raise ValueError(f'No rows match "{rows}". {table["name"]} has rows {table["numbers"][0]}-{table["numbers"][-1]}.')
    return table | {"rows": [table["rows"][i] for i in keep], "numbers": [table["numbers"][i] for i in keep]}
