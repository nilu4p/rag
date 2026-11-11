"""Configuration lue depuis les variables d'environnement (fichier .env via docker compose)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip().replace(",", ".") or default)
    except ValueError:
        return default


def _str(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


def _bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "oui", "on", "auto"}


@dataclass
class Settings:
    # Services
    ollama_url: str = field(default_factory=lambda: _str("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/"))
    qdrant_url: str = field(default_factory=lambda: _str("QDRANT_URL", "http://qdrant:6333").rstrip("/"))
    qdrant_api_key: str = field(default_factory=lambda: _str("QDRANT_API_KEY", ""))
    searxng_url: str = field(default_factory=lambda: _str("SEARXNG_URL", "http://searxng:8080").rstrip("/"))

    # Modèles
    chat_model: str = field(default_factory=lambda: _str("CHAT_MODEL", "gpt-oss:20b"))
    embed_model: str = field(default_factory=lambda: _str("EMBED_MODEL", "bge-m3"))
    num_ctx: int = field(default_factory=lambda: _int("OLLAMA_NUM_CTX", 16384))
    keep_alive: str = field(default_factory=lambda: _str("OLLAMA_KEEP_ALIVE", "30m"))
    # gpt-oss ne peut pas désactiver sa réflexion : low = la plus courte. Autres modèles : off.
    think: str = field(default_factory=lambda: _str("LLM_THINK", "low"))
    temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.2))

    # Documents et index
    docs_dir: Path = field(default_factory=lambda: Path(_str("DOCS_DIR", "/data/documents")))
    data_dir: Path = field(default_factory=lambda: Path(_str("DATA_DIR", "/data/state")))
    collection: str = field(default_factory=lambda: _str("QDRANT_COLLECTION", "documents"))
    chunk_size: int = field(default_factory=lambda: _int("CHUNK_SIZE", 1200))
    chunk_overlap: int = field(default_factory=lambda: _int("CHUNK_OVERLAP", 200))
    index_interval: int = field(default_factory=lambda: _int("INDEX_INTERVAL", 30))
    ocr_langs: str = field(default_factory=lambda: _str("OCR_LANGS", "fra+eng"))
    max_file_mb: int = field(default_factory=lambda: _int("MAX_FILE_MB", 200))

    # Recherche
    top_k: int = field(default_factory=lambda: _int("TOP_K", 6))
    min_score: float = field(default_factory=lambda: _float("MIN_SCORE", 0.40))
    memory_turns: int = field(default_factory=lambda: _int("MEMORY_TURNS", 4))
    query_router: bool = field(default_factory=lambda: _bool("QUERY_ROUTER", True))

    # Internet
    web_search: str = field(default_factory=lambda: _str("WEB_SEARCH", "auto").lower())
    web_results: int = field(default_factory=lambda: max(1, min(8, _int("WEB_RESULTS", 4))))
    web_lang: str = field(default_factory=lambda: _str("WEB_LANG", "fr"))

    # Accès
    chat_user: str = field(default_factory=lambda: _str("CHAT_USER", "moi"))
    chat_password: str = field(default_factory=lambda: os.environ.get("CHAT_PASSWORD", ""))
    api_token: str = field(default_factory=lambda: os.environ.get("RAG_API_TOKEN", ""))

    timezone: str = field(default_factory=lambda: _str("TIMEZONE", "Europe/Paris"))

    @property
    def web_enabled(self) -> bool:
        return self.web_search not in {"off", "0", "false", "non", "no"}

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rag.sqlite3"


settings = Settings()
