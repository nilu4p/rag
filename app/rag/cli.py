"""Commandes en terminal (lancées par make dans le conteneur) : interrogent l'API locale.

    python -m rag.cli status | doctor | index | reindex | ask "question" [mode]
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

from .config import settings

API = "http://127.0.0.1:8000"
G, R, Y, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"


def client(timeout: float = 30) -> httpx.Client:
    return httpx.Client(base_url=API, timeout=timeout, headers={"Authorization": f"Bearer {settings.api_token}"})


def wait_ready(seconds: int = 60) -> None:
    for _ in range(seconds):
        try:
            if httpx.get(f"{API}/api/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    sys.exit(f"{R}✘ Le service rag ne répond pas{X} (make logs s=rag)")


def status() -> None:
    with client() as c:
        s = c.get("/api/status").json()
    ok = lambda b: f"{G}✔{X}" if b else f"{R}✘{X}"  # noqa: E731
    print(f"{ok(s.get('ollama'))} Ollama ({s['ollama_url']}) : {s['chat_model']} + {s['embed_model']}"
          + (f"  {R}manquant(s) : {', '.join(s['models_missing'])} → make models{X}" if s.get("models_missing") else "")
          + (f"  {R}{s.get('ollama_error', '')}{X}" if not s.get("ollama") else ""))
    if s.get("loaded"):
        print(f"  → en mémoire : {', '.join(s['loaded'])}")
    print(f"{ok(s.get('qdrant'))} Qdrant : {s.get('chunks', '?')} morceaux, {s['documents']} document(s) indexé(s)"
          + (f", {Y}{s['documents_errors']} non lisible(s) (voir make docs){X}" if s.get("documents_errors") else ""))
    print(f"{ok(True)} Recherche internet : {'activée' if s['web_search'] else 'désactivée'}")
    ix = s["indexing"]
    if ix["running"]:
        print(f"{B}→ Indexation en cours : {ix['done']}/{ix['total']} ({ix['current']}){X}")
    elif ix.get("message"):
        print(f"→ Dernière synchronisation : {ix['message']}")


def docs() -> None:
    with client() as c:
        files = c.get("/api/documents").json()["files"]
    if not files:
        print("Aucun document. Dépose des fichiers dans documents/ ou via l'interface.")
    for f in files:
        mark = f"{G}✔{X}" if f["status"] == "ok" else f"{R}✘{X}"
        extra = f"{f['chunks']} morceaux, {f['method']}" if f["status"] == "ok" else (f["error"] or f["status"])
        print(f"{mark} {f['path']}  ({extra})")


def doctor() -> None:
    print("→ Tests en cours (le premier chargement du LLM peut prendre 1 à 2 min)…")
    with client(timeout=600) as c:
        d = c.get("/api/doctor").json()
    for ch in d["checks"]:
        mark = f"{G}✔{X}" if ch["ok"] else f"{R}✘{X}"
        secs = f" ({ch['seconds']} s)" if ch.get("seconds") is not None else ""
        print(f"{mark} {ch['name']}{secs} : {ch['detail']}")
    if any(not c["ok"] and c["name"].startswith("Ollama") for c in d["checks"]):
        print(f"\n{Y}Ollama du PC injoignable depuis Docker ?{X} Il doit écouter sur toutes les interfaces :")
        print("   make ollama-config   (une seule fois, demande le mot de passe sudo)")


def index(full: bool) -> None:
    print("→ Réindexation complète…" if full else "→ Synchronisation de documents/…")
    with client(timeout=None) as c:  # type: ignore[arg-type]
        r = c.post("/api/index", json={"full": full, "wait": True}).json()
    ix = r["indexing"]
    print(f"{G}✔{X} {r.get('message') or 'Rien de nouveau'}")
    for e in ix.get("errors", []):
        print(f"  {R}✘{X} {e}")


def ask(question: str, mode: str = "auto") -> None:
    if not question.strip():
        sys.exit('Usage : make ask Q="ta question"')
    print(f"→ Question envoyée ({settings.chat_model} sur CPU : compte 20 s à 2 min)…\n")
    t0 = time.time()
    with client(timeout=None) as c:  # type: ignore[arg-type]
        r = c.post("/api/ask", json={"question": question, "mode": mode})
    d = r.json()
    print(d["answer"])
    for s in d.get("sources") or []:
        if not s.get("cited"):
            continue
        where = s.get("url") or (s.get("path", "") + (f", p. {s['page']}" if s.get("page") else ""))
        print(f"  [{s['n']}] {where}")
    for w in d.get("warnings") or []:
        print(f"{Y}⚠ {w}{X}")
    if d.get("unverified_numbers"):
        print(f"{Y}🔎 À vérifier (absent des sources) : {', '.join(d['unverified_numbers'])}{X}")
    print(f"\n({d.get('route') or ''}, {time.time() - t0:.0f} s)")


def main() -> None:
    args = sys.argv[1:] or ["status"]
    cmd = args[0]
    wait_ready()
    if cmd == "status":
        status()
    elif cmd == "docs":
        docs()
    elif cmd == "doctor":
        doctor()
    elif cmd in {"index", "reindex"}:
        index(cmd == "reindex")
    elif cmd == "ask":
        ask(" ".join(args[1:2]) if len(args) > 1 else "", args[2] if len(args) > 2 and args[2] else "auto")
    elif cmd == "json":  # débogage : make cli c="json /api/status"
        with client() as c:
            print(json.dumps(c.get(args[1]).json(), indent=2, ensure_ascii=False))
    else:
        sys.exit(f"Commande inconnue : {cmd}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
