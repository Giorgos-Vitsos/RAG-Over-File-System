from ...config import OCRConfig
from pathlib import Path
import pytesseract
from PIL import Image, ImageOps, ImageSequence

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
        if ocr_results:
            return "\n\n".join(ocr_results),True
        else:
            return "",False
