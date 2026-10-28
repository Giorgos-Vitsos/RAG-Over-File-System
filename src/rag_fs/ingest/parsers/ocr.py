from ...config import OCRConfig
from pathlib import Path
import pytesseract
from PIL import Image, ImageOps
import re
from collections import Counter
import unicodedata

LIGATURE=re.compile("[\ufb00-\ufb06]")#one character for two or three letters
BROKEN_WORD=re.compile(r"(\w+)-\n(\w+)")#a word cut by a hyphen at the end of a line
HYPHENATED_WORD=re.compile(r"\w+(?:-\w+)*")#a word together with its hyphens
NUMBER=re.compile(r"\d+")
LOOKALIKE = str.maketrans("ABEZHIKMNOPTYXoviukxap", "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧονιυκχαρ")
WORD = re.compile(r"\w+")
OCR_SAMPLE=10

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
    original=parse_ocr_image(image,ocr)
    if original[1]>=ocr.min_confidence:
        return original
    rotated=parse_ocr_image(image.rotate(180),ocr)#we try to rotate to be read better
    if rotated[1]>original[1]:#we keep the best
        return rotated
    return original

def parse_ocr_image(image,ocr:OCRConfig)->tuple[str,float]:
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
        text_by_page={}
        if path.suffix.lower() in (".tif", ".tiff"):
            frames_count=getattr(img, "n_frames", 1)#we keep multiple frames
        else:
            frames_count=1
        candidates=[]#for sampling
        for index in range(frames_count):
            img.seek(index)
            if min(img.size)<ocr.min_image_size:
                continue
            candidates.append(index)
        samples,rest=ocr_sample(candidates)
        for group in (samples,rest):
            if group is rest and not text_by_page:#if it failed with the samples we dont bother
                break
            for index in group:
                img.seek(index)
                curr_frame=ImageOps.exif_transpose(img)
                if curr_frame.mode.startswith("I;16"):
                    curr_frame=curr_frame.convert("I").point(lambda v: v * (1 / 256)).convert("L")
                rgba = curr_frame.convert("RGBA")#we convert all images to white background with bold text so its more visible
                page = Image.new("RGB", rgba.size, "white")
                page.paste(rgba, mask=rgba.getchannel("A"))
                text=run_ocr(page,ocr)
                if text is None:
                    continue
                text_by_page[index]=text
        keys_sorted=sorted(text_by_page)
        ocr_results=[]
        for i in keys_sorted:
            ocr_results.append(text_by_page[i])
        pages=[]
        for text in ocr_results:
            pages.append(text.split("\n\n"))
        pages,titles=drop_repeated_edges(pages)#we remove headers that repeat
        for page_index,text in reversed(titles):#it keeps the correct order
            pages[page_index].insert(0,text)#we add the titles
        pages_normalized=[]
        for page in pages:#we normalize to match other file types
            temp=[]
            for paragraph in page:
                temp.append((paragraph,None))#get_words needs tuple with the rows in case its a table, but here is none
            pages_normalized.append(temp)
        words=get_words(pages_normalized)
        pages_new=[]
        for page in pages:
            temp=[]
            for paragraph in page:
                temp.append(fix_text_block(paragraph,words))
            pages_new.append(temp)
        pages=pages_new
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

def get_words(pages:  list[list[tuple[str,list[list[str]]|None]]])->set[str]:#it returns all words other than "-\n" connected
    words=set()
    for blocks in pages:
        for text,rows in blocks:
            if rows is None:
                parts=[text]
            else:
                parts=[]
                for row in rows:
                    for cell in row:
                        parts.append(cell)
            for part in parts:
                whole=BROKEN_WORD.sub(" ",part)
                for word in HYPHENATED_WORD.findall(whole):
                    words.add(word.lower())
    return words

def fix_text_block(block:str,words:set[str])->str:
    block=BROKEN_WORD.sub(lambda match: join_broken(match.group(1),match.group(2),words),block)#fixes broken words "-"
    block=LIGATURE.sub(lambda match: unicodedata.normalize("NFKC",match.group()),block)#fixes combined symbols
    return " ".join(block.split())

def join_broken(word1:str,word2:str,words:set[str])->str:
    joined=word1+word2
    hyphenated=word1+"-"+word2
    if not word2[0].islower():#if the second part starts with capital then definitly not a broken word
        return hyphenated
    if hyphenated.lower() in words and joined.lower() not in words:
        return hyphenated
    return joined

def ocr_sample(candidates:list[int])->tuple[list[int],list[int]]:
    samples=[]
    rest=[]
    if len(candidates)<=OCR_SAMPLE:
        samples=candidates
        return samples,rest
    for i in range(OCR_SAMPLE):
        samples.append(candidates[i*len(candidates)//OCR_SAMPLE])
    for candidate in candidates:
        if candidate not in samples:
            rest.append(candidate)
    return samples,rest

def run_ocr(image,ocr:OCRConfig)->str|None:
    text_ocr,confidence=ocr_image(image,ocr)
    if confidence<ocr.min_confidence or len(text_ocr.strip())<ocr.min_chars:#ocr is not confident source
        return None
    return text_ocr
