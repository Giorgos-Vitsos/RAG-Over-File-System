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
    return str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", " / ").strip()#a cell can have multiply lines of text now we indicate it with /


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
    h=counts.index(max(counts))
    widest=counts[h]#we find the row with the most (not empty) cells from the first HEADER_SEARCH_ROWS rows
    first_data=-1#keeps the index of the first data row (not tile or header)
    for i in range(len(counts)):
        if any(looks_like_value(cell) for cell in rows[i]):
            first_data=i
            break
    if first_data==-1: #if we have no data then the first widest is probably the header
        return h
    best=-1#otherwise we find the best match
    i=first_data-1
    while i>=0:#from the data row and above we look for the row that best matches
        if counts[i]>=widest-1 and (best==-1 or counts[i]>counts[best]):
            best=i
        i=i-1
    if best==-1:
        return h
    return best

def looks_like_value(s:str)->bool:
    return any(ch.isdigit() for ch in s) and not any(ch.isalpha() for ch in s)

def format_rows(rows, header_rows: int = 0)->str:
    clean=[]
    for row in rows:#it removes empty rows
        cells=[]
        for c in row:
            cells.append(cell_to_str(c))
        if any(cells):
            clean.append(cells)
    if not clean:
        return ""
    if header_rows>0:#the file says which rows are the header
        first=0
        h=min(header_rows,len(clean))-1
    else:#otherwise we guess it
        h=find_header(clean)
        first=h#we only have 1 header here and its what we found
        filled_cells=[c for c in clean[h] if c]
        if len(filled_cells) < 2 or all(looks_like_value(cell) for cell in filled_cells):#less than 2 cells or only values are not a real header so we just return without them
            lines = []
            for row in clean:
                lines.append(plain_row(row))
            return "\n".join(lines)
    lines = []
    for row in clean[:first]:#everything above the header has no column names
        lines.append(plain_row(row))
    header=[]
    max_width=max(len(row) for row in clean)#the longest row
    for i in range(max_width):#we fill the header list
        names=[]
        for row in clean[first:h+1]:#every header row
            if i<len(row) and row[i] and row[i] not in names:#we dont add dupe names
                names.append(row[i])
        if names:
            header.append(" - ".join(names))
        else:
            header.append(f"col{i+1}")#a column without a name keeps its position
    data_rows = clean[h + 1:]
    if not data_rows:
        for row in clean[first:h+1]:#if we dont have data then we return the header rows as text
            lines.append(plain_row(row))
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

