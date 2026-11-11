"""Recherche hybride : sens (vecteurs denses) + mots-clés rares (vecteurs creux), fusion RRF."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from . import sparse
from .config import settings
from .llm import Ollama
from .qdrant import Qdrant

RRF_K = 60


@dataclass
class Passage:
    path: str
    name: str
    page: int | None
    label: str
    heading: str
    text: str
    chunk: int
    score: float  # similarité cosinus (sens)
    keyword_rank: int | None  # rang dans la recherche par mots-clés (0 = meilleur)
    fused: float = 0.0

    @property
    def where(self) -> str:
        if self.label:
            return f"{self.name}, {self.label}"
        if self.page:
            return f"{self.name}, p. {self.page}"
        return self.name


class Retriever:
    def __init__(self, llm: Ollama, store: Qdrant):
        self.llm, self.store = llm, store

    async def search(self, query: str, top_k: int | None = None, min_score: float | None = None) -> list[Passage]:
        top_k = top_k or settings.top_k
        min_score = settings.min_score if min_score is None else min_score
        depth = max(20, top_k * 4)
        vec = (await self.llm.embed([query]))[0]
        dense, kw = await asyncio.gather(
            self.store.query_dense(vec, depth),
            self.store.query_sparse(sparse.query_vector(query), depth),
        )
        by_id: dict[str, Passage] = {}
        dense_score: dict[str, float] = {}
        for rank, pt in enumerate(dense):
            dense_score[pt["id"]] = pt["score"]
            by_id[pt["id"]] = _passage(pt, pt["score"], None)
            by_id[pt["id"]].fused += 1 / (RRF_K + rank)
        for rank, pt in enumerate(kw):
            p = by_id.get(pt["id"])
            if p is None:
                # Trouvé seulement par mots-clés : on estime sa similarité de sens
                p = by_id[pt["id"]] = _passage(pt, 0.0, rank)
            p.keyword_rank = rank
            p.fused += 1 / (RRF_K + rank)

        missing = [pid for pid, p in by_id.items() if pid not in dense_score and p.keyword_rank is not None and p.keyword_rank < 5]
        if missing:
            texts = [by_id[pid].text for pid in missing]
            for pid, v in zip(missing, await self.llm.embed(texts)):
                by_id[pid].score = _cos(vec, v)

        kept = []
        for p in by_id.values():
            strong_keyword = p.keyword_rank is not None and p.keyword_rank < 3 and p.score >= min_score - 0.08
            if p.score >= min_score or strong_keyword:
                kept.append(p)
        kept.sort(key=lambda p: p.fused, reverse=True)
        return kept[:top_k]


def _passage(pt: dict, score: float, kw_rank: int | None) -> Passage:
    pl = pt.get("payload") or {}
    return Passage(
        path=pl.get("path", ""),
        name=pl.get("name", ""),
        page=pl.get("page"),
        label=pl.get("label", ""),
        heading=pl.get("heading", ""),
        text=pl.get("text", ""),
        chunk=pl.get("chunk", 0),
        score=score,
        keyword_rank=kw_rank,
    )


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return num / (na * nb) if na and nb else 0.0
