import datetime
import openpyxl
from pathlib import Path
import pandas as pd
from .text import read_text
import csv
import io
import xlrd  

HEADER_SEARCH_ROWS = 10 #the number of rows in which we search for a header

def cell_to_str(value)->str:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime) and value.time() == datetime.time(0, 0):#in some version if we type a date time 00:00:00 automatically appends
        return str(value.date())#this only keeps the date
    if isinstance(value,float) and value.is_integer():
        return str(int(value))
    return str(value).replace("\n", " / ").strip()#a cell can have multiply lines of text now we indicate it with /


def plain_row(cells: list[str]) -> str:
    filled = []
    for c in cells:
        if c:
            filled.append(c)
    return " | ".join(filled)


def find_header(rows: list[list[str]]) -> int:
    counts = []
    for row in rows[:HEADER_SEARCH_ROWS]:
        counts.append(len([c for c in row if c]))#header is the row with the most (not empty) cells in the top HEADER_SEARCH_ROWS rows
    return counts.index(max(counts))


def format_rows(rows)->str:
    clean=[]
    for row in rows:#it removes empty cells
        cells=[]
        for c in row:
            cells.append(cell_to_str(c))
        if any(cells):
            clean.append(cells)
    if not clean:
        return ""
    h = find_header(clean)
    if len([c for c in clean[h] if c]) < 2:#less than 2 cells not a real header so we just return without them
        lines = []
        for row in clean:
            lines.append(plain_row(row))
        return "\n".join(lines)
    lines = []
    for row in clean[:h]:#everything above the headers dont have them
        lines.append(plain_row(row))
    header=[]
    for i,name in enumerate(clean[h]):
        if not name:
            header.append(f"col{i+1}")#if a header is empty we keep its location
        else:
            header.append(name)
    data_rows = clean[h + 1:]
    if not data_rows:
        lines.append(plain_row(clean[h]))#if we dont have data then we return only the headers and whats above
    for row in data_rows:
        parts = []
        for name, value in zip(header, row):#we zip the header with the cell's value
            if value:
                parts.append(f"{name}: {value}")
        if parts:
            lines.append("; ".join(parts))
    return "\n".join(lines)

def read_xlsx(path: Path)->str:
    book=openpyxl.load_workbook(path,read_only=True,data_only=True)#for macros we only want the output,data_only=True does that
    sheets=[]
    for sheet in book.worksheets:
        rows=list(sheet.iter_rows(values_only=True))#we remove data like colour,font,etc. from each
        sheets.append((sheet.title,rows))
    book.close()
    return format_sheets(sheets)


def format_sheets(sheets):#for multiple pages
    blocks=[]
    for title,lines in sheets:
        text=format_rows(lines)
        if text:
            blocks.append(f"# Sheet: {title}\n{text}")
    return "\n\n".join(blocks)

def read_ods(path: Path)->str:
    tables=pd.read_excel(path,engine="odf",sheet_name=None,header=None,dtype=str,keep_default_na=False)
    sheets=[]
    for name,df in tables.items():
        sheets.append((name,df.values.tolist()))
    return format_sheets(sheets)

def read_csv(path: Path)->str:#normal text with seperators
    text=read_text(path)
    if path.suffix.lower()==".tsv":
        sep="\t"
    else:
        try:
            sep=csv.Sniffer().sniff(text[:5000],delimiters=",;\t|").delimiter#reads first 5k lines and guesses the seperator from the ,;\t| pool
        except csv.Error:
            sep=","
    rows=list(csv.reader(io.StringIO(text),delimiter=sep))
    return format_rows(rows)

def read_xls(path: Path)->str:
    book=xlrd.open_workbook(str(path))
    sheets=[]
    for sheet in book.sheets():
        rows=[]
        for r in range(sheet.nrows):
            values=sheet.row_values(r)
            types=sheet.row_types(r)
            row=[]
            for value,kind in zip(values,types):#this format saves dates as days passed from 1900
                if kind==xlrd.XL_CELL_DATE:#so if we find a date
                    value=xlrd.xldate_as_datetime(float(value),book.datemode)#we convert it
                row.append(value)
            rows.append(row)
        sheets.append((sheet.name,rows))
    book.release_resources()
    return format_sheets(sheets)

