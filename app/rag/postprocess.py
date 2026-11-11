"""Vérifications après génération : citations valides, chiffres présents dans les sources."""
from __future__ import annotations

import re

from .prompts import NOT_FOUND

CITE_RE = re.compile(r"\[\s*(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\s*\]")
NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![\w])")


def fix_citations(answer: str, n_sources: int) -> tuple[str, list[int], int]:
    """Normalise [1, 2] en [1][2], retire les numéros inexistants. Renvoie (texte, cités, retirés)."""
    cited: list[int] = []
    removed = 0

    def repl(m: re.Match) -> str:
        nonlocal removed
        nums = [int(x) for x in re.split(r"[,;]", m.group(1))]
        valid = []
        for n in nums:
            if 1 <= n <= n_sources:
                valid.append(n)
                if n not in cited:
                    cited.append(n)
            else:
                removed += 1
        return "".join(f"[{n}]" for n in dict.fromkeys(valid))

    text = CITE_RE.sub(repl, answer)
    text = re.sub(r"[ \t]+([.,;:])", r"\1", text)
    return text, cited, removed


def is_not_found(answer: str) -> bool:
    a = answer.strip().strip("*_`'\" .")
    if a.upper().startswith(NOT_FOUND) or (NOT_FOUND in answer and len(answer) < 250):
        return True
    return False


def strip_sentinel(answer: str) -> str:
    return answer.replace(NOT_FOUND, "").strip()


def _norm_forms(raw: str) -> set[str]:
    s = re.sub(r"[   ]", "", raw)
    forms = {s.replace(",", ".")}
    if "." in s and re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # 1.200 = 1200 (séparateur de milliers)
        forms.add(s.replace(".", ""))
    if "," in s and re.fullmatch(r"\d{1,3}(,\d{3})+", s):  # 1,200 (anglais)
        forms.add(s.replace(",", ""))
    out = set()
    for f in forms:
        if "." in f:
            f2 = f.rstrip("0").rstrip(".")
            out.add(f2)
        out.add(f)
        out.add(f.lstrip("0") or "0")
    return out


def numbers_in(text: str) -> set[str]:
    out: set[str] = set()
    for m in NUM_RE.finditer(text):
        out |= _norm_forms(m.group(1))
    return out


def unverified_numbers(answer: str, sources_text: str, question: str) -> list[str]:
    """Nombres (2 chiffres ou plus) de la réponse absents des sources et de la question."""
    body = CITE_RE.sub(" ", answer)
    body = re.sub(r"^\s*\d+[.)]\s", " ", body, flags=re.M)  # listes numérotées
    known = numbers_in(sources_text) | numbers_in(question)
    flagged: list[str] = []
    for m in NUM_RE.finditer(body):
        raw = m.group(1)
        if sum(c.isdigit() for c in raw) < 2:
            continue
        if not (_norm_forms(raw) & known) and raw not in flagged:
            flagged.append(raw)
    return flagged[:8]
