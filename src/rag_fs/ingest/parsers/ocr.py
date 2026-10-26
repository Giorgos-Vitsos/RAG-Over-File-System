from ...config import OCRConfig
from pathlib import Path
import pytesseract
from PIL import Image, ImageOps, ImageSequence
import re
from collections import Counter

NUMBER=re.compile(r"\d+")
LOOKALIKE = str.maketrans("ABEZHIKMNOPTYXoviukxap", "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧονιυκχαρ")
WORD = re.compile(r"\w+")

def check_ocr(ocr: OCRConfig):
    if not ocr.languages:
        raise ValueError("No OCR language in config")
    if ocr.tessdata_dir:
        path=Path(ocr.tessdata_dir).expanduser()
        flag=f'--tessdata-dir "{path}"'
        if not path.is_dir():
            raise ValueError(f"No OCR data folder with name {path}")
    else:
        flag=""  
    installed_lang=pytesseract.get_languages(flag)
    if installed_lang:
        for lang in ocr.languages:
            if lang not in installed_lang:
                raise ValueError(f"{lang} is not installed for OCR")
    else:
        raise ValueError("OCR has none installed languages")

def ocr_image(image,ocr:OCRConfig)->tuple[str,float]:
    paragraphs={}
    confidences=[]
    if ocr.tessdata_dir:
        dir_flag=f'--tessdata-dir "{Path(ocr.tessdata_dir).expanduser()}"'
    else:
        dir_flag=""
    data=pytesseract.image_to_data(image,"+".join(ocr.languages),dir_flag,output_type=pytesseract.Output.DICT)
    for i in range(len(data["text"])):
        if not data["text"][i].strip() or data["conf"][i]==-1:
            continue
        paragraphs.setdefault((data["block_num"][i],data["par_num"][i]),{}).setdefault(data["line_num"][i],[]).append(data["text"][i])
        confidences.append(data["conf"][i])
    blocks=[]
    for paragraph in paragraphs.values():
        lines=[]
        for line in paragraph.values():
            lines.append(" ".join(line))
        blocks.append("\n".join(lines))
    output="\n\n".join(blocks)
    if len(confidences)==0:
        mean=0
    else:
        mean=sum(confidences)/len(confidences)
    return output,mean

def read_image(path:Path,ocr:OCRConfig)->tuple[str,bool]:
    with Image.open(path) as img:
        ocr_results=[]
        if path.suffix.lower() in (".tif", ".tiff"):
            frames=ImageSequence.Iterator(img)
        else:
            frames=[img]
        for frame in frames:
            frame=ImageOps.exif_transpose(frame)
            if min(frame.size)<ocr.min_image_size:
                continue
            if frame.mode.startswith("I;16"):
                frame=frame.convert("I").point(lambda v: v * (1 / 256)).convert("L")
            rgba = frame.convert("RGBA")#we convert all images to white background with bold text so its more visible
            page = Image.new("RGB", rgba.size, "white")
            page.paste(rgba, mask=rgba.getchannel("A"))
            text,confidence=ocr_image(page,ocr)
            if confidence<ocr.min_confidence or len(text.strip())<ocr.min_chars:
                continue
            ocr_results.append(text)
        pages=[]
        for text in ocr_results:
            pages.append(text.split("\n\n"))
        pages,titles=drop_repeated_edges(pages)#we remove headers that repeat
        for page_index,text in reversed(titles):#it keeps the correct order
            pages[page_index].insert(0,text)#we add the titles
        clean_ocr_result=[]
        for paragraphs in pages:
            if paragraphs:
                clean_ocr_result.append("\n\n".join(paragraphs))
        ocr_results=clean_ocr_result
        if ocr_results:
            return "\n\n".join(ocr_results),True
        else:
            return "",False

def edge_key(text: str)->str:
    return NUMBER.sub("#"," ".join(text.split()).lower())#different pages give the same key if the dig is the only difference

def drop_repeated_edges(pages:list[list[str]])->tuple[list[list[str]],list[tuple[int,str]]]:
    if len(pages)<3:#not enough pages to find headers
        return pages,[]
    counter=Counter()
    variants={}#keeps all the variants of repeated text
    for paragraphs in pages:
        if len(paragraphs)<2:
            continue
        keys=set()
        for index,text in enumerate(paragraphs):
            if index<2 or index >=len(paragraphs)-2:
                keys.add(edge_key(text))
                variants.setdefault(edge_key(text),set()).add(" ".join(text.split()).lower())
        counter.update(keys)
    repeated=set()
    for key,count in counter.items():
        if count>=len(pages)/2:
            repeated.add(key)
    output=[]
    titles=[]
    seen=set()
    for page_index,paragraphs in enumerate(pages):
        kept=[]
        for index,text in enumerate(paragraphs):
            if edge_key(text) in repeated and (index<2 or index >=len(paragraphs)-2):#we drop edges that repeat
                if len(variants[edge_key(text)])==1 and edge_key(text) not in seen:#if it has no variants then its probably a title
                    seen.add(edge_key(text))
                    titles.append((page_index,text))
                continue
            kept.append(text)
        output.append(kept)
    return output,titles

def is_greek(c: str) -> bool:
    return "\u0370" <= c <= "\u03ff" or "\u1f00" <= c <= "\u1fff"

def fix_lookalikes(text:str)->str:
    words=set()
    for w in WORD.findall(text):
        w=w.lower()
        if any(is_greek(c) for c in w):
            words.add(w)
    return WORD.sub(lambda match: fix_word(match.group(0),words),text)#fixes greek words broken from ocr

def fix_word(word:str,words:set[str])->str:
    has_greek_char=False
    for c in word:
        if is_greek(c):
            has_greek_char=True
    greek=word.translate(LOOKALIKE)
    if greek.lower() in words or has_greek_char:
        return greek
    return word
