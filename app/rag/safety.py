"""Protection contre l'injection de prompt dans les documents et les pages web."""
from __future__ import annotations

import re

PATTERNS = [
    r"ignore[rz]?\s+(?:toutes?\s+)?(?:les|tes|vos|mes|ces|all|any|the|previous|prior|above)?\s*(?:\w+\s+)?(?:instructions?|consignes?|r[èe]gles?|rules|prompts?)",
    r"oublie[rz]?\s+(?:toutes?\s+)?(?:les|tes|vos)\s+(?:instructions?|consignes?|r[èe]gles?)",
    r"disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous\s+|prior\s+|above\s+)?(?:instructions?|rules|prompts?)",
    r"forget\s+(?:all\s+|your\s+|the\s+)?(?:previous\s+)?(?:instructions?|rules)",
    r"(?:nouvelles?|new)\s+(?:instructions?|consignes?)\s*:",
    r"(?:tu es|vous [êe]tes) (?:d[ée]sormais|maintenant)\b",
    r"\byou are now\b",
    r"\b(?:act|behave) as (?:an?|the)\b",
    r"(?:system|syst[èe]me)\s*prompt",
    r"<\|?(?:im_start|im_end|system|assistant|endoftext)\|?>",
    r"\[/?(?:INST|SYS)\]",
    r"^\s*(?:system|assistant)\s*:",
    r"(?:r[ée]v[èe]le|reveal|print|affiche)\s+(?:ton|tes|your|the)\s+(?:prompt|instructions?)",
    r"(?:r[ée]ponds?|answer|say)\s+(?:uniquement|seulement|only)\s+(?:que|par|with|that)\b",
]
INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in PATTERNS), re.I | re.M)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?\n])\s+")


def sanitize(text: str) -> tuple[str, bool]:
    """Retire les phrases qui ressemblent à des instructions adressées à l'IA. Renvoie (texte, suspect)."""
    if not INJECTION_RE.search(text):
        return text, False
    kept = []
    for sentence in SENTENCE_SPLIT.split(text):
        if INJECTION_RE.search(sentence):
            kept.append("[passage retiré : instruction suspecte]")
        else:
            kept.append(sentence)
    return " ".join(kept), True


def neutralize_tags(text: str) -> str:
    """Empêche un extrait de fermer artificiellement la balise qui l'encadre."""
    return re.sub(r"</?\s*(?:extrait|source|document|page)\b[^>]*>", "", text, flags=re.I)
