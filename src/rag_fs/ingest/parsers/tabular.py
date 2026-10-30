import openpyxl
from pathlib import Path
from .tables import cell_to_str,fill_merged,format_rows,format_sheets
from .text import read_text,with_address
import csv
import io
import xlrd
import re
import zipfile
from openpyxl.worksheet.cell_range import CellRange
from ..filetypes import MB
import xml.etree.ElementTree as ET

MERGE_CELL=re.compile(rb'<(?:\w+:)?mergeCell ref="([^"]+)"')#meged cells are tagged with this regex
HYPERLINK=re.compile(rb'<(?:\w+:)?hyperlink\b[^>]*>')#link tag of a cell
LINK_REF=re.compile(rb'\bref="([^"]+)"')#the cell of a link
LINK_ID=re.compile(rb'\br:id="([^"]+)"')#the name of the address in the .rels file
TAIL=4096#bytes kept from the last chunk

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

def read_xlsx(path: Path)->str:
    book=openpyxl.load_workbook(path,read_only=True,data_only=True)#for macros we only want the output,data_only=True does that
    sheets=[]
    for sheet in book.worksheets:
        rows=[]
        for row in sheet.iter_rows(values_only=True):
            rows.append(list(row))
        for (r,c),address in xlsx_links(path,sheet).items():
            if r<len(rows) and c<len(rows[r]) and rows[r][c] is not None:#if we have text other than the link
                rows[r][c]=with_address(cell_to_str(rows[r][c]),address)
        rows=fill_merged(rows,xlsx_merges(path,sheet))
        sheets.append((sheet.title,rows,0))
    book.close()
    return format_sheets(sheets)

def xlsx_merges(path: Path, sheet) -> list[tuple[int,int,int,int]]:
    with zipfile.ZipFile(path) as z:
        found=sheet_tags(z,sheet._worksheet_path,MERGE_CELL)
    merges=[]
    for ref in found:#we make the extracted tags readable for our function
        cr=CellRange(ref.decode())
        merges.append((cr.min_row-1,cr.min_col-1,cr.max_row-1,cr.max_col-1))
    return merges

def xlsx_links(path: Path, sheet) -> dict[tuple[int,int],str]:
    found=set()
    with zipfile.ZipFile(path) as z:
        for tag in sheet_tags(z,sheet._worksheet_path,HYPERLINK):
            ref=LINK_REF.search(tag)
            rid=LINK_ID.search(tag)
            if ref and rid:#a valid link outside the file
                found.add((ref.group(1).decode(),rid.group(1).decode()))
        folder,name=sheet._worksheet_path.rsplit("/",1)
        rels_path=f"{folder}/_rels/{name}.rels"#here lives the relations of the file
        addresses={}
        if rels_path in z.namelist():#if relations exists
            for rel in ET.fromstring(z.read(rels_path)):
                if rel.get("TargetMode")=="External":
                    addresses[rel.get("Id")]=rel.get("Target")
    links={}
    for ref,rid in found:
        if rid in addresses:
            cell=CellRange(ref)
            links[(cell.min_row-1,cell.min_col-1)]=addresses[rid]
    return links

def sheet_tags(z:zipfile.ZipFile,sheet_path:str,pattern:re.Pattern[bytes])->set[bytes]:#every match of pattern in the xml of a sheet
    found=set()#we keep each tag only once
    with z.open(sheet_path) as f:#the xml of the sheet inside the zip
        tail=b""#helps us recover broken data if it was cut
        while True:
            chunk=f.read(MB)#we read in chunks to not overload memory
            if not chunk:#when the file ends
                break
            data=tail+chunk#if it was broken because of the chunking we recreate it
            found.update(pattern.findall(data))
            tail=data[-TAIL:]#we keep the last part in case we split it by accident
    return found

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
        sheets.append((sheet.name,fill_merged(rows,merges),0))
    book.release_resources()
    return format_sheets(sheets)
