import datetime
import gzip
import hashlib
import io
import random
import tarfile
import zipfile
from pathlib import Path

import libarchive
import pytest
import tempfile

from rag_fs.config import CorpusConfig
from rag_fs.ingest.pipeline import read_archive
from rag_fs.ingest.file_scanner import scan

NOTES = "Σημειώσεις του μαθήματος για την εξέταση."
CODE = "def main():\n    print('γεια')\n"


# reads the archive `name` of a corpus where `make` writes it, with `skip` as the hashes already read
# and `limits` as other settings of the corpus
def read(tmp_path, name, make, skip=None, **limits):
    root = tmp_path / "corpus"
    f = root / name
    f.parent.mkdir(parents=True, exist_ok=True)
    make(f)
    config = CorpusConfig(roots=[root], **limits)
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


MB = 1024 * 1024
FAILED = ("work.zip", "failed", "binary", "")
NOTES_READ = ("work.zip/notes.txt", "ok", "prose", NOTES)


# the temporary folders are made inside the test folder, so that a file written outside them is seen and cleaned
@pytest.fixture
def temp_dir(tmp_path, monkeypatch):
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


# a tar with links {name: target} first and then files {name: text}
def tar_with_links(links, files, hard=False):
    def make(f):
        with tarfile.open(f, "w") as t:
            for name, target in links.items():
                link = tarfile.TarInfo(name)
                link.type = tarfile.LNKTYPE if hard else tarfile.SYMTYPE
                link.linkname = target
                t.addfile(link)
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(as_bytes(data))
                t.addfile(info, io.BytesIO(as_bytes(data)))
    return make


# the bytes of an archive that `make` writes, to put it inside another archive
def bytes_of(tmp_path, name, make):
    f = tmp_path / "build" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    make(f)
    return f.read_bytes()


# the limits of archives are settings of the corpus: 2 GB unpacked in all, archives inside archives up to 4 levels
def test_archive_limits_default():
    assert (CorpusConfig().max_archive_size_mb, CorpusConfig().max_archive_depth) == (2048, 4)


# a name that leads out of the temporary folder is not written anywhere, the other files are read
# and the archive is failed
@pytest.mark.parametrize("name", ["../evil.txt", "../../evil.txt", "a/../../evil.txt", "absolute"])
@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_read_archive_name_outside(tmp_path, temp_dir, name, kind):
    name = str(tmp_path / "evil.txt") if name == "absolute" else name
    files = {name: "κακό", "notes.txt": NOTES}
    archive = "work." + kind
    docs = read(tmp_path, archive, zip_of(files) if kind == "zip" else tar_of(files))
    assert summary(docs) == [(archive, "failed", "binary", ""), (archive + "/notes.txt", "ok", "prose", NOTES)]
    assert sorted(f.name for f in tmp_path.iterdir()) == ["corpus", "tmp"]
    assert list(temp_dir.iterdir()) == []


# a name that is the folder itself is not a file: failed, not a crash
@pytest.mark.parametrize("name", ["a/..", "."])
def test_read_archive_name_of_the_folder(tmp_path, temp_dir, name):
    assert summary(read(tmp_path, "work.zip", zip_of({name: "κακό", "notes.txt": NOTES}))) == [FAILED, NOTES_READ]


# two dots inside a name are not a way out
def test_read_archive_name_with_two_dots(tmp_path, temp_dir):
    docs = read(tmp_path, "work.zip", zip_of({"σημειώσεις..τελικές.txt": NOTES}))
    assert summary(docs) == [("work.zip/σημειώσεις..τελικές.txt", "ok", "prose", NOTES)]


# a name too long for linux (255 bytes, 128 greek letters) or a file where a folder should be:
# that file is failed, the rest are read
@pytest.mark.parametrize("files, read_too", [
    ({"α" * 130 + ".txt": CODE, "notes.txt": NOTES}, NOTES_READ),
    ({"notes.txt": NOTES, "notes.txt/main.py": CODE}, NOTES_READ),
], ids=["long name", "file and folder"])
def test_read_archive_name_that_cannot_be_written(tmp_path, temp_dir, files, read_too):
    assert summary(read(tmp_path, "work.zip", zip_of(files))) == [FAILED, read_too]


