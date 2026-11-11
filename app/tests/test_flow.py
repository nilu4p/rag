"""Tests de bout en bout avec un faux Ollama et un faux Qdrant (en mémoire)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
from pathlib import Path

import pytest

from rag import sparse, websearch
from rag.chat import Assistant
from rag.config import settings
from rag.db import DB
from rag.indexer import Indexer
from rag.retrieval import Retriever


# ---------------------------------------------------------------- faux services
class FakeLLM:
    """Embeddings = sac de mots haché ; réponses de chat scriptées selon le prompt."""

    def __init__(self):
        self.replies: list[str] = []
        self.calls: list[list[dict]] = []

    async def embed(self, texts, batch=16):
        out = []
        for t in texts:
            v = [0.0] * 64
            for tok in sparse.tokenize(t):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out

    async def chat_stream(self, messages, **kw):
        self.calls.append(messages)
        reply = self.replies.pop(0) if self.replies else "OK"
        yield "thinking", "hmm"
        for i in range(0, len(reply), 7):
            yield "content", reply[i : i + 7]

    async def chat(self, messages, **kw):
        out = ""
        async for k, t in self.chat_stream(messages, **kw):
            if k == "content":
                out += t
        return out

    async def tags(self):
        return [{"name": settings.chat_model}, {"name": settings.embed_model + ":latest"}]

    async def loaded(self):
        return []

    async def close(self):
        pass


class FakeQdrant:
    def __init__(self):
        self.points: dict[str, dict] = {}
        self.dim = None

    async def ensure(self, dim):
        if self.dim is None:
            self.dim = dim
            return True
        return self.dim == dim

    async def create(self, dim):
        self.dim, self.points = dim, {}

    async def drop(self):
        self.dim, self.points = None, {}

    async def count(self):
        return len(self.points)

    async def upsert(self, points):
        for p in points:
            self.points[p["id"]] = p

    async def delete_path(self, path, keep_sha=None):
        for k in [k for k, p in self.points.items() if p["payload"]["path"] == path and p["payload"]["sha"] != keep_sha]:
            del self.points[k]

    async def scroll_path(self, path, limit=5000):
        return sorted((p["payload"] for p in self.points.values() if p["payload"]["path"] == path), key=lambda x: x["chunk"])

    async def query_dense(self, vector, limit):
        scored = [
            {"id": k, "score": sum(a * b for a, b in zip(vector, p["vector"]["dense"])), "payload": p["payload"]}
            for k, p in self.points.items()
        ]
        return sorted(scored, key=lambda x: -x["score"])[:limit]

    async def query_sparse(self, sp, limit):
        q = dict(zip(sp["indices"], sp["values"]))
        scored = []
        for k, p in self.points.items():
            d = dict(zip(p["vector"]["sparse"]["indices"], p["vector"]["sparse"]["values"]))
            s = sum(q[i] * d[i] for i in q if i in d)
            if s:
                scored.append({"id": k, "score": s, "payload": p["payload"]})
        return sorted(scored, key=lambda x: -x["score"])[:limit]

    async def close(self):
        pass


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(settings, "docs_dir", docs)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "state")
    monkeypatch.setattr(settings, "min_score", 0.2)
    monkeypatch.setattr(settings, "web_search", "auto")
    monkeypatch.setattr(settings, "query_router", True)
    db = DB(settings.db_path)
    llm, store = FakeLLM(), FakeQdrant()
    idx = Indexer(db, llm, store)
    asst = Assistant(db, llm, store, Retriever(llm, store))
    return docs, db, llm, store, idx, asst


def run(coro):
    return asyncio.run(coro)


async def collect(agen):
    return [ev async for ev in agen]


CV = """# Expériences

Développeur Python chez ACME à Toulouse de 2019 à 2022 : API, données, automatisation.

# Formation

Master informatique à l'Université de Guyane, obtenu en 2018."""

JARDIN = "Notes de jardin : planter les tomates en mars, arroser les courgettes le soir."


# ---------------------------------------------------------------- indexation
def test_incremental_sync(env):
    docs, db, llm, store, idx, _ = env
    (docs / "cv.md").write_text(CV, encoding="utf-8")
    (docs / "jardin.txt").write_text(JARDIN, encoding="utf-8")
    (docs / ".cache").mkdir()
    (docs / ".cache" / "x.txt").write_text("caché")
    (docs / "image.xyz").write_text("format inconnu")

    p = run(idx.sync())
    assert p.added == 2 and not p.errors
    assert set(db.files()) == {"cv.md", "jardin.txt"}
    n = len(store.points)

    p = run(idx.sync())  # rien n'a changé
    assert len(store.points) == n

    (docs / "jardin.txt").write_text(JARDIN + " Récolter les haricots en juillet.", encoding="utf-8")
    os.utime(docs / "jardin.txt", (1, 1))
    p = run(idx.sync())
    assert p.updated == 1
    texts = [pt["payload"]["text"] for pt in store.points.values() if pt["payload"]["path"] == "jardin.txt"]
    assert len(texts) == 1 and "haricots" in texts[0]  # l'ancienne version a disparu

    (docs / "jardin.txt").unlink()
    p = run(idx.sync())
    assert p.removed == 1 and "jardin.txt" not in db.files()
    assert all(pt["payload"]["path"] == "cv.md" for pt in store.points.values())


