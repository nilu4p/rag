"""API web + interface de chat."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import websearch
from .chat import Assistant
from .config import settings
from .db import DB
from .extract import SUPPORTED, is_supported
from .indexer import Indexer
from .jobs import Jobs
from .llm import Ollama
from .qdrant import Qdrant
from .retrieval import Retriever

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("rag")

STATIC = Path(__file__).parent / "static"


class State:
    db: DB
    llm: Ollama
    store: Qdrant
    indexer: Indexer
    assistant: Assistant
    jobs: Jobs
    watch_task: asyncio.Task


S = State()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings.docs_dir.mkdir(parents=True, exist_ok=True)
    (settings.docs_dir / "ajouts").mkdir(exist_ok=True)
    S.db = DB(settings.db_path)
    S.llm = Ollama()
    S.store = Qdrant()
    S.indexer = Indexer(S.db, S.llm, S.store)
    S.assistant = Assistant(S.db, S.llm, S.store, Retriever(S.llm, S.store))
    S.jobs = Jobs(S.db, S.assistant)
    S.watch_task = asyncio.create_task(S.indexer.watch())
    log.info("Prêt : Ollama %s (%s + %s), documents %s", settings.ollama_url, settings.chat_model, settings.embed_model, settings.docs_dir)
    yield
    S.watch_task.cancel()
    await S.llm.close()
    await S.store.close()


app = FastAPI(title="Assistant RAG", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


# ================================================================ accès
def auth(request: Request) -> None:
    header = request.headers.get("authorization", "")
    if settings.api_token and header.startswith("Bearer "):
        if secrets.compare_digest(header[7:].strip(), settings.api_token):
            return
    if header.startswith("Basic "):
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:  # noqa: BLE001
            user, pwd = "", ""
        if settings.chat_password and secrets.compare_digest(user, settings.chat_user) and secrets.compare_digest(
            pwd, settings.chat_password
        ):
            return
    raise HTTPException(401, "Identifiants requis", headers={"WWW-Authenticate": 'Basic realm="Assistant RAG", charset="UTF-8"'})


Auth = Depends(auth)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path == "/" or request.url.path.startswith("/static"):
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
        )
    return resp


# ================================================================ pages
@app.get("/", dependencies=[Auth])
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


# ================================================================ état
@app.get("/api/status", dependencies=[Auth])
async def status() -> dict:
    out: dict[str, Any] = {
        "chat_model": settings.chat_model,
        "embed_model": settings.embed_model,
        "ollama_url": settings.ollama_url,
        "web_search": settings.web_enabled,
        "indexing": S.indexer.status(),
    }
    try:
        names = {m["name"] for m in await S.llm.tags()}
        out["ollama"] = True
        out["models_missing"] = [m for m in (settings.chat_model, settings.embed_model) if m not in names and f"{m}:latest" not in names]
        out["loaded"] = [m["name"] for m in await S.llm.loaded()]
    except Exception as e:  # noqa: BLE001
        out["ollama"] = False
        out["ollama_error"] = str(e)
    try:
        out["chunks"] = await S.store.count()
        out["qdrant"] = True
    except Exception as e:  # noqa: BLE001
        out["qdrant"] = False
        out["qdrant_error"] = str(e)
    files = S.db.files()
    out["documents"] = sum(1 for f in files.values() if f["status"] == "ok")
    out["documents_errors"] = sum(1 for f in files.values() if f["status"] != "ok")
    return out


# ================================================================ documents
def _safe_path(rel_path: str) -> Path:
    p = (settings.docs_dir / rel_path).resolve()
    root = settings.docs_dir.resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(404, "Document introuvable")
    return p


@app.get("/api/documents", dependencies=[Auth])
async def documents() -> dict:
    return {"files": list(S.db.files().values()), "indexing": S.indexer.status(), "supported": sorted(SUPPORTED)}


@app.get("/api/documents/file", dependencies=[Auth])
async def document_file(path: str) -> FileResponse:
    p = _safe_path(path)
    # sandbox : un document HTML déposé ne peut exécuter aucun script dans l'origine de l'application
    return FileResponse(
        p, filename=p.name, content_disposition_type="inline",
        headers={"Content-Security-Policy": "sandbox; default-src 'none'; img-src data: 'self'; style-src 'unsafe-inline'"},
    )


def _clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", Path(name or "fichier").name)
    name = re.sub(r"[^\w.\- ()&,'+àâäéèêëîïôöùûüçœæÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]", "_", name).strip(" .")
    return name[:150] or "fichier"


@app.post("/api/documents", dependencies=[Auth])
async def upload(files: list[UploadFile] = File(...)) -> dict:
    dest_dir = settings.docs_dir / "ajouts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved, refused = [], []
    for f in files:
        name = _clean_name(f.filename or "")
        if not is_supported(Path(name)):
            refused.append(f"{name} (format non pris en charge)")
            continue
        dest = dest_dir / name
        stem, suffix = dest.stem, dest.suffix
        n = 2
        while dest.exists():
            dest = dest_dir / f"{stem} ({n}){suffix}"
            n += 1
        size = 0
        tmp = dest.with_name("." + dest.name + ".part")
        with tmp.open("wb") as out:
            while chunk := await f.read(1 << 20):
                size += len(chunk)
                if size > settings.max_file_mb * 1024 * 1024:
                    break
                out.write(chunk)
        if size > settings.max_file_mb * 1024 * 1024:
            tmp.unlink(missing_ok=True)
            refused.append(f"{name} (plus de {settings.max_file_mb} Mo)")
            continue
        tmp.rename(dest)
        saved.append(dest.relative_to(settings.docs_dir).as_posix())
    if saved:
        S.indexer.trigger()
    return {"saved": saved, "refused": refused}


@app.delete("/api/documents", dependencies=[Auth])
async def delete_document(path: str) -> dict:
    p = _safe_path(path)
    p.unlink()
    S.indexer.trigger()
    return {"deleted": path}


class IndexRequest(BaseModel):
    full: bool = False
    wait: bool = False


@app.post("/api/index", dependencies=[Auth])
async def reindex(req: IndexRequest) -> dict:
    if req.full or req.wait:
        if req.wait:
            # Attend la fin d'une éventuelle indexation en cours, puis synchronise
            async with S.indexer.lock:
                pass
            prog = await S.indexer.sync(full=req.full)
            return {"done": True, "indexing": S.indexer.status(), "message": prog.message}
        asyncio.create_task(S.indexer.sync(full=True))
        return {"started": True}
    S.indexer.trigger()
    return {"started": True, "indexing": S.indexer.status()}


# ================================================================ conversations
@app.get("/api/conversations", dependencies=[Auth])
async def conversations() -> dict:
    return {"conversations": S.db.conversations()}


@app.get("/api/conversations/{cid}", dependencies=[Auth])
async def conversation(cid: str) -> dict:
    conv = S.db.conversation(cid)
    if not conv:
        raise HTTPException(404, "Conversation introuvable")
    msgs = S.db.messages(cid)
    for m in msgs:
        m["live"] = m["status"] == "pending" and m["id"] in S.jobs.jobs
    return {"conversation": conv, "messages": msgs}


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@app.patch("/api/conversations/{cid}", dependencies=[Auth])
async def rename(cid: str, body: Rename) -> dict:
    S.db.rename_conversation(cid, body.title)
    return {"ok": True}


@app.delete("/api/conversations/{cid}", dependencies=[Auth])
async def delete_conversation(cid: str) -> dict:
    S.db.delete_conversation(cid)
    return {"ok": True}


# ================================================================ chat
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = None
    mode: str = "auto"


async def _ndjson(events: AsyncIterator[dict], head: dict | None = None) -> AsyncIterator[bytes]:
    if head:
        yield (json.dumps(head, ensure_ascii=False) + "\n").encode()
    async for ev in events:
        yield (json.dumps(ev, ensure_ascii=False) + "\n").encode()


def _stream(events: AsyncIterator[dict], head: dict | None = None) -> StreamingResponse:
    return StreamingResponse(
        _ndjson(events, head),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat", dependencies=[Auth])
async def chat(req: ChatRequest) -> StreamingResponse:
    cid = req.conversation_id
    if not cid or not S.db.conversation(cid):
        title = re.sub(r"\s+", " ", req.message).strip()
        cid = S.db.create_conversation(title[:60] + ("…" if len(title) > 60 else ""))
    mid = S.jobs.start(cid, req.message.strip(), req.mode)
    return _stream(S.jobs.follow(mid), {"type": "meta", "conversation_id": cid, "message_id": mid})


@app.get("/api/chat/{mid}/stream", dependencies=[Auth])
async def chat_resume(mid: str) -> StreamingResponse:
    if mid not in S.jobs.jobs:
        raise HTTPException(404, "Réponse terminée ou inconnue")
    return _stream(S.jobs.follow(mid))


@app.post("/api/chat/{mid}/stop", dependencies=[Auth])
async def chat_stop(mid: str) -> dict:
    return {"stopped": S.jobs.cancel(mid)}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20000)
    mode: str = "auto"


@app.post("/api/ask", dependencies=[Auth])
async def ask(req: AskRequest) -> JSONResponse:
    """Question simple, réponse JSON complète (n8n, scripts, terminal)."""
    t0 = time.time()
    final = await S.jobs.run_once(req.question, req.mode)
    if final["type"] == "error":
        return JSONResponse({"ok": False, "answer": final["text"], "seconds": round(time.time() - t0, 1)}, status_code=503)
    meta = final.get("meta") or {}
    return JSONResponse(
        {
            "ok": True,
            "answer": final["content"],
            "route": meta.get("route"),
            "sources": meta.get("sources", []),
            "warnings": meta.get("warnings", []),
            "unverified_numbers": meta.get("unverified", []),
            "seconds": meta.get("seconds"),
        }
    )


# ================================================================ diagnostic
@app.get("/api/doctor", dependencies=[Auth])
async def doctor() -> dict:
    """Teste chaque composant (utilisé par `make doctor`)."""
    checks: list[dict] = []

    async def check(name: str, coro) -> Any:  # type: ignore[no-untyped-def]
        t0 = time.time()
        try:
            detail = await coro
            checks.append({"name": name, "ok": True, "detail": detail, "seconds": round(time.time() - t0, 1)})
            return detail
        except Exception as e:  # noqa: BLE001
            checks.append({"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}", "seconds": round(time.time() - t0, 1)})
            return None

    async def ollama_models() -> str:
        names = {m["name"] for m in await S.llm.tags()}
        missing = [m for m in (settings.chat_model, settings.embed_model) if m not in names and f"{m}:latest" not in names]
        if missing:
            raise RuntimeError(f"modèle(s) absent(s) : {', '.join(missing)} → make models")
        return f"{settings.ollama_url} : {settings.chat_model} + {settings.embed_model}"

    async def embed() -> str:
        v = await S.llm.embed(["test"])
        return f"dimension {len(v[0])}"

    async def qdrant() -> str:
        return f"{await S.store.count()} morceaux indexés"

    async def searx() -> str:
        if not settings.web_enabled:
            return "désactivée (WEB_SEARCH=off)"
        res = await websearch.search("météo Paris", 3)
        return f"{len(res)} résultat(s)"

    async def llm() -> str:
        t0 = time.time()
        txt = await S.llm.chat([{"role": "user", "content": "Réponds juste : OK"}], num_predict=400)
        return f"« {txt[:40]} » en {time.time() - t0:.1f} s"

    ok = await check("Ollama (modèles)", ollama_models())
    if ok:
        await check("Embeddings", embed())
    await check("Qdrant", qdrant())
    await check("Recherche internet (SearXNG)", searx())
    if ok:
        await check("LLM (génération)", llm())
    try:
        mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        avail = int(mem["MemAvailable"].split()[0]) / 1024 / 1024
        total = int(mem["MemTotal"].split()[0]) / 1024 / 1024
        checks.append({"name": "Mémoire", "ok": avail > 4, "detail": f"{avail:.1f} Go libres sur {total:.1f} Go"})
    except Exception:  # noqa: BLE001
        pass
    return {"checks": checks, "indexing": S.indexer.status()}
