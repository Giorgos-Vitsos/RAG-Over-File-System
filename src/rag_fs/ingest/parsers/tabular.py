import datetime
import openpyxl
from pathlib import Path
import pandas as pd
from .text import read_text
import csv
import io
import xlrd  
import re
import zipfile
from openpyxl.worksheet.cell_range import CellRange
from ..file_scanner import MB

HEADER_SEARCH_ROWS = 10 #the number of rows in which we search for a header

def cell_to_str(value)->str:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime) and value.time() == datetime.time(0, 0):#in some version if we type a date time 00:00:00 automatically appends
        return str(value.date())#this only keeps the date
    if isinstance(value,float) and value.is_integer():
        return str(int(value))
    return str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", " / ").strip()#a cell can have multiply lines of text now we indicate it with /

def fill_merged(rows, merges):
    filled=[list(row) for row in rows]#we make list from tuple to modify value
    for top,left,bottom,right in merges:
        value=filled[top][left]#the value of the merge is in its top left cell
        last=min(bottom,len(filled)-1)#a merge cant go past the last row
        for r in range(top,last+1):
            row=filled[r]
            while len(row)<=right:#we create empty cells
                row.append(None)
            for c in range(left,right+1):#we add the value
                row[c]=value
    return filled


def all_same(cells: list[str]) -> bool:
    filled=[c for c in cells if c]
    return len(filled)>=2 and len(set(filled))==1

def plain_row(cells: list[str]) -> str:
    filled = []
    for c in cells:
        if c:
            filled.append(c)
    if all_same(filled):#if dupe we write only one
        return filled[0]
    return " | ".join(filled)


def find_header(rows: list[list[str]]) -> int:
    counts = []
    for row in rows[:HEADER_SEARCH_ROWS]:
        if all_same(row):#a merged row counts as one cell
            counts.append(1)
        else:
            counts.append(len([c for c in row if c]))
    h=counts.index(max(counts))
    widest=counts[h]#we find the row with the most (not empty) cells from the first HEADER_SEARCH_ROWS rows
    first_data=-1#keeps the index of the first data row (not tile or header)
    for i in range(len(counts)):
        if any(looks_like_value(cell) for cell in rows[i]):
            first_data=i
            break
    if first_data==-1:#if we dont find data (numbers)
        i=h-1#then we search from widest and above for a pivot row
        while i>=0:
            if counts[i]>=widest-1 and is_corner_header(rows,i):#if we found one then thats probabably the header
                return i
            i=i-1
        return h
    if is_corner_header(rows,first_data):#a pivot with values in its header
        return first_data
    best=-1#otherwise we find the best match
    i=first_data-1
    while i>=0:
        if counts[i]>=widest-1 and (best==-1 or counts[i]>counts[best]):
            best=i
        i=i-1
    if best==-1:
        return h
    return best

def is_corner_header(rows: list[list[str]], i: int) -> bool:
    row=rows[i]#the row 
    filled=[c for c in row[1:] if c]#the cells after the empty corner
    if row[0] or len(filled)<2:#if the corner is not empty then its not pivot
        return False
    if i+1>=len(rows):#its the last row so not pivot
        return False
    below=rows[i+1]
    return bool(below[0]) and any(c and not looks_like_value(c) for c in below)

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
        if len(filled_cells) < 2 or looks_like_value(clean[h][0]) or (all(looks_like_value(cell) for cell in filled_cells) and not is_corner_header(clean,h)):#less than 2 cells or only values in a not pivot are not a real header so we just return without them
            lines = []
            for row in clean:
                lines.append(plain_row(row))
            return "\n".join(lines)
    lines = []
    for row in clean[:first]:#everything above the header has no column names
        lines.append(plain_row(row))
    header=[]
    named=[]#the columns that have a real name
    max_width=max(len(row) for row in clean)#the longest row
    for i in range(max_width):#we fill the header list
        names=[]
        for row in clean[first:h+1]:#every header row
            if i<len(row) and row[i] and row[i] not in names:#we dont add dupe names
                names.append(row[i])
        if names:
            header.append(" - ".join(names))
            named.append(i)
        else:
            header.append(f"col{i+1}")#a column without a name keeps its position
    data_rows = clean[h + 1:]
    if not data_rows:
        for row in clean[first:h+1]:#if we dont have data then we return the header rows as text
            lines.append(plain_row(row))
    used=set()#the columns that got at least one value
    for row in data_rows:
        if all_same(row):#a merged section row is not a value of the columns
            lines.append(plain_row(row))
            continue
        parts = []
        for i, value in enumerate(row):
            if value:
                parts.append(f"{header[i]}: {value}")
                used.add(i)
        if parts:
            lines.append("; ".join(parts))
    unused=[header[i] for i in named if i not in used]
    if data_rows and unused:#names of columns without any value would be lost
        lines.append(" | ".join(unused))
    return "\n".join(lines)

MERGE_CELL=re.compile(rb'<(?:\w+:)?mergeCell ref="([^"]+)"')#meged cells are tagged with this regex

def xlsx_merges(path: Path, sheet) -> list[tuple[int,int,int,int]]:
    found=set()#we keep each tag only once
    with zipfile.ZipFile(path) as z, z.open(sheet._worksheet_path) as f:#the xml of the sheet inside the zip
        tail=b""
        while True:
            chunk=f.read(MB)#we only read a mb to not tank the memory
            if not chunk:#when the file ends
                break
            data=tail+chunk#if it was broken because of the chunking we recreate it
            for ref in MERGE_CELL.findall(data):
                found.add(ref.decode())
            tail=data[-100:]#we keep the last part in case we split it by accident
    merges=[]
    for ref in found:#we make the extracted tags readable for our function
        cr=CellRange(ref)
        merges.append((cr.min_row-1,cr.min_col-1,cr.max_row-1,cr.max_col-1))
    return merges

def read_xlsx(path: Path)->str:
    book=openpyxl.load_workbook(path,read_only=True,data_only=True)#for macros we only want the output,data_only=True does that
    sheets=[]
    for sheet in book.worksheets:
        rows=fill_merged(list(sheet.iter_rows(values_only=True)),xlsx_merges(path,sheet))#we remove data like colour,font,etc. from each and we merge if needed
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
    book=xlrd.open_workbook(str(path),formatting_info=True)#we need formatting for merged cells
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
        merges=[]
        for rlo,rhi,clo,chi in sheet.merged_cells:#different merge formatting
            merges.append((rlo,clo,rhi-1,chi-1))    
        sheets.append((sheet.name,fill_merged(rows,merges)))
    book.release_resources()
    return format_sheets(sheets)

