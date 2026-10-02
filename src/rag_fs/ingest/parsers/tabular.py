import datetime
import openpyxl
from pathlib import Path
import pandas as pd
from .text import read_text
import csv
import io

HEADER_SEARCH_ROWS = 10


def cell_to_str(value)->str:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime) and value.time() == datetime.time(0, 0):
        return str(value.date())
    return str(value).replace("\n", " / ").strip()


def plain_row(cells: list[str]) -> str:
    filled = []
    for c in cells:
        if c:
            filled.append(c)
    return " | ".join(filled)


def find_header(rows: list[list[str]]) -> int:
    counts = []
    for row in rows[:HEADER_SEARCH_ROWS]:
        counts.append(len([c for c in row if c]))
    return counts.index(max(counts))


def format_rows(rows)->str:
    clean=[]
    for row in rows:
        cells=[]
        for c in row:
            cells.append(cell_to_str(c))
        if any(cells):
            clean.append(cells)
    if not clean:
        return ""
    h = find_header(clean)
    if len([c for c in clean[h] if c]) < 2:
        lines = []
        for row in clean:
            lines.append(plain_row(row))
        return "\n".join(lines)
    lines = []
    for row in clean[:h]:
        lines.append(plain_row(row))
    header=[]
    for i,name in enumerate(clean[h]):
        if not name:
            header.append(f"col{i+1}")
        else:
            header.append(name)
    data_rows = clean[h + 1:]
    if not data_rows:
        lines.append(plain_row(clean[h]))
    for row in data_rows:
        parts = []
        for name, value in zip(header, row):
            if value:
                parts.append(f"{name}: {value}")
        if parts:
            lines.append("; ".join(parts))
    return "\n".join(lines)

def read_xlsx(path: Path)->str:
    book=openpyxl.load_workbook(path,read_only=True,data_only=True)
    sheets=[]
    for sheet in book.worksheets:
        rows=list(sheet.iter_rows(values_only=True))
        sheets.append((sheet.title,rows))
    book.close()
    return format_sheets(sheets)


def format_sheets(sheets):
    blocks=[]
    for title,line in sheets:
        text=format_rows(line)
        if text:
            blocks.append(f"# Sheet: {title}\n{text}")
    return "\n\n".join(blocks)

def read_ods(path: Path)->str:
    tables=pd.read_excel(path,engine="odf",sheet_name=None,header=None,dtype=str,keep_default_na=False)
    sheets=[]
    for name,df in tables.items():
        sheets.append((name,df.values.tolist()))
    return format_sheets(sheets)

def read_csv(path: Path)->str:
    text=read_text(path)
    if path.suffix.lower()==".tsv":
        sep="\t"
    else:
        try:
            sep=csv.Sniffer().sniff(text[:5000],delimiters=",;\t|").delimiter
        except csv.Error:
            sep=","
    rows=list(csv.reader(io.StringIO(text),delimiter=sep))
    return format_rows(rows)
