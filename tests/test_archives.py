import datetime
import gzip
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import libarchive
import pytest

from rag_fs.config import CorpusConfig
from rag_fs.ingest.archives import read_archive
from rag_fs.ingest.file_scanner import scan

NOTES = "Σημειώσεις του μαθήματος για την εξέταση."
CODE = "def main():\n    print('γεια')\n"


# reads the archive `name` of a corpus where `make` writes it, with `skip` as the hashes already read
def read(tmp_path, name, make, skip=None):
    root = tmp_path / "corpus"
    f = root / name
    f.parent.mkdir(parents=True, exist_ok=True)
    make(f)
    config = CorpusConfig(roots=[root])
    scanned = next(s for s in scan(config).files if s.rel_path == Path(name))
    return read_archive(scanned, config, set() if skip is None else skip)


# (path, status, type, text) of every document, sorted by path
def summary(docs):
    return sorted((str(d.file_path), d.parse_status, d.content_type, d.raw_text) for d in docs)


def as_bytes(data):
    return data.encode("utf-8") if isinstance(data, str) else data


# writers of archives from {name: text or bytes}
def zip_of(files):
    def make(f):
        with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, as_bytes(data))
    return make


def tar_of(files, mode="w"):
    def make(f):
        with tarfile.open(f, mode) as t:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(as_bytes(data))
                info.mtime = 1588291200  # 2020-05-01 00:00 UTC
                t.addfile(info, io.BytesIO(as_bytes(data)))
    return make


def sevenzip_of(files):
    def make(f):
        with libarchive.file_writer(str(f), "7zip") as a:
            for name, data in files.items():
                a.add_file_from_memory(name, len(as_bytes(data)), as_bytes(data))
    return make


def gz_of(data):
    def make(f):
        f.write_bytes(gzip.compress(as_bytes(data)))
    return make


WORK = {"src/main.py": CODE, "notes.txt": NOTES}
WORK_READ = [("work{ext}/notes.txt", "ok", "prose", NOTES), ("work{ext}/src/main.py", "ok", "code", CODE)]


# (archive name, writer) every kind of archive gives the files inside it, with the archive in their path
@pytest.mark.parametrize("name, make", [
    ("work.zip", zip_of(WORK)),
    ("work.tar", tar_of(WORK)),
    ("work.tar.gz", tar_of(WORK, "w:gz")),
    ("work.tgz", tar_of(WORK, "w:gz")),
    ("work.7z", sevenzip_of(WORK)),
    ("work.ZIP", zip_of(WORK)),
], ids=["zip", "tar", "tar.gz", "tgz", "7z", "zip in capitals"])
def test_read_archive_kinds(tmp_path, name, make):
    ext = name.removeprefix("work")
    assert summary(read(tmp_path, name, make)) == [(p.format(ext=ext), s, t, x) for p, s, t, x in WORK_READ]


# the folder of the archive and greek names inside it are kept in the path
def test_read_archive_path_and_greek_names(tmp_path):
    docs = read(tmp_path, "μαθήματα/εργασία.zip", zip_of({"σημειώσεις/πρώτη.txt": NOTES}))
    assert summary(docs) == [("μαθήματα/εργασία.zip/σημειώσεις/πρώτη.txt", "ok", "prose", NOTES)]


# a lone file compressed with gzip is one file, named without the .gz
def test_read_archive_single_gz(tmp_path):
    assert summary(read(tmp_path, "notes.txt.gz", gz_of(NOTES))) == [("notes.txt.gz/notes.txt", "ok", "prose", NOTES)]


# the ignore list of folders and files applies inside archives too
def test_read_archive_ignore_list(tmp_path):
    files = {".git/config": "[core]", "__pycache__/main.cpython-312.pyc": b"\x00\x01", "Thumbs.db": b"\x00",
             "build.log": "log", "notes.txt": NOTES}
    assert summary(read(tmp_path, "work.zip", zip_of(files))) == [("work.zip/notes.txt", "ok", "prose", NOTES)]


# every document of an archive belongs to the root of the archive and has the hash of its own bytes
def test_read_archive_root_and_hash(tmp_path):
    docs = read(tmp_path, "work.zip", zip_of({"notes.txt": NOTES}))
    assert [(d.root_id, d.sha256, d.ext) for d in docs] == [("root_0", hashlib.sha256(NOTES.encode()).hexdigest(), ".txt")]


# the date of a file is the date written in the archive, not the time it was unpacked
def test_read_archive_dates(tmp_path):
    docs = read(tmp_path, "work.tar", tar_of({"notes.txt": NOTES}))
    assert [d.last_modified for d in docs] == [datetime.datetime(2020, 5, 1, tzinfo=datetime.timezone.utc).timestamp()]


# a file the scanner already read outside the archive is not read again, and what is read is added to the skip set
def test_read_archive_skips_files_read_before(tmp_path):
    skip = {hashlib.sha256(NOTES.encode()).hexdigest()}
    docs = read(tmp_path, "work.zip", zip_of(WORK), skip)
    assert summary(docs) == [("work.zip/src/main.py", "ok", "code", CODE)]
    assert hashlib.sha256(CODE.encode()).hexdigest() in skip


# two files with the same bytes in one archive: only the first by path is read
def test_read_archive_same_file_twice(tmp_path):
    docs = read(tmp_path, "work.zip", zip_of({"b/notes.txt": NOTES, "a/notes.txt": NOTES}))
    assert summary(docs) == [("work.zip/a/notes.txt", "ok", "prose", NOTES)]


# files the parsers cannot read come out as they would outside an archive
def test_read_archive_binary_file_inside(tmp_path):
    docs = read(tmp_path, "work.zip", zip_of({"Main.class": b"\xca\xfe\xba\xbe\x00", "notes.txt": NOTES}))
    assert summary(docs) == [("work.zip/Main.class", "unsupported", "binary", ""), ("work.zip/notes.txt", "ok", "prose", NOTES)]


# an archive with only folders, or with nothing, gives nothing
@pytest.mark.parametrize("make", [zip_of({}), zip_of({"empty/": b""})], ids=["empty", "only a folder"])
def test_read_archive_without_files(tmp_path, make):
    assert read(tmp_path, "work.zip", make) == []


# an archive of which no file can be read gives one failed document with its own path
@pytest.mark.parametrize("name, make", [
    ("work.zip", lambda f: f.write_bytes(zip_bytes(WORK)[:60])),
    ("work.zip", lambda f: f.write_text(NOTES, encoding="utf-8")),
    ("work.zip", lambda f: password_zip(f)),
], ids=["cut short", "not an archive", "with a password"])
def test_read_archive_that_fails(tmp_path, name, make):
    assert summary(read(tmp_path, name, make)) == [("work.zip", "failed", "binary", "")]


# a zip cut in the middle of its second file: the first file is read, the half of the second is not left behind,
# and the archive itself is failed so that the report shows it was not read whole
def test_read_archive_cut_after_the_first_file(tmp_path):
    files = {"a/notes.txt": NOTES, "b/long.txt": "Μια μεγάλη πρόταση με πολλές λέξεις. " * 4000, "c/last.txt": "τέλος"}
    data = zip_bytes(files, zipfile.ZIP_STORED)
    docs = read(tmp_path, "work.zip", lambda f: f.write_bytes(data[:len(data) // 2]))
    assert summary(docs) == [("work.zip", "failed", "binary", ""), ("work.zip/a/notes.txt", "ok", "prose", NOTES)]


def zip_bytes(files, method=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as z:
        for name, data in files.items():
            z.writestr(name, as_bytes(data))
    return buffer.getvalue()


def password_zip(f):
    with libarchive.file_writer(str(f), "zip", options="encryption=traditional", passphrase="μυστικό") as a:
        a.add_file_from_memory("notes.txt", len(NOTES.encode()), NOTES.encode())


# the failed document of an archive has the extension of the archive in small letters, like every document
def test_read_archive_failed_document(tmp_path):
    docs = read(tmp_path, "WORK.ZIP", lambda f: f.write_text(NOTES, encoding="utf-8"))
    assert [(str(d.file_path), d.ext, d.root_id) for d in docs] == [("WORK.ZIP", ".zip", "root_0")]


# an archive in the second root of the corpus: its files belong to that root, not to the folder they were unpacked in
def test_read_archive_in_the_second_root(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "other.txt").write_text(NOTES, encoding="utf-8")
    zip_of({"notes.txt": CODE})(second / "work.zip")
    config = CorpusConfig(roots=[first, second])
    scanned = next(s for s in scan(config).files if s.rel_path == Path("work.zip"))
    assert [(d.root_id, str(d.file_path)) for d in read_archive(scanned, config, set())] == [("root_1", "work.zip/notes.txt")]
