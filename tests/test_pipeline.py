import io
import zipfile

import pytest

from rag_fs.config import CorpusConfig, OCRConfig
from rag_fs.ingest.pipeline import read_corpus

NOTES = "Σημειώσεις του μαθήματος για την εξέταση."
CODE = "def main():\n    print('γεια')\n"


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, text in files.items():
            z.writestr(name, text.encode("utf-8"))
    return buffer.getvalue()


# a corpus of {name: text or bytes} read whole; (path, status, text) of every document in the order they come
def read(tmp_path, files, **settings):
    root = tmp_path / "corpus"
    for name, data in files.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    docs = read_corpus(CorpusConfig(roots=[root], **settings))
    return [(str(d.file_path), d.parse_status, d.raw_text) for d in docs]


# every file of the corpus gives a document, and an archive gives the documents of the files inside it
def test_read_corpus_files_and_archives(tmp_path):
    files = {"notes.txt": NOTES, "src/main.py": CODE, "work.zip": zip_bytes({"a/notes.txt": "Άλλες σημειώσεις."})}
    assert sorted(read(tmp_path, files)) == [
        ("notes.txt", "ok", NOTES), ("src/main.py", "ok", CODE), ("work.zip/a/notes.txt", "ok", "Άλλες σημειώσεις.")]


# a file inside an archive that is also outside is read once, from outside, even when the archive comes first
def test_read_corpus_copy_outside_wins(tmp_path):
    files = {"a.zip": zip_bytes({"notes.txt": NOTES, "main.py": CODE}), "z.txt": NOTES}
    assert read(tmp_path, files) == [("a.zip/main.py", "ok", CODE), ("z.txt", "ok", NOTES)]


# the same archive twice is read once
def test_read_corpus_same_archive_twice(tmp_path):
    data = zip_bytes({"notes.txt": NOTES})
    assert read(tmp_path, {"a.zip": data, "b/copy.zip": data}) == [("a.zip/notes.txt", "ok", NOTES)]


# an archive deeper than the limit, counted from the archive of the corpus, is failed
def test_read_corpus_depth_of_archives(tmp_path):
    files = {"work.zip": zip_bytes({"notes.txt": NOTES}), "main.py": CODE}
    assert read(tmp_path, files, max_archive_depth=0) == [("main.py", "ok", CODE), ("work.zip", "failed", "")]


# the OCR settings are checked before anything is read
def test_read_corpus_checks_ocr(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "notes.txt").write_text(NOTES, encoding="utf-8")
    with pytest.raises(ValueError):
        read_corpus(CorpusConfig(roots=[root]), OCRConfig(languages=[]))


# the same broken archive twice is reported once
def test_read_corpus_same_broken_archive_twice(tmp_path):
    assert read(tmp_path, {"a.zip": NOTES, "b.zip": NOTES}) == [("a.zip", "failed", "")]