def test_unreadable_file_reported(env):
    docs, db, _, _, idx, _ = env
    (docs / "casse.pdf").write_bytes(b"%PDF-1.4 pas vraiment un pdf")
    p = run(idx.sync())
    assert p.errors and db.files()["casse.pdf"]["status"] == "error"


# ---------------------------------------------------------------- chat
def test_docs_answer_with_citations(env):
    docs, db, llm, _, idx, asst = env
    (docs / "cv.md").write_text(CV, encoding="utf-8")
    (docs / "jardin.txt").write_text(JARDIN, encoding="utf-8")
    run(idx.sync())
    llm.replies = [
        '{"route": "docs", "query": "Où ai-je travaillé comme développeur Python ?"}',
        "Tu as été développeur Python chez **ACME** à Toulouse de 2019 à 2022 [1][7], soit 45 mois [1].",
    ]
    evs = run(collect(asst.answer("où j'ai bossé ?", [])))
    types = [e["type"] for e in evs]
    assert "token" in types and "thinking" in types and types[-1] == "done"
    done = evs[-1]
    assert done["meta"]["route"] == "docs"
    assert "[7]" not in done["content"] and "[1]" in done["content"]
    assert done["meta"]["sources"][0]["path"] == "cv.md" and done["meta"]["sources"][0]["cited"]
    assert "45" in done["meta"]["unverified"]
    assert any("inexistant" in w for w in done["meta"]["warnings"])
    # Le prompt de réponse contient bien les extraits numérotés et la question réécrite
    prompt = llm.calls[-1][1]["content"]
    assert '<extrait n="1"' in prompt and "Où ai-je travaillé comme développeur Python" in prompt


def test_not_found_falls_back_to_web(env, monkeypatch):
    docs, _, llm, _, idx, asst = env
    (docs / "cv.md").write_text(CV, encoding="utf-8")
    run(idx.sync())

    async def fake_search(q, n=None):
        return [websearch.WebPage(url="https://meteo.example.org/cayenne", title="Météo Cayenne", snippet="Pluie, 29 °C")]

    async def fake_read(pages, q, n=None):
        for p in pages:
            p.text = "Demain à Cayenne : averses, 29 °C."
        return pages

    monkeypatch.setattr(websearch, "search", fake_search)
    monkeypatch.setattr(websearch, "read_pages", fake_read)
    llm.replies = [
        '{"route": "docs", "query": "Développeur Python météo"}',
        "NON_TROUVE",
        "Demain à Cayenne : averses et 29 °C [1].",
    ]
    evs = run(collect(asst.answer("développeur python météo", [])))
    assert "reset" in [e["type"] for e in evs]
    assert not any(e["type"] == "token" and "NON_TROUVE" in e["text"] for e in evs)  # la sentinelle n'est jamais affichée
    done = evs[-1]
    assert done["meta"]["route"] == "web" and done["meta"]["fallback"]
    assert done["meta"]["sources"][0]["url"].startswith("https://meteo.example.org")


def test_doc_mode_never_uses_web(env, monkeypatch):
    docs, _, llm, _, idx, asst = env
    (docs / "cv.md").write_text(CV, encoding="utf-8")
    run(idx.sync())

    async def boom(*a, **k):
        raise AssertionError("internet ne doit pas être utilisé")

    monkeypatch.setattr(websearch, "search", boom)
    llm.replies = ["NON_TROUVE"]
    done = run(collect(asst.answer("/doc quel est le prix du pain ?", [])))[-1]
    assert done["type"] == "done" and done["meta"]["route"] == "docs"
    assert "ne contiennent pas" in done["content"]


def test_chat_route_uses_history(env):
    _, _, llm, _, _, asst = env
    llm.replies = ['{"route": "chat", "query": "traduire"}', "Hello, how are you?"]
    hist = [{"role": "user", "content": "Bonjour"}, {"role": "assistant", "content": "Salut !"}]
    done = run(collect(asst.answer("traduis « bonjour, comment vas-tu » en anglais", hist)))[-1]
    assert done["meta"]["route"] == "chat" and done["content"] == "Hello, how are you?"
    assert llm.calls[-1][1]["content"] == "Bonjour"


def test_router_garbage_defaults_to_docs(env):
    docs, _, llm, _, idx, asst = env
    (docs / "cv.md").write_text(CV, encoding="utf-8")
    run(idx.sync())
    llm.replies = ["je ne sais pas faire du JSON", "Master en 2018 [1]."]
    done = run(collect(asst.answer("quel diplôme master ?", [])))[-1]
    assert done["meta"]["route"] == "docs"


