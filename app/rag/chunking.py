"""Découpage des pages en morceaux qui se chevauchent, en respectant paragraphes et phrases."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .extract import Page

SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÀ-ÖØ-Þ0-9«\"(\[])")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


@dataclass
class Chunk:
    text: str
    page: int | None
    label: str
    heading: str


def _pieces(text: str, size: int) -> list[str]:
    """Unités insécables : paragraphes, ou phrases (voire tranches) s'ils sont trop longs."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            out.append(para)
            continue
        lines = para.split("\n") if para.count("\n") >= 3 else SENTENCE_RE.split(para)
        for s in lines:
            s = s.strip()
            while len(s) > size:
                cut = s.rfind(" ", 0, size)
                cut = cut if cut > size // 2 else size
                out.append(s[:cut].strip())
                s = s[cut:].strip()
            if s:
                out.append(s)
    return out


def split_page(page: Page, size: int, overlap: int, heading: str = "") -> tuple[list[Chunk], str]:
    chunks: list[Chunk] = []
    current: list[str] = []
    cur_len = 0
    cur_heading = heading
    chunk_heading = heading

    def flush() -> None:
        nonlocal current, cur_len, chunk_heading
        if not current:
            return
        chunks.append(Chunk("\n\n".join(current), page.number, page.label, chunk_heading))
        # Chevauchement : on reprend les derniers éléments jusqu'à `overlap` caractères
        tail: list[str] = []
        n = 0
        for piece in reversed(current):
            if n + len(piece) > overlap:
                break
            tail.insert(0, piece)
            n += len(piece) + 2
        if not tail and overlap > 0:
            # Dernier paragraphe plus long que le chevauchement : on en reprend la fin (à partir d'un mot)
            last = current[-1]
            start = last.find(" ", len(last) - overlap)
            if 0 < start < len(last) - 20:
                tail = ["…" + last[start + 1 :]]
                n = len(tail[0]) + 2
        current, cur_len = tail, n
        chunk_heading = cur_heading

    for piece in _pieces(page.text, size):
        m = HEADING_RE.match(piece.split("\n", 1)[0])
        if m:
            cur_heading = m.group(2).strip()[:120]
            # Un nouveau titre démarre de préférence un nouveau morceau
            if cur_len > size // 3:
                flush()
                current, cur_len = [], 0
            if not current:
                chunk_heading = cur_heading
        if cur_len + len(piece) > size and current:
            flush()
        current.append(piece)
        cur_len += len(piece) + 2
    # Dernier morceau : on évite d'émettre un morceau qui ne serait que du chevauchement
    if current and (not chunks or "\n\n".join(current) not in chunks[-1].text):
        chunks.append(Chunk("\n\n".join(current), page.number, page.label, chunk_heading))
    return chunks, cur_heading


def split_pages(pages: list[Page], size: int, overlap: int) -> list[Chunk]:
    out: list[Chunk] = []
    heading = ""
    for p in pages:
        if not p.text.strip():
            continue
        chunks, heading = split_page(p, size, overlap, heading)
        out.extend(chunks)
    return out
