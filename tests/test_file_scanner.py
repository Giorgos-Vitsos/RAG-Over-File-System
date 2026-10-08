import os
from pathlib import Path

import pytest

from rag_fs.config import CorpusConfig
from rag_fs.ingest.file_scanner import scan

# sha256 of the text "hello", the content of notes.txt
HELLO_SHA256 = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


# a small corpus: normal files in nested folders, files and a folder that must be ignored,
# a file bigger than the limit and a symlink
@pytest.fixture
def fake_corpus(tmp_path):
    root = tmp_path / "corpus"
    files = {
        "notes.txt": "hello",
        "bills/march.pdf": "pdf",
        "bills/2024/april.pdf": "pdf2",
        ".env": "KEY=secret",
        "code/main.py": "print(1)",
        "code/debug.log": "log",
        "code/node_modules/lib.js": "x",
    }
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    (root / "big.bin").write_bytes(b"x" * 2_000_000)
    os.symlink(root / "notes.txt", root / "shortcut.txt")
    return root


# the scan of fake_corpus with a 1 MB size limit
@pytest.fixture
def result(fake_corpus):
    config = CorpusConfig(roots=[fake_corpus], max_file_size_mb=1)
    return scan(config)


# relative paths of the files the scan kept
def found(result):
    paths = []
    for f in result.files:
        paths.append(f.rel_path.as_posix())
    return paths


# (file name, reason) for everything the scan skipped
def ignored(result):
    pairs = []
    for path, reason in result.ignored:
        pairs.append((path.name, reason))
    return pairs


# only the 4 normal files are kept, always in the same order
def test_finds_exactly_the_expected_files_in_order(result):
    assert found(result) == [
        "notes.txt",
        "bills/march.pdf",
        "bills/2024/april.pdf",
        "code/main.py",
    ]


# files come out sorted by name even if they were created in reverse order
def test_files_in_a_folder_come_out_sorted(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    names = [f"file_{n:02d}.txt" for n in range(20)]
    for name in reversed(names):
        (root / name).write_text(name)

    result = scan(CorpusConfig(roots=[root]))

    assert found(result) == names


# .env and *.log match ignore_globs, so they are skipped with a reason
def test_skips_ignored_files(result):
    assert ".env" not in found(result)
    assert "code/debug.log" not in found(result)
    assert (".env", "ignored_file") in ignored(result)
    assert ("debug.log", "ignored_file") in ignored(result)


# node_modules is skipped as a whole, the files inside it are not even listed
def test_does_not_enter_ignored_dirs(result):
    assert ("node_modules", "ignored_dir") in ignored(result)
    everything = found(result) + [name for name, _ in ignored(result)]
    assert not any("lib.js" in name for name in everything)


# big.bin is 2 MB and the limit is 1 MB
def test_skips_too_large_files(result):
    assert ("big.bin", "too_large") in ignored(result)


# symlinks are not followed by default, so notes.txt is found only once
def test_skips_symlinks_without_duplicates(result):
    assert ("shortcut.txt", "ignored_symlink") in ignored(result)
    assert found(result).count("notes.txt") == 1


# the hash, size, root and absolute path of a kept file are filled correctly
def test_hash_size_and_root_are_correct(result):
    notes = None
    for f in result.files:
        if f.rel_path == Path("notes.txt"):
            notes = f
    assert notes is not None
    assert notes.sha256 == HELLO_SHA256
    assert notes.size == 5
    assert notes.root_id == "root_0"
    assert notes.abs_path.is_absolute()


# a root folder that does not exist is an error, not an empty corpus
def test_missing_root_raises(tmp_path):
    config = CorpusConfig(roots=[tmp_path / "does_not_exist"])
    with pytest.raises(FileNotFoundError):
        scan(config)


# a root inside an ignored folder (.ssh) is refused
def test_root_inside_ignored_dir_raises(tmp_path):
    secret_dir = tmp_path / ".ssh"
    secret_dir.mkdir()
    config = CorpusConfig(roots=[secret_dir])
    with pytest.raises(ValueError):
        scan(config)


# a file we can't read is skipped with a reason and the scan goes on
# (skipped for root and on windows, where permissions don't block reading)
@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="file permissions are not enforced for root or on Windows",
)
def test_unreadable_file_does_not_stop_the_scan(fake_corpus):
    locked = fake_corpus / "locked.txt"
    locked.write_text("secret")
    locked.chmod(0)
    try:
        result = scan(CorpusConfig(roots=[fake_corpus], max_file_size_mb=1))
    finally:
        locked.chmod(0o644)

    assert ("locked.txt", "unreadable") in ignored(result)
    assert "notes.txt" in found(result)


# with follow_symlinks a link can lead outside the root, those files are skipped
def test_followed_symlink_outside_root_is_skipped(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.txt").write_text("a")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    os.symlink(outside, root / "linkdir")

    result = scan(CorpusConfig(roots=[root], follow_symlinks=True))

    assert ("secret.txt", "outside_root") in ignored(result)
    assert found(result) == ["a.txt"]
