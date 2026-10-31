from dataclasses import dataclass, field
from pathlib import Path
import yaml

@dataclass(frozen=True)
class CorpusConfig:
    roots: list[Path] = field(default_factory=list)
    ignore_dirs: list[str] = field(default_factory=lambda: [
        ".git", ".venv", ".ssh", ".cache", "__pycache__", "node_modules",".pytest_cache",".mypy_cache","*.egg-info",
        "__MACOSX", ".ipynb_checkpoints", ".idea", ".vscode", ".vs", ".settings", ".metadata", ".gradle", "cmake-build-*",
        "nbproject"
    ])
    ignore_globs: list[str] = field(default_factory=lambda: [
        "*.temp", "*.log", "*.iso", ".env*", "*.pem", "id_rsa*",
        "desktop.ini", "Thumbs.db", ".DS_Store", "~$*", "._*","*.asset","*.mat","*.anim","*.prefab","*.controller",
        "*.shadergraph","*.unity","*.mixer","*.inputactions","*.iml","*.csproj","*.sln","*.slnx","*.synctex.gz"
    ])
    ignore_dirs_with: dict[str, list[str]] = field(default_factory=lambda: {#we ignore these dirs only if they are generated with these files
        "Library": ["ArtifactDB", "PackageCache"],
        "Builds": ["UnityPlayer.dll", "*.apk"],
        "UserSettings": ["EditorUserSettings.asset"],
        "Temp": ["UnityLockfile"],
        "build": ["CMakeCache.txt", "build.ninja", "*.class", "*.o", "*.obj"],
        "out": ["*.class", "*.o", "*.obj"],
        "bin": ["*.class", "*.o", "*.obj", "*.pdb"],
        "obj": ["*.o", "*.obj", "project.assets.json"],
    })
    ignore_sidecars: list[str] = field(default_factory=lambda: [#we ignore those files only if the original exists
        "*.meta"
    ])
    max_file_size_mb: int = 50
    max_archive_size_mb: int = 2048
    max_archive_depth: int = 4
    follow_symlinks: bool = False

@dataclass(frozen=True)
class OCRConfig:
    languages: list[str]=field(default_factory=lambda:[
        "ell","eng"
    ])
    tessdata_dir: str="~/.local/share/tessdata_best"
    dpi: int=300
    min_confidence: float=60
    min_chars: int=20
    min_image_size: int=300

@dataclass(frozen=True)
class ChunkingConfig:
    strategy: str = "content_aware"
    narrative_words: int = 400
    overlap: float = 0.2
    tabular_rows: int = 20

@dataclass(frozen=True)
class ModelsConfig:
    embedding: str = "BAAI/bge-m3"
    reranker: str = "BAAI/bge-reranker-v2-m3"
    device: str = "auto"
    batch_size: int = 32

@dataclass(frozen=True)
class RetrievalConfig:
    retriever: str = "hybrid"
    rerank: bool = True
    pool_size: int = 30
    top_k: int = 5
    rrf_k: int = 60

@dataclass(frozen=True)
class LLMConfig:
    provider: str = "none"
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""

@dataclass(frozen=True)
class Config:
    seed: int = 14
    corpus: CorpusConfig = field(default_factory=CorpusConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ocr: OCRConfig= field(default_factory=OCRConfig)

def load_config(path: str | Path)->Config:
    with open(path,encoding="utf-8") as f:
        data=yaml.safe_load(f) or {}
    allowed=["seed","corpus","chunking","models","retrieval","llm","ocr"]
    for key in data:
        if key not in allowed:
            raise ValueError(f"unknown config section {key}")
    corpus_data= dict(data.get("corpus",{}))
    new_roots=[]
    for r in corpus_data.get("roots",[]):#we make strs to paths
        new_roots.append(Path(r).expanduser())
    corpus_data["roots"]=new_roots

    return Config(
        seed=data.get("seed",Config.seed),
        corpus=CorpusConfig(**corpus_data),
        chunking=ChunkingConfig(**data.get("chunking",{})),
        models=ModelsConfig(**data.get("models",{})),
        retrieval=RetrievalConfig(**data.get("retrieval",{})),
        llm=LLMConfig(**data.get("llm",{})),
        ocr=OCRConfig(**data.get("ocr",{}))
    )
