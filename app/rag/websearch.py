"""Recherche sur internet via SearXNG (auto-hébergé), puis lecture et nettoyage des pages."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from . import sparse
from .config import settings
from .safety import sanitize

log = logging.getLogger("rag.web")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
MAX_BYTES = 3_000_000
PAGE_CHARS = 3500
SKIP_DOMAINS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com")


@dataclass
class WebPage:
    url: str
    title: str
    snippet: str
    text: str = ""
    suspicious: bool = False


class WebError(RuntimeError):
    pass


def _is_public_host(host: str) -> bool:
    """Refuse localhost, IP privées et services internes (protection SSRF)."""
    if not host or host.endswith((".local", ".internal", ".lan", ".home", ".localhost")) or "." not in host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


async def _safe_url(url: str) -> bool:
    u = urlparse(url)
    if u.scheme not in {"http", "https"} or not u.hostname:
        return False
    return await asyncio.to_thread(_is_public_host, u.hostname)


async def search(query: str, n: int | None = None) -> list[WebPage]:
    n = n or settings.web_results
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{settings.searxng_url}/search",
                params={"q": query, "format": "json", "language": settings.web_lang, "safesearch": 0},
            )
    except httpx.HTTPError as e:
        raise WebError(f"SearXNG injoignable : {e!r}") from e
    if r.status_code != 200:
        raise WebError(f"SearXNG a répondu {r.status_code}")
    pages: list[WebPage] = []
    seen: set[str] = set()
    for res in r.json().get("results", []):
        url = res.get("url") or ""
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if not url or url in seen or any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS):
            continue
        seen.add(url)
        pages.append(WebPage(url=url, title=(res.get("title") or host).strip(), snippet=(res.get("content") or "").strip()))
        if len(pages) >= n + 3:  # quelques pages de réserve si certaines sont illisibles
            break
    return pages


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    for _ in range(5):  # redirections suivies à la main pour contrôler chaque destination
        if not await _safe_url(url):
            return None
        async with client.stream("GET", url) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code != 200:
                return None
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                return None
            data = b""
            async for chunk in r.aiter_bytes():
                data += chunk
                if len(data) > MAX_BYTES:
                    break
            return data.decode(r.encoding or "utf-8", errors="replace")
    return None


def _html_to_text(html: str) -> str:
    try:
        import trafilatura

        text = trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True)
        if text:
            return text
    except Exception:  # noqa: BLE001
        pass
    html = re.sub(r"(?is)<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def best_passages(text: str, query: str, limit: int = PAGE_CHARS) -> str:
    """Garde les paragraphes qui parlent de la question (pages longues), dans l'ordre d'origine."""
    if len(text) <= limit:
        return text
    paras = [p.strip() for p in re.split(r"\n+", text) if len(p.strip()) > 30]
    q = set(sparse.tokenize(query))
    scored = []
    for i, para in enumerate(paras):
        toks = sparse.tokenize(para)
        hits = sum(1 for t in toks if t in q)
        scored.append((hits / (1 + len(toks) ** 0.5) + (0.3 if i < 3 else 0), i, para))
    chosen, total = [], 0
    for _, i, para in sorted(scored, reverse=True):
        if total + len(para) > limit:
            continue
        chosen.append((i, para))
        total += len(para)
    return "\n".join(p for _, p in sorted(chosen))


async def read_pages(pages: list[WebPage], query: str, n: int | None = None) -> list[WebPage]:
    n = n or settings.web_results
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7"},
        follow_redirects=False,
    ) as client:

        async def one(p: WebPage) -> WebPage:
            try:
                html = await asyncio.wait_for(_fetch(client, p.url), timeout=15)
            except Exception as e:  # noqa: BLE001 - une page illisible ne doit rien bloquer
                log.info("Page illisible %s : %s", p.url, e)
                html = None
            text = await asyncio.to_thread(_html_to_text, html) if html else ""
            if len(text) < 200:
                text = p.snippet  # site qui exige JavaScript ou bloque les robots : résumé du moteur
            p.text, p.suspicious = sanitize(best_passages(text, query))
            return p

        done = await asyncio.gather(*(one(p) for p in pages))
    useful = [p for p in done if p.text.strip()]
    return useful[:n]