def test_commands(env):
    docs, _, llm, _, idx, asst = env
    (docs / "cv.md").write_text(CV, encoding="utf-8")
    run(idx.sync())
    out = run(collect(asst.answer("/docs", [])))[-1]
    assert "cv.md" in out["content"]
    assert "Commandes" in run(collect(asst.answer("/aide", [])))[-1]["content"]
    assert "inconnue" in run(collect(asst.answer("/truc", [])))[-1]["content"]
    llm.replies = ["Résumé : CV d'un développeur Python."]
    out = run(collect(asst.answer("/resume cv", [])))[-1]
    assert out["meta"]["route"] == "summary" and "Résumé" in out["content"]
    assert "ACME" in llm.calls[-1][1]["content"]  # le document entier a été lu
    assert "Aucun document" in run(collect(asst.answer("/resume facture", [])))[-1]["content"]


def test_llm_down_gives_clear_error(env):
    _, _, llm, _, _, asst = env
    from rag.llm import LLMError

    async def down(*a, **k):
        raise LLMError("connexion refusée")
        yield  # noqa

    llm.chat_stream = down
    ev = run(collect(asst.answer("bonjour", [])))[-1]
    assert ev["type"] == "error" and "Ollama" in ev["text"]


# ---------------------------------------------------------------- API HTTP
def test_api_end_to_end(env, monkeypatch):
    from fastapi.testclient import TestClient

    import rag.main as main

    docs, db, llm, store, idx, asst = env
    monkeypatch.setattr(settings, "chat_password", "secret")
    monkeypatch.setattr(settings, "api_token", "tok")
    monkeypatch.setattr(settings, "index_interval", 3600)
    monkeypatch.setattr(main, "Ollama", lambda: llm)
    monkeypatch.setattr(main, "Qdrant", lambda: store)

    with TestClient(main.app) as c:
        assert c.get("/").status_code == 401
        basic = {"Authorization": "Basic " + base64.b64encode(b"moi:secret").decode()}
        bad = {"Authorization": "Basic " + base64.b64encode(b"moi:faux").decode()}
        assert c.get("/api/status", headers=bad).status_code == 401
        assert c.get("/", headers=basic).status_code == 200
        h = {"Authorization": "Bearer tok"}

        r = c.post("/api/documents", headers=h, files=[("files", ("cv.md", CV.encode(), "text/markdown")),
                                                       ("files", ("virus.exe", b"MZ", "application/octet-stream"))])
        assert r.json()["saved"] == ["ajouts/cv.md"] and r.json()["refused"]
        r = c.post("/api/index", headers=h, json={"wait": True})
        assert r.json()["done"] and main.S.db.files()["ajouts/cv.md"]["status"] == "ok"

        assert c.get("/api/documents/file", headers=h, params={"path": "../../etc/passwd"}).status_code == 404
        assert c.get("/api/documents/file", headers=h, params={"path": "ajouts/cv.md"}).text.startswith("# Exp")

        llm.replies = ['{"route": "docs", "query": "Développeur Python : quelle entreprise ?"}', "Chez ACME [1]."]
        with c.stream("POST", "/api/chat", headers=h, json={"message": "Où ai-je travaillé ?"}) as r:
            events = [json.loads(line) for line in r.iter_lines() if line.strip()]
        assert events[0]["type"] == "meta" and events[-1]["type"] == "done"
        cid = events[0]["conversation_id"]
        conv = c.get(f"/api/conversations/{cid}", headers=h).json()
        assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
        assert conv["messages"][1]["status"] == "done" and conv["messages"][1]["meta"]["sources"]

        # 2e question dans la même conversation : l'historique est transmis à l'aiguillage
        llm.replies = ['{"route": "docs", "query": "Où était ACME développeur ?"}', "À Toulouse [1]."]
        with c.stream("POST", "/api/chat", headers=h, json={"message": "c'était où ?", "conversation_id": cid}) as r:
            events = [json.loads(line) for line in r.iter_lines() if line.strip()]
        assert events[-1]["content"] == "À Toulouse [1]."
        assert "Chez ACME" in llm.calls[-2][1]["content"]

        llm.replies = ['{"route": "chat", "query": "x"}', "Bonjour !"]
        r = c.post("/api/ask", headers=h, json={"question": "salut"})
        assert r.json()["answer"] == "Bonjour !" and r.json()["route"] == "chat"

        assert len(c.get("/api/conversations", headers=h).json()["conversations"]) == 1
        c.delete(f"/api/conversations/{cid}", headers=h)
        assert c.get("/api/conversations", headers=h).json()["conversations"] == []

        c.delete("/api/documents", headers=h, params={"path": "ajouts/cv.md"})
        assert not (docs / "ajouts" / "cv.md").exists()
