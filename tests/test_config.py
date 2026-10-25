from pathlib import Path

import pytest

from rag_fs.config import ChunkingConfig, Config, load_config


# the defaults live in config.py, so Config() works without any yaml file
def test_defaults_need_no_yaml():
    config = Config()
    assert config.seed == 14
    assert config.chunking.strategy == "content_aware"


# an empty yaml changes nothing
def test_empty_yaml_gives_all_defaults(tmp_path):
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("")

    assert load_config(yaml_file) == Config()


# the example file in configs/ must always load
def test_example_yaml_is_valid():
    config = load_config("configs/example.yaml")
    assert isinstance(config, Config)


# a yaml only changes the values it lists, everything else keeps its default
def test_override_changes_only_what_it_mentions(tmp_path):
    yaml_file = tmp_path / "exp.yaml"
    yaml_file.write_text("retrieval:\n  top_k: 10\n")

    config = load_config(yaml_file)

    assert config.retrieval.top_k == 10
    assert config.retrieval.rrf_k == Config().retrieval.rrf_k
    assert config.chunking == ChunkingConfig()


# roots come from the yaml as strings and must become Path objects
def test_roots_are_converted_to_path(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("corpus:\n  roots: [\"~/Documents\"]\n")

    config = load_config(yaml_file)

    assert len(config.corpus.roots) == 1
    assert isinstance(config.corpus.roots[0], Path)


# greek folder names in the yaml must not break (the file is read as utf-8)
def test_yaml_is_read_as_utf8(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("corpus:\n  roots: [\"/data/Έγγραφα\"]\n", encoding="utf-8")

    config = load_config(yaml_file)

    assert config.corpus.roots == [Path("/data/Έγγραφα")]


# a group missing from the yaml (here chunking) keeps its defaults
def test_missing_group_falls_back_to_defaults(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("seed: 99\n")

    config = load_config(yaml_file)

    assert config.seed == 99
    assert config.chunking.strategy == "content_aware"


# a typo in a key (top_kk) must fail, not be ignored
def test_unknown_key_raises_error(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("retrieval:\n  top_kk: 5\n")

    with pytest.raises(TypeError):
        load_config(yaml_file)


# a typo in a group name (chucking) must fail too
def test_unknown_group_raises_error(tmp_path):
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("chucking:\n  overlap: 0.4\n")

    with pytest.raises(ValueError):
        load_config(yaml_file)


# the ignore rules with markers and the sidecar patterns can be set in the yaml
def test_ignore_rules_from_yaml(tmp_path):
    yaml_file = tmp_path / "rules.yaml"
    yaml_file.write_text('corpus:\n  ignore_dirs_with:\n    dist: ["*.whl"]\n  ignore_sidecars: []\n')

    config = load_config(yaml_file)

    assert config.corpus.ignore_dirs_with == {"dist": ["*.whl"]}
    assert config.corpus.ignore_sidecars == []


# the OCR defaults: Greek and English, the best models, 300 dpi, the D30 quality limits
def test_ocr_defaults():
    ocr = Config().ocr
    assert ocr.languages == ["ell", "eng"]
    assert ocr.tessdata_dir == "~/.local/share/tessdata_best"
    assert (ocr.dpi, ocr.min_confidence, ocr.min_chars, ocr.min_image_size) == (300, 60, 20, 300)


# the yaml can change the OCR languages and keep the rest
def test_ocr_settings_from_yaml(tmp_path):
    yaml_file = tmp_path / "ocr.yaml"
    yaml_file.write_text("ocr:\n  languages: [deu, eng]\n  dpi: 200\n")

    ocr = load_config(yaml_file).ocr

    assert ocr.languages == ["deu", "eng"]
    assert ocr.dpi == 200
    assert ocr.min_chars == 20