# a symbolic link is not made, not even when it leads to a folder outside that later files go into
@pytest.mark.parametrize("name, target, inside", [
    ("link.txt", "/etc/passwd", "notes.txt"),
    ("out", "OUTSIDE", "out/notes.txt"),
], ids=["to a file", "to a folder"])
def test_read_archive_symbolic_link(tmp_path, temp_dir, name, target, inside):
    outside = tmp_path / "outside"
    target = str(outside) if target == "OUTSIDE" else target
    docs = read(tmp_path, "work.tar", tar_with_links({name: target}, {inside: NOTES}))
    assert summary(docs) == [("work.tar/" + inside, "ok", "prose", NOTES)]
    assert not outside.exists()


# a hard link is not made: inside the archive it has no bytes of its own, the file it points to is read by itself
@pytest.mark.parametrize("target", ["notes.txt", "../../etc/passwd"])
def test_read_archive_hard_link(tmp_path, temp_dir, target):
    docs = read(tmp_path, "work.tar", tar_with_links({"copy.txt": target}, {"notes.txt": NOTES}, hard=True))
    assert summary(docs) == [("work.tar/notes.txt", "ok", "prose", NOTES)]


# a file bigger than the limit of the corpus is not read, and is not even written whole:
# 3 MB unpacked would go over the 2 MB of the archive, the 1 MB that is written does not
def test_read_archive_file_too_large(tmp_path, temp_dir):
    files = {"big.txt": "a" * (3 * MB), "notes.txt": NOTES}
    docs = read(tmp_path, "work.zip", zip_of(files), max_file_size_mb=1, max_archive_size_mb=2)
    assert summary(docs) == [NOTES_READ]


# more unpacked than the limit (a zip bomb): the unpacking stops there and the archive is failed
@pytest.mark.parametrize("files, expected", [
    ({"notes.txt": NOTES, "big.txt": "a" * (3 * MB)}, [FAILED, NOTES_READ]),
    ({"big.txt": "a" * (3 * MB), "notes.txt": NOTES}, [FAILED]),
], ids=["big file last", "big file first"])
def test_read_archive_too_much_unpacked(tmp_path, temp_dir, files, expected):
    assert summary(read(tmp_path, "work.zip", zip_of(files), max_archive_size_mb=2)) == expected


# archives inside an archive are read like the archive itself, with both in the path
@pytest.mark.parametrize("name, make, inside", [
    ("inner.zip", zip_of({"notes.txt": NOTES}), "inner.zip/notes.txt"),
    ("inner.tar.gz", tar_of({"notes.txt": NOTES}, "w:gz"), "inner.tar.gz/notes.txt"),
    ("inner.tgz", tar_of({"notes.txt": NOTES}, "w:gz"), "inner.tgz/notes.txt"),
    ("inner.7z", sevenzip_of({"notes.txt": NOTES}), "inner.7z/notes.txt"),
    ("Lib.JAR", zip_of({"notes.txt": NOTES}), "Lib.JAR/notes.txt"),
    ("notes.txt.gz", gz_of(NOTES), "notes.txt.gz/notes.txt"),
], ids=["zip", "tar.gz", "tgz", "7z", "jar", "gz"])
def test_read_archive_inside_archive(tmp_path, temp_dir, name, make, inside):
    files = {"src/" + name: bytes_of(tmp_path, name, make), "main.py": CODE}
    docs = read(tmp_path, "work.zip", zip_of(files))
    assert summary(docs) == [("work.zip/main.py", "ok", "code", CODE), ("work.zip/src/" + inside, "ok", "prose", NOTES)]


# level 1 holds level 2 and so on: up to the limit every level is read, the archive past it is failed
def chain(levels):
    files = {f"n{levels}.txt": f"Σημειώσεις του επιπέδου {levels}."}
    for level in range(levels - 1, 0, -1):
        files = {f"n{level}.txt": f"Σημειώσεις του επιπέδου {level}.", f"l{level + 1}.zip": zip_bytes(files)}
    return files


