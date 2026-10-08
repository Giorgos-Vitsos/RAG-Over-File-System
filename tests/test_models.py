from pathlib import Path

import pytest

from rag_fs.models import (
    Chunk, Document, GoldSpan, Query, load_jsonl, save_jsonl, to_relative,
)


def test_query_round_trip_keeps_all_gold_spans(tmp_path):
    q = Query(
        qid="t07_prose", topic_id="t07", evidence_type="prose", split="test",
        text="Πόσο ήταν το ρεύμα τον Μάρτιο;",
        gold=[
            GoldSpan(file_path=Path("bills/2024/march.pdf"), char_start=120, char_end=180),
            GoldSpan(file_path=Path("finance/bills.xlsx"), char_start=900, char_end=940),
        ],
        author="giorgos",
    )
    file = tmp_path / "queries.jsonl"

    save_jsonl([q], file)
    loaded = load_jsonl(Query, file)

    assert loaded == [q]
    assert len(loaded[0].gold) == 2
    assert isinstance(loaded[0].gold[0], GoldSpan)
    assert isinstance(loaded[0].gold[0].file_path, Path)


def test_document_and_chunk_round_trip(tmp_path):
    doc = Document(
        file_path=Path("notes/σημειώσεις.txt"), root_id="root_1", ext=".txt",
        raw_text="Γεια σου κόσμε", last_modified=1728384000.5, content_type="prose",
        parse_status="ok", sha256="abc",
    )
    chunk = Chunk(
        chunk_id="c1", file_path=Path("notes/σημειώσεις.txt"), char_start=0, char_end=4,
        text="Γεια", index_text="File: notes/σημειώσεις.txt\n\nΓεια",
        content_type="prose", chunk_strategy="narrative",
    )

    save_jsonl([doc], tmp_path / "docs.jsonl")
    save_jsonl([chunk], tmp_path / "chunks.jsonl")

    assert load_jsonl(Document, tmp_path / "docs.jsonl") == [doc]
    assert load_jsonl(Chunk, tmp_path / "chunks.jsonl") == [chunk]


def test_saved_file_keeps_greek_readable(tmp_path):
    doc = Document(
        file_path=Path("a.txt"), root_id="root_1", ext=".txt", raw_text="Γεια",
        last_modified=0.0, content_type="prose", parse_status="ok", sha256="abc",
    )
    file = tmp_path / "docs.jsonl"

    save_jsonl([doc], file)

    assert "Γεια" in file.read_text(encoding="utf-8")


def test_to_relative_strips_the_root(tmp_path):
    root = tmp_path / "corpus"
    file = root / "bills" / "2024" / "march.pdf"

    assert to_relative(file, root) == Path("bills/2024/march.pdf")


def test_to_relative_rejects_file_outside_root(tmp_path):
    root = tmp_path / "corpus"
    outside = tmp_path / "secret" / "id_rsa"

    with pytest.raises(ValueError):
        to_relative(outside, root)