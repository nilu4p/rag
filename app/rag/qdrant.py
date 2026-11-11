"""Client Qdrant minimal (API REST) : collection hybride dense + mots-clés."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("rag.qdrant")


class QdrantError(RuntimeError):
    pass


class Qdrant:
    def __init__(self, url: str | None = None, api_key: str | None = None, collection: str | None = None):
        headers = {}
        key = settings.qdrant_api_key if api_key is None else api_key
        if key:
            headers["api-key"] = key
        self.collection = collection or settings.collection
        self._client = httpx.AsyncClient(base_url=url or settings.qdrant_url, headers=headers, timeout=60)

    async def close(self) -> None:
        await self._client.aclose()

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = await self._client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise QdrantError(f"Qdrant injoignable : {e!r}") from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise QdrantError(f"Qdrant {method} {path} → {r.status_code} : {r.text[:300]}")
        return r.json().get("result")

    @property
    def _c(self) -> str:
        return f"/collections/{self.collection}"

    # ---------- Collection ----------
    async def info(self) -> dict | None:
        return await self._req("GET", self._c)

    async def vector_size(self) -> int | None:
        info = await self.info()
        if not info:
            return None
        vectors = info.get("config", {}).get("params", {}).get("vectors", {})
        dense = vectors.get("dense") if isinstance(vectors, dict) else None
        return (dense or {}).get("size")

    async def create(self, dim: int) -> None:
        await self._req(
            "PUT",
            self._c,
            json={
                "vectors": {"dense": {"size": dim, "distance": "Cosine"}},
                "sparse_vectors": {"sparse": {"modifier": "idf"}},
                "on_disk_payload": True,
            },
        )
        for fieldname, schema in (("path", "keyword"), ("chunk", "integer")):
            await self._req(
                "PUT", f"{self._c}/index?wait=true", json={"field_name": fieldname, "field_schema": schema}
            )
        log.info("Collection %s créée (dimension %d)", self.collection, dim)

    async def drop(self) -> None:
        await self._req("DELETE", self._c)

    async def ensure(self, dim: int) -> bool:
        """Crée la collection si besoin. Renvoie False si elle existe avec une autre dimension."""
        size = await self.vector_size()
        if size is None:
            await self.create(dim)
            return True
        return size == dim

    async def count(self) -> int:
        res = await self._req("POST", f"{self._c}/points/count", json={"exact": True})
        return int((res or {}).get("count", 0))

    # ---------- Points ----------
    async def upsert(self, points: list[dict]) -> None:
        for i in range(0, len(points), 64):
            await self._req("PUT", f"{self._c}/points?wait=true", json={"points": points[i : i + 64]})

    async def delete_path(self, path: str, keep_sha: str | None = None) -> None:
        flt: dict[str, Any] = {"must": [{"key": "path", "match": {"value": path}}]}
        if keep_sha:
            flt["must_not"] = [{"key": "sha", "match": {"value": keep_sha}}]
        await self._req("POST", f"{self._c}/points/delete?wait=true", json={"filter": flt})

    async def scroll_path(self, path: str, limit: int = 5000) -> list[dict]:
        out: list[dict] = []
        offset = None
        while True:
            body: dict[str, Any] = {
                "filter": {"must": [{"key": "path", "match": {"value": path}}]},
                "limit": 256,
                "with_payload": True,
                "with_vector": False,
            }
            if offset is not None:
                body["offset"] = offset
            res = await self._req("POST", f"{self._c}/points/scroll", json=body)
            if not res:
                break
            out.extend(p["payload"] for p in res.get("points", []))
            offset = res.get("next_page_offset")
            if offset is None or len(out) >= limit:
                break
        out.sort(key=lambda p: p.get("chunk", 0))
        return out

    async def query_dense(self, vector: list[float], limit: int) -> list[dict]:
        res = await self._req(
            "POST",
            f"{self._c}/points/query",
            json={"query": vector, "using": "dense", "limit": limit, "with_payload": True},
        )
        return (res or {}).get("points", [])

    async def query_sparse(self, sparse: dict, limit: int) -> list[dict]:
        if not sparse["indices"]:
            return []
        res = await self._req(
            "POST",
            f"{self._c}/points/query",
            json={"query": sparse, "using": "sparse", "limit": limit, "with_payload": True},
        )
        return (res or {}).get("points", [])
