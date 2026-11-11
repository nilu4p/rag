"""Vecteurs creux « mots-clés » pour la recherche hybride (l'IDF est calculé par Qdrant)."""
from __future__ import annotations

import math
import re
import unicodedata
import zlib
from collections import Counter

STOPWORDS = set(
    """
    a au aux avec ce ces cet cette dans de des du elle elles en et eux il ils je la le les leur leurs lui ma mais me
    meme mes moi mon ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un une
    vos votre vous y est sont ai as avons avez ont etait etre fait faire plus tres tout tous toute toutes comme si
    quel quelle quels quelles quoi dont ici la ca cela ceci sans sous entre vers chez donc car ni or deja aussi
    the of and to in is are was were be been for on at by with from as it its this that these those an or not
    what which who whom how when where why do does did my your our their his her has have had will would can could
    """.split()
)

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def tokenize(text: str) -> list[str]:
    toks = []
    for t in TOKEN_RE.findall(normalize(text)):
        if t in STOPWORDS:
            continue
        if len(t) < 2 and not t.isdigit():
            continue
        # Pluriels simples : « factures » = « facture »
        if len(t) > 4 and t.endswith(("s", "x")) and not t[-2].isdigit():
            t = t[:-1]
        toks.append(t)
    return toks


def _index(token: str) -> int:
    return zlib.crc32(token.encode()) & 0x7FFFFFFF


def _merge(pairs: dict[int, float]) -> dict:
    items = sorted(pairs.items())
    return {"indices": [i for i, _ in items], "values": [v for _, v in items]}


def doc_vector(text: str) -> dict:
    counts = Counter(tokenize(text))
    pairs: dict[int, float] = {}
    for tok, n in counts.items():
        idx = _index(tok)
        pairs[idx] = pairs.get(idx, 0.0) + 1.0 + math.log(n)
    return _merge(pairs)


def query_vector(text: str) -> dict:
    pairs = {_index(t): 1.0 for t in set(tokenize(text))}
    return _merge(pairs)
