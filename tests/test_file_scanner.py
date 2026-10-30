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



# (path in the corpus, what the scan does with it: "kept" or the reason of the first ignored part of the path)
LAYOUT = {
    # folders made by tools, found by a file inside them
    "game/Library/ArtifactDB": "generated_dir",
    "game/Builds/v1/UnityPlayer.dll": "generated_dir",
    "game/UserSettings/EditorUserSettings.asset": "generated_dir",
    "game/Temp/UnityLockfile": "generated_dir",
    "game2/Library/PackageCache/com.unity/package.json": "generated_dir",
    "ray/build.meta": "sidecar_file",
    "ray/build/CMakeCache.txt": "generated_dir",
    "ray/build2/CMakeFiles/a.txt": "kept",
    "cards/bin/Model/Card.class": "generated_dir",
    "cards/out/production/app/Main.class": "generated_dir",
    "app/obj/main.o": "generated_dir",
    # the same names with real files inside
    "course/Library/tutorial.pdf": "kept",
    "compiler/out/phase2.txt": "kept",
    "notes/build/plan.txt": "kept",
    "scripts/bin/run.sh": "kept",
    # tool folders, by name
    "ray/cmake-build-debug/a.txt": "ignored_dir",
    "zip/__MACOSX/a.py": "ignored_dir",
    "lab/.ipynb_checkpoints/hw-checkpoint.ipynb": "ignored_dir",
    "java/.idea/misc.xml": "ignored_dir",
    "java/.vscode/settings.json": "ignored_dir",
    "java/.settings/org.eclipse.jdt.core.prefs": "ignored_dir",
    "eclipse/.metadata/version.ini": "ignored_dir",
    # unity .meta next to the file or folder it describes
    "game/Assets/Player.cs": "kept",
    "game/Assets/Player.cs.meta": "sidecar_file",
    "game/Assets.meta": "sidecar_file",
    "game/Assets/notes.meta": "kept",
    "game/Assets/.meta": "kept",
    # unity data of the editor (D68)
    "game/Assets/Level1.asset": "ignored_file",
    # junk files of the system and of office
    "docs/desktop.ini": "ignored_file",
    "docs/Thumbs.db": "ignored_file",
    "docs/.DS_Store": "ignored_file",
    "docs/~$report.docx": "ignored_file",
    "docs/._report.docx": "ignored_file",
    "docs/report.docx": "kept",
}


# writes every path of LAYOUT and scans the folder
@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "corpus"
    for rel in LAYOUT:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    return root, scan(CorpusConfig(roots=[root]))


# "kept", or the reason of the ignored entry that is the path or one of its folders
def what_happened(root, result, rel):
    target = root / rel
    for path, reason in result.ignored:
        if path == target or path in target.parents:
            return reason
    if any(f.abs_path == target for f in result.files):
        return "kept"
    return "missing"


# every path ends up kept or ignored for the right reason
@pytest.mark.parametrize("rel, expected", LAYOUT.items(), ids=LAYOUT.keys())
def test_ignore_rules(layout, rel, expected):
    root, result = layout
    assert what_happened(root, result, rel) == expected


# a generated folder is skipped as a whole, nothing inside it is listed
def test_does_not_enter_generated_dirs(layout):
    root, result = layout
    inside = [f for f in result.files if root / "game/Library" in f.abs_path.parents]
    assert inside == []
    assert [reason for path, reason in result.ignored if path == root / "game/Library"] == ["generated_dir"]


# the rules come from the config: without them the same folders are kept
def test_ignore_rules_can_be_turned_off(layout, tmp_path):
    root, _ = layout
    result = scan(CorpusConfig(roots=[root], ignore_dirs_with={}, ignore_sidecars=[]))
    for rel in ["game/Library/ArtifactDB", "cards/bin/Model/Card.class", "game/Assets/Player.cs.meta"]:
        assert what_happened(root, result, rel) == "kept"


# when two rules match the same folder name, a rule without a marker inside does not stop the next one
def test_every_matching_rule_is_tried(tmp_path):
    root = tmp_path / "corpus"
    (root / "game/Library").mkdir(parents=True)
    (root / "game/Library/ArtifactDB").write_text("x")

    result = scan(CorpusConfig(roots=[root], ignore_dirs_with={"Lib*": ["nothing"], "Library": ["ArtifactDB"]}))

    assert what_happened(root, result, "game/Library/ArtifactDB") == "generated_dir"




# data files of unity and project files of the IDEs are not text of the user (D71)
@pytest.mark.parametrize("rel", [
    "game/Assets/Wood.mat", "game/Assets/Walk.anim", "game/Assets/Player.prefab", "game/Assets/Player.controller",
    "game/Assets/Water.shadergraph", "game/Assets/Main.unity", "game/Assets/Audio.mixer", "game/Assets/Controls.inputactions",
    "java/app.iml", "game/Game.csproj", "game/Game.sln", "game/Game.slnx",
])
def test_unity_and_ide_files_are_ignored(tmp_path, rel):
    root = tmp_path / "corpus"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("x")
    assert what_happened(root, scan(CorpusConfig(roots=[root])), rel) == "ignored_file"


# pictures written as numbers and latex helper files stay, the user decided so (D71)
@pytest.mark.parametrize("rel", ["lab/picture.ppm", "thesis/main.aux"])
def test_ppm_and_aux_files_are_kept(tmp_path, rel):
    root = tmp_path / "corpus"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("x")
    assert what_happened(root, scan(CorpusConfig(roots=[root])), rel) == "kept"


ARCHIVES = ["work.zip", "lib.JAR", "data.tar", "data.tar.gz", "src.tgz", "back.7z", "old.rar", "notes.txt.gz"]


# a corpus of one file `name` with `size` MB, scanned with 1 MB for files and 3 MB for archives
def scan_one(tmp_path, name, size):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / name).write_bytes(b"x" * int(size * 1024 * 1024))
    return scan(CorpusConfig(roots=[root], max_file_size_mb=1, max_archive_size_mb=3))


# an archive is a folder, not a document: bigger than the limit of files is kept, its files have that limit inside
@pytest.mark.parametrize("name", ARCHIVES)
def test_archive_bigger_than_a_file_is_kept(tmp_path, name):
    result = scan_one(tmp_path, name, 2)
    assert [str(f.rel_path) for f in result.files] == [name] and result.ignored == []


# up to the limit of archives the archive is kept, past it it is too large like any file
@pytest.mark.parametrize("size, kept", [
    pytest.param(3, True, id="at the limit"),
    pytest.param(3.5, False, id="past the limit"),
])
def test_archive_limit(tmp_path, size, kept):
    result = scan_one(tmp_path, "work.zip", size)
    assert ([str(f.rel_path) for f in result.files], [r for _, r in result.ignored]) == \
        ((["work.zip"], []) if kept else ([], ["too_large"]))


# the same size for a file that is not an archive is still too large, also with archive words in the name
@pytest.mark.parametrize("name", ["notes.txt", "zip.txt", "work.zip.txt", "data.gz.bak"])
def test_not_archive_bigger_than_a_file(tmp_path, name):
    result = scan_one(tmp_path, name, 2)
    assert result.files == [] and [r for _, r in result.ignored] == ["too_large"]


# the archive extensions are written once, in the scanner, and the parsers use the same set
def test_archive_extensions_in_one_place():
    from rag_fs.ingest import file_scanner, parsers
    assert parsers.ARCHIVE_EXT is file_scanner.ARCHIVE_EXT
    assert file_scanner.ARCHIVE_EXT <= parsers.BINARY_EXT