@pytest.mark.parametrize("depth, levels", [(None, 5), (2, 3), (1, 2)])
def test_read_archive_depth(tmp_path, temp_dir, depth, levels):
    limits = {} if depth is None else {"max_archive_depth": depth}
    depth = 4 if depth is None else depth
    docs = read(tmp_path, "l1.zip", zip_of(chain(levels)), **limits)
    folders = ["/".join(f"l{k}.zip" for k in range(1, level + 1)) for level in range(1, levels + 1)]
    expected = [(folders[level - 1] + f"/n{level}.txt", "ok", "prose", f"Σημειώσεις του επιπέδου {level}.")
                for level in range(1, depth + 1)]
    assert summary(docs) == sorted(expected + [(folders[depth], "failed", "binary", "")])


# a chain that ends inside the limit is read whole
def test_read_archive_depth_inside_the_limit(tmp_path, temp_dir):
    docs = read(tmp_path, "l1.zip", zip_of(chain(4)))
    assert [s for _, s, _, _ in summary(docs)] == ["ok"] * 4


# an archive inside that cannot be opened is failed by itself, the archive around it is not
def test_read_archive_broken_inside(tmp_path, temp_dir):
    docs = read(tmp_path, "work.zip", zip_of({"bad.zip": NOTES, "main.py": CODE}))
    assert summary(docs) == [("work.zip/bad.zip", "failed", "binary", ""), ("work.zip/main.py", "ok", "code", CODE)]


# the files read are skipped across levels: a file inside the inner archive that is also in the outer one
# is read once, whichever of the two comes first
def test_read_archive_skip_across_levels(tmp_path, temp_dir):
    files = {"notes.txt": NOTES, "inner.zip": zip_bytes({"copy.txt": NOTES, "main.py": CODE})}
    skip = set()
    docs = read(tmp_path, "work.zip", zip_of(files), skip)
    assert sorted(d.raw_text for d in docs) == sorted([NOTES, CODE])
    assert ("work.zip/inner.zip/main.py", "ok", "code", CODE) in summary(docs)
    assert hashlib.sha256(CODE.encode()).hexdigest() in skip


# the 2 MB are for the archive and all the archives inside it together, not 2 MB for each
def test_read_archive_limit_for_all_levels(tmp_path, temp_dir):
    files = {"a.zip": zip_bytes({"big.txt": "a" * (3 * MB // 2)}), "b.zip": zip_bytes({"big.txt": "b" * (3 * MB // 2)})}
    docs = read(tmp_path, "work.zip", zip_of(files), max_archive_size_mb=2)
    failed = [path for path, status, _, _ in summary(docs) if status == "failed"]
    assert len(failed) == 1 and failed[0] in ("work.zip/a.zip", "work.zip/b.zip")


# the documents of an archive inside an archive belong to the root of the outer archive
def test_read_archive_inside_archive_in_the_second_root(tmp_path, temp_dir):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "other.txt").write_text(NOTES, encoding="utf-8")
    zip_of({"inner.zip": zip_bytes({"main.py": CODE})})(second / "work.zip")
    config = CorpusConfig(roots=[first, second])
    scanned = next(s for s in scan(config).files if s.rel_path == Path("work.zip"))
    docs = read_archive(scanned, config, set())
    assert [(d.root_id, str(d.file_path)) for d in docs] == [("root_1", "work.zip/inner.zip/main.py")]


# the sync data of LaTeX (.synctex.gz) is not content: ignored like build.log
def test_read_archive_synctex_ignored(tmp_path, temp_dir):
    files = {"paper.synctex.gz": gzip.compress(b"SyncTeX Version:1\nInput:1:paper.tex\n"), "notes.txt": NOTES}
    assert summary(read(tmp_path, "work.zip", zip_of(files))) == [NOTES_READ]


# the temporary folders are behind a link (on macOS /tmp is a link to /private/tmp): the files are still inside them
def test_read_archive_temp_folder_behind_a_link(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "link"))
    assert summary(read(tmp_path, "work.zip", zip_of({"notes.txt": NOTES}))) == [NOTES_READ]


# an archive bigger than the limit of files is read: its files have that limit, the archive has its own
def test_read_archive_bigger_than_a_file(tmp_path, temp_dir):
    files = {"data.bin": random.Random(0).randbytes(MB * 3 // 2), "notes.txt": NOTES}
    make = lambda f: f.write_bytes(zip_bytes(files, zipfile.ZIP_STORED))
    assert summary(read(tmp_path, "work.zip", make, max_file_size_mb=1)) == [NOTES_READ]
