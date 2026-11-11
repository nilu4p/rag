"""Indexation incrémentale du dossier documents/ vers Qdrant.

- Seuls les fichiers nouveaux ou modifiés sont recalculés (taille + date, puis empreinte SHA-256).
- Les fichiers supprimés sont retirés de l'index.
- Les nouveaux morceaux sont écrits AVANT de supprimer les anciens : jamais de trou dans l'index.
- Une seule indexation à la fois (verrou) ; une surveillance tourne en tâche de fond.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import sparse
from .chunking import split_pages
from .config import settings
from .db import DB
from .extract import ExtractError, extract, is_supported
from .llm import LLMError, Ollama
from .qdrant import Qdrant

log = logging.getLogger("rag.indexer")

IGNORED_DIRS = {"_ocr", "__pycache__", "node_modules", "lost+found"}
NS = uuid.UUID("6f1c0b0e-5a43-4e8f-9d4b-2c7a8e1f3b55")


@dataclass
class Progress:
    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    total: int = 0
    done: int = 0
    current: str = ""
    added: int = 0
    updated: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)
    message: str = ""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(settings.docs_dir).as_posix()


def scan(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if not root.exists():
        return found
    for p in root.rglob("*"):
        parts = p.relative_to(root).parts
        if any(x.startswith(".") or x in IGNORED_DIRS for x in parts):
            continue
        if p.name.startswith("~$") or p.name.endswith((".part", ".tmp", ".crdownload")):
            continue
        if p.is_file() and is_supported(p):
            found[rel(p)] = p
    return found


def embed_text(name: str, heading: str, label: str, text: str) -> str:
    """Texte réellement vectorisé : le contexte (document, section) aide beaucoup la recherche."""
    ctx = f"Document : {name}"
    if label:
        ctx += f" ({label})"
    if heading:
        ctx += f"\nSection : {heading}"
    return f"{ctx}\n\n{text}"


class Indexer:
    def __init__(self, db: DB, llm: Ollama, store: Qdrant):
        self.db, self.llm, self.store = db, llm, store
        self.lock = asyncio.Lock()
        self.progress = Progress()
        self._wake = asyncio.Event()
        self._dim: int | None = None

    # ---------- préparation ----------
    async def embedding_dim(self) -> int:
        if self._dim is None:
            self._dim = len((await self.llm.embed(["dimension"]))[0])
        return self._dim

    async def ensure_collection(self) -> None:
        """Crée la collection ; la recrée si le modèle d'embeddings a changé (réindexation complète)."""
        dim = await self.embedding_dim()
        previous = self.db.get_meta("embed_model")
        same_dim = await self.store.ensure(dim)
        if not same_dim or (previous and previous != settings.embed_model):
            log.warning("Modèle d'embeddings changé (%s → %s) : réindexation complète", previous, settings.embed_model)
            await self.store.drop()
            await self.store.create(dim)
            self.db.clear_files()
        elif await self.store.count() == 0 and self.db.files():
            # Index Qdrant vidé (volume supprimé…) : on oublie l'état pour tout réindexer
            self.db.clear_files()
        self.db.set_meta("embed_model", settings.embed_model)

    # ---------- synchronisation ----------
    def trigger(self) -> None:
        self._wake.set()

    async def watch(self) -> None:
        """Boucle de fond : synchronise au démarrage, puis toutes les INDEX_INTERVAL secondes."""
        await asyncio.sleep(2)
        while True:
            try:
                await self.sync()
            except Exception as e:  # noqa: BLE001 - la surveillance ne doit jamais s'arrêter
                log.error("Synchronisation impossible : %s", e)
                self.progress.message = f"Échec de la synchronisation : {e}"
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(5, settings.index_interval))
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def sync(self, full: bool = False) -> Progress:
        if self.lock.locked():
            return self.progress
        async with self.lock:
            return await self._sync(full)

    async def _sync(self, full: bool) -> Progress:
        await self.ensure_collection()
        if full:
            await self.store.drop()
            await self.store.create(await self.embedding_dim())
            self.db.clear_files()

        on_disk = await asyncio.to_thread(scan, settings.docs_dir)
        known = self.db.files()

        todo: list[tuple[str, Path]] = []
        for key, path in on_disk.items():
            st = path.stat()
            row = known.get(key)
            if row and row["size"] == st.st_size and abs((row["mtime"] or 0) - st.st_mtime) < 1e-3:
                continue
            todo.append((key, path))
        removed = [k for k in known if k not in on_disk]

        if not todo and not removed:
            return self.progress

        p = self.progress = Progress(running=True, started_at=time.time(), total=len(todo))
        try:
            for key in removed:
                await self.store.delete_path(key)
                self.db.delete_file(key)
                p.removed += 1
                log.info("Retiré de l'index : %s", key)
            for key, path in todo:
                p.current = key
                try:
                    await self._index_file(key, path, known.get(key))
                except LLMError as e:
                    # Ollama indisponible : inutile d'insister sur les autres fichiers
                    p.errors.append(f"{key} : {e}")
                    p.message = f"Ollama indisponible : {e}"
                    log.error("Indexation interrompue : %s", e)
                    break
                p.done += 1
        finally:
            p.running = False
            p.current = ""
            p.finished_at = time.time()
            if not p.message:
                p.message = (
                    f"{p.added} ajouté(s), {p.updated} mis à jour, {p.removed} retiré(s)"
                    + (f", {len(p.errors)} erreur(s)" if p.errors else "")
                )
            log.info("Synchronisation : %s", p.message)
        return p

    async def _index_file(self, key: str, path: Path, row: dict | None) -> None:
        p = self.progress
        st = path.stat()
        if st.st_size > settings.max_file_mb * 1024 * 1024:
            await self._fail(key, st, None, f"fichier trop volumineux (> {settings.max_file_mb} Mo)")
            return
        digest = await asyncio.to_thread(sha256, path)
        if row and row["sha"] == digest and row["status"] == "ok":
            self.db.save_file(key, size=st.st_size, mtime=st.st_mtime)  # simplement « touché »
            return
        t0 = time.time()
        try:
            ex = await asyncio.to_thread(extract, path)
        except ExtractError as e:
            await self._fail(key, st, digest, str(e))
            return
        except Exception as e:  # noqa: BLE001 - fichier corrompu : on le signale et on continue
            log.exception("Extraction de %s", key)
            await self._fail(key, st, digest, f"illisible ({type(e).__name__} : {e})")
            return

        chunks = split_pages(ex.pages, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            await self.store.delete_path(key)
            self.db.save_file(
                key, sha=digest, size=st.st_size, mtime=st.st_mtime, status="empty", method=ex.method,
                error="aucun texte trouvé (image sans texte lisible, document vide ?)", pages=len(ex.pages),
                chunks=0, chars=0, indexed_at=time.time(),
            )
            return

        name = path.name
        vectors = await self.llm.embed([embed_text(name, c.heading, c.label, c.text) for c in chunks])
        points = []
        for i, (c, vec) in enumerate(zip(chunks, vectors)):
            points.append(
                {
                    "id": str(uuid.uuid5(NS, f"{key}\x00{digest}\x00{i}")),
                    "vector": {
                        "dense": vec,
                        "sparse": sparse.doc_vector(f"{name} {c.heading} {c.text}"),
                    },
                    "payload": {
                        "path": key,
                        "name": name,
                        "sha": digest,
                        "chunk": i,
                        "page": c.page,
                        "label": c.label,
                        "heading": c.heading,
                        "text": c.text,
                        "method": ex.method,
                    },
                }
            )
        await self.store.upsert(points)
        await self.store.delete_path(key, keep_sha=digest)
        self.db.save_file(
            key, sha=digest, size=st.st_size, mtime=st.st_mtime, status="ok", error=None, method=ex.method,
            pages=len(ex.pages), chunks=len(chunks), chars=ex.text_length, indexed_at=time.time(),
        )
        if row:
            p.updated += 1
        else:
            p.added += 1
        log.info("Indexé : %s (%d morceaux, %s, %.1f s)", key, len(chunks), ex.method, time.time() - t0)

    async def _fail(self, key: str, st, digest: str | None, error: str) -> None:  # type: ignore[no-untyped-def]
        await self.store.delete_path(key)  # pas d'anciens morceaux d'une version périmée
        log.warning("✘ %s : %s", key, error)
        self.progress.errors.append(f"{key} : {error}")
        self.db.save_file(
            key, sha=digest, size=st.st_size, mtime=st.st_mtime, status="error", error=error,
            chunks=0, indexed_at=time.time(),
        )

    def status(self) -> dict:
        return asdict(self.progress)
