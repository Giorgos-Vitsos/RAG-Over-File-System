import pymupdf
import pytesseract
import pytest
from PIL import Image

from rag_fs.config import OCRConfig
from rag_fs.ingest.parsers.ocr import check_ocr, ocr_image


# a white picture with the html written on it, like a scanned page
def picture(html, width=1000, height=300, mode="RGB"):
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    if html:
        page.insert_htmlbox(pymupdf.Rect(30, 30, width - 30, height - 30), f"<div style='font-size:22px'>{html}</div>")
    pix = page.get_pixmap(dpi=200)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert(mode)


# (text written on the picture, text that comes out)
READ_CASES = {
    "english": ("The deadline is strict.", "The deadline is strict."),
    "greek with accents": ("Οι εργασίες παραδίδονται στις 12 Μαρτίου.", "Οι εργασίες παραδίδονται στις 12 Μαρτίου."),
    "greek and english in one line": ("Οι ασκήσεις του εργαστηρίου είναι σε Java.", "Οι ασκήσεις του εργαστηρίου είναι σε Java."),
    "lines of a paragraph": ("Πρώτη σειρά<br>δεύτερη σειρά", "Πρώτη σειρά\nδεύτερη σειρά"),
    "two paragraphs": ("<p>Πρώτη παράγραφος</p><p style='margin-top:60px'>Δεύτερη παράγραφος</p>",
                       "Πρώτη παράγραφος\n\nΔεύτερη παράγραφος"),
}


# the text of the picture comes out with its lines and paragraphs
@pytest.mark.parametrize("html, expected", READ_CASES.values(), ids=READ_CASES.keys())
def test_ocr_image_reads_the_text(html, expected):
    text, confidence = ocr_image(picture(html), OCRConfig())
    assert text == expected
    assert confidence >= 80


# a picture with nothing on it gives no text and no confidence
def test_ocr_image_of_a_blank_picture():
    assert ocr_image(picture(""), OCRConfig()) == ("", 0.0)


# a grey picture is read like a colour one
def test_ocr_image_of_a_grey_picture():
    html, expected = READ_CASES["greek with accents"]
    assert ocr_image(picture(html, mode="L"), OCRConfig())[0] == expected


# the languages come from the config: without Greek the Greek words are lost
def test_ocr_image_uses_the_languages_of_the_config():
    text, _ = ocr_image(picture("Οι εργασίες παραδίδονται"), OCRConfig(languages=["eng"]))
    assert "εργασίες" not in text


# the models of the system (empty tessdata_dir) read the text too
def test_ocr_image_with_the_system_models():
    text, _ = ocr_image(picture("The deadline is strict."), OCRConfig(tessdata_dir=""))
    assert text == "The deadline is strict."


# the models come from the folder of the config: a folder without models cannot read
def test_ocr_image_uses_the_folder_of_the_config(tmp_path):
    with pytest.raises(pytesseract.TesseractError):
        ocr_image(picture("The deadline is strict."), OCRConfig(tessdata_dir=str(tmp_path)))


# the same picture always gives the same text
def test_ocr_image_is_deterministic():
    image = picture(READ_CASES["two paragraphs"][0])
    assert ocr_image(image, OCRConfig()) == ocr_image(image, OCRConfig())


# the default config and the system models are ready to use
@pytest.mark.parametrize("folder", ["~/.local/share/tessdata_best", ""], ids=["best models", "system models"])
def test_check_ocr_accepts_installed_languages(folder):
    check_ocr(OCRConfig(tessdata_dir=folder))


# a language or a folder that is not there stops before any file is read, and the message names it
@pytest.mark.parametrize("config, named", [
    pytest.param(dict(languages=["ell", "xyz"]), "xyz", id="missing language"),
    pytest.param(dict(tessdata_dir="~/no/such/folder"), "no/such/folder", id="missing folder"),
    pytest.param(dict(languages=[]), "language", id="no language"),
])
def test_check_ocr_rejects(config, named):
    with pytest.raises(ValueError, match=named):
        check_ocr(OCRConfig(**config))


# a folder that exists but has no models is rejected too
def test_check_ocr_rejects_an_empty_folder(tmp_path):
    with pytest.raises(ValueError, match="ell|language"):
        check_ocr(OCRConfig(tessdata_dir=str(tmp_path)))


# tesseract can give a box with only a space and a good confidence: it is not a word
def test_ocr_image_skips_boxes_without_text(monkeypatch):
    boxes = {"text": ["", " ", "Λέξη"], "conf": [-1, 95, 90], "block_num": [1, 1, 1], "par_num": [1, 1, 1], "line_num": [1, 1, 1]}
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *args, **kwargs: boxes)
    assert ocr_image(picture(""), OCRConfig()) == ("Λέξη", 90.0)
