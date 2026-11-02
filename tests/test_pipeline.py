import io
import zipfile

import pytest

from rag_fs.config import CorpusConfig, OCRConfig
from rag_fs.ingest.cache import drop_cached, open_cache
from rag_fs.ingest.parsers import PARSERS
from rag_fs.ingest.parsers.text import read_text
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


# piece C2: read_corpus with the cache (D95)
# the readers of .txt and .md count the files they really read
@pytest.fixture
def reads(monkeypatch):
    names = []
    for ext, reader in ((".txt", PARSERS[".txt"]), (".md", PARSERS[".md"])):
        def counted(path, reader=reader):
            names.append(path.name)
            return reader(path)
        monkeypatch.setitem(PARSERS, ext, counted)
    return names


def write(root, files):
    for name, data in files.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


# one run over the corpus with the cache: (path, status, text) of every document
def run(root, db):
    return [(str(d.file_path), d.parse_status, d.raw_text) for d in read_corpus(CorpusConfig(roots=[root]), db=db)]


@pytest.fixture
def db(tmp_path):
    return open_cache(tmp_path / "data" / "cache.db")


# the second run reads nothing and gives the same documents
def test_second_run_reads_nothing(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES, "a/b.md": "# Title\n\nText.", "main.py": CODE})
    first = run(root, db)
    assert sorted(reads) == ["b.md", "notes.txt"]
    reads.clear()
    assert run(root, db) == first
    assert reads == []


# the same documents with and without the cache, field by field
def test_cached_documents_are_the_same(tmp_path, db):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES, "work.zip": zip_bytes({"a/in.txt": "Μέσα."})})
    plain = read_corpus(CorpusConfig(roots=[root]))
    read_corpus(CorpusConfig(roots=[root]), db=db)
    assert read_corpus(CorpusConfig(roots=[root]), db=db) == plain


# a file that changed is read again, the others not
def test_changed_file_is_read_again(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES, "other.txt": "Άλλο."})
    run(root, db)
    reads.clear()
    write(root, {"notes.txt": "Νέες σημειώσεις."})
    assert run(root, db) == [("notes.txt", "ok", "Νέες σημειώσεις."), ("other.txt", "ok", "Άλλο.")]
    assert reads == ["notes.txt"]


# a file that moved is not read again, and its document has the new path
def test_moved_file_is_not_read_again(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES})
    run(root, db)
    reads.clear()
    (root / "notes.txt").rename(root / "moved.txt")
    assert run(root, db) == [("moved.txt", "ok", NOTES)]
    assert reads == []


# the same file in two places is read once and gives two documents, one for each path (D95)
def test_same_file_twice_is_read_once(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"a/notes.txt": NOTES, "b/copy.txt": NOTES})
    assert run(root, db) == [("a/notes.txt", "ok", NOTES), ("b/copy.txt", "ok", NOTES)]
    assert len(reads) == 1


# the same bytes with another extension are read again: .md is read differently
def test_same_bytes_other_extension_is_read(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": "# Title", "notes.md": "# Title"})
    run(root, db)
    assert sorted(reads) == ["notes.md", "notes.txt"]


# the files inside an archive go through the cache too: the archive is opened again, its files are not read again
def test_files_inside_archives_are_cached(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"work.zip": zip_bytes({"a/notes.txt": NOTES})})
    first = run(root, db)
    assert first == [("work.zip/a/notes.txt", "ok", NOTES)]
    reads.clear()
    assert run(root, db) == first
    assert reads == []


# a failure is not kept: the next run tries again (the parser may be fixed by then)
def test_failed_file_is_tried_again(tmp_path, db, monkeypatch):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES})
    def broken(path):
        raise ValueError("broken")
    monkeypatch.setitem(PARSERS, ".txt", broken)
    assert run(root, db) == [("notes.txt", "failed", "")]
    monkeypatch.setitem(PARSERS, ".txt", read_text)
    assert run(root, db) == [("notes.txt", "ok", NOTES)]


# --rebuild .txt: only the .txt files are read again
def test_rebuild_reads_the_dropped_extensions_again(tmp_path, db, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES, "b.md": "# Title"})
    run(root, db)
    reads.clear()
    drop_cached(db, [".txt"])
    run(root, db)
    assert reads == ["notes.txt"]


# what one run read is in the cache for the next one, also when the next run opens the cache again
def test_cache_survives_between_runs(tmp_path, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES})
    path = tmp_path / "data" / "cache.db"
    run(root, open_cache(path))
    reads.clear()
    assert run(root, open_cache(path)) == [("notes.txt", "ok", NOTES)]
    assert reads == []


# without a cache read_corpus reads everything, as before
def test_without_cache_everything_is_read(tmp_path, reads):
    root = tmp_path / "corpus"
    write(root, {"notes.txt": NOTES})
    read_corpus(CorpusConfig(roots=[root]))
    read_corpus(CorpusConfig(roots=[root]))
    assert reads == ["notes.txt", "notes.txt"]
