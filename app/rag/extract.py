"""Extraction du texte de tous les formats pris en charge.

Chaque extracteur renvoie une liste de `Page(numero, texte)` : le numéro sert aux citations
(page d'un PDF, diapositive, feuille de calcul…). Les originaux ne sont jamais modifiés.
"""
from __future__ import annotations

import csv
import email
import email.policy
import io
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings

log = logging.getLogger("rag.extract")

TEXT_EXT = {".md", ".markdown", ".txt", ".text", ".log", ".json", ".yaml", ".yml", ".xml", ".ini", ".cfg"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif"}
PANDOC_EXT = {".odt", ".rtf", ".epub", ".html", ".htm", ".org", ".rst", ".tex"}
SHEET_EXT = {".xlsx", ".xlsm", ".csv", ".tsv"}
SUPPORTED = (
    {".pdf", ".docx", ".doc", ".pptx", ".eml"} | TEXT_EXT | IMAGE_EXT | PANDOC_EXT | SHEET_EXT
)
MAX_SHEET_ROWS = 5000
OCR_MIN_CHARS = 40  # en dessous, une page PDF est considérée comme scannée


class ExtractError(RuntimeError):
    pass


@dataclass
class Page:
    number: int | None
    text: str
    label: str = ""  # ex. « Feuille Budget », « diapositive 3 »


@dataclass
class Extracted:
    pages: list[Page] = field(default_factory=list)
    method: str = "texte"

    @property
    def text_length(self) -> int:
        return sum(len(p.text.strip()) for p in self.pages)


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED


def extract(path: Path) -> Extracted:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf(path)
    if ext in IMAGE_EXT:
        return Extracted([Page(None, _ocr_image_file(path))], "OCR")
    if ext == ".docx":
        return Extracted([Page(None, _docx(path))], "texte")
    if ext == ".pptx":
        return _pptx(path)
    if ext in {".xlsx", ".xlsm"}:
        return _xlsx(path)
    if ext in {".csv", ".tsv"}:
        return Extracted([Page(None, _csv(path), "tableau")], "tableau")
    if ext in PANDOC_EXT:
        return Extracted([Page(None, _pandoc(path))], "conversion")
    if ext == ".doc":
        return Extracted([Page(None, _run(["catdoc", "-w", "-d", "utf-8", str(path)]))], "conversion")
    if ext == ".eml":
        return Extracted([Page(None, _eml(path))], "e-mail")
    if ext in TEXT_EXT:
        return Extracted([Page(None, read_text(path))], "texte")
    raise ExtractError(f"Format non pris en charge : {ext}")


# ---------------------------------------------------------------- utilitaires
def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run(cmd: list[str], timeout: int = 300, stdin: bytes | None = None) -> str:
    if not shutil.which(cmd[0]):
        raise ExtractError(f"Outil {cmd[0]} absent du conteneur")
    try:
        res = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise ExtractError(f"{cmd[0]} : délai dépassé ({timeout} s)") from e
    if res.returncode != 0:
        raise ExtractError(f"{cmd[0]} : {res.stderr.decode(errors='replace')[:300]}")
    return res.stdout.decode("utf-8", errors="replace")


def clean(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # césures de fin de ligne
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- OCR
def _tesseract_png(png: bytes) -> str:
    return _run(["tesseract", "stdin", "stdout", "-l", settings.ocr_langs, "--psm", "3"], timeout=240, stdin=png)


def _prepare_image(img):  # type: ignore[no-untyped-def]
    from PIL import ImageOps

    img = ImageOps.exif_transpose(img)
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    img = img.convert("L")
    # Petites images (captures, photos réduites) : agrandissement pour l'OCR
    w, h = img.size
    if max(w, h) < 1800:
        factor = min(3.0, 1800 / max(w, h, 1))
        img = img.resize((int(w * factor), int(h * factor)))
    return ImageOps.autocontrast(img)


def _ocr_image_file(path: Path) -> str:
    from PIL import Image

    try:
        import pillow_heif  # noqa: F401

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        with Image.open(path) as img:
            img.seek(0)
            prepared = _prepare_image(img)
    except Exception as e:  # noqa: BLE001 - toute image illisible
        raise ExtractError(f"Image illisible : {e}") from e
    buf = io.BytesIO()
    prepared.save(buf, format="PNG")
    return clean(_tesseract_png(buf.getvalue()))


# ---------------------------------------------------------------- PDF
def _pdf(path: Path) -> Extracted:
    import pymupdf

    try:
        doc = pymupdf.open(path)
    except Exception as e:  # noqa: BLE001
        raise ExtractError(f"PDF illisible : {e}") from e
    if doc.needs_pass:
        raise ExtractError("PDF protégé par mot de passe")
    pages: list[Page] = []
    ocr_pages = 0
    with doc:
        for i, page in enumerate(doc, start=1):
            text = _pdf_page_text(page)
            if len(text) < OCR_MIN_CHARS and (page.get_images() or not text):
                try:
                    pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
                    ocr = clean(_tesseract_png(pix.tobytes("png")))
                    if len(ocr) > len(text):
                        text = ocr
                        ocr_pages += 1
                except ExtractError as e:
                    log.warning("OCR impossible page %d de %s : %s", i, path.name, e)
            pages.append(Page(i, text))
    if ocr_pages == 0:
        method = "texte"
    elif ocr_pages == len(pages):
        method = "OCR"
    else:
        method = f"texte + OCR ({ocr_pages} p.)"
    return Extracted(pages, method)


BULLET_RE = re.compile(r"^\s*([•▪◦●■\-–*]|\d+[.)])\s")


def _pdf_page_text(page) -> str:  # type: ignore[no-untyped-def]
    """Texte d'une page, bloc par bloc dans l'ordre du document (les colonnes restent séparées).

    Dans un bloc (= un paragraphe), les retours à la ligne de mise en page sont recollés,
    sauf devant une puce : sinon le texte justifié sort « un mot par ligne »."""
    paras = []
    for b in page.get_text("blocks"):
        if len(b) > 6 and b[6] != 0:  # bloc image
            continue
        lines = [ln.strip() for ln in b[4].splitlines() if ln.strip()]
        if not lines:
            continue
        out = lines[0]
        for ln in lines[1:]:
            out += ("\n" if BULLET_RE.match(ln) else " ") + ln
        out = re.sub(r"([a-zà-ÿ])- ([a-zà-ÿ])", r"\1\2", out)  # césures
        # Intertitre probable (« Formation », « Expérience professionnelle ») : sert de section aux morceaux
        if len(lines) == 1 and 3 <= len(out) <= 40 and out[0].isupper() and not re.search(r"[.,;:!?@/\d]", out) \
                and len(out.split()) <= 4:
            out = "## " + out
        paras.append(out)
    return clean("\n\n".join(paras))


# ---------------------------------------------------------------- Word
def _docx(path: Path) -> str:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        d = docx.Document(str(path))
    except Exception as e:  # noqa: BLE001
        raise ExtractError(f"Document Word illisible : {e}") from e
    lines: list[str] = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            txt = p.text.strip()
            if not txt:
                continue
            style = (p.style.name if p.style is not None else "").lower()
            m = re.search(r"(heading|titre)\s*(\d)", style)
            if m:
                lines.append("#" * min(int(m.group(2)), 6) + " " + txt)
            elif style in {"title", "titre"}:
                lines.append("# " + txt)
            else:
                lines.append(txt)
        elif tag == "tbl":
            rows = []
            for row in Table(child, d).rows:
                cells = []
                for c in row.cells:
                    t = c.text.strip().replace("\n", " ")
                    if not cells or cells[-1] != t:  # cellules fusionnées répétées
                        cells.append(t)
                rows.append(cells)
            lines.append(_md_table(rows))
    return clean("\n\n".join(lines))


def _md_table(rows: list[list[str]]) -> str:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


# ---------------------------------------------------------------- Présentations
def _pptx(path: Path) -> Extracted:
    from pptx import Presentation

    try:
        prs = Presentation(str(path))
    except Exception as e:  # noqa: BLE001
        raise ExtractError(f"Présentation illisible : {e}") from e
    pages = []
    for i, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text.strip())
            if getattr(shape, "has_table", False):
                rows = [[c.text.strip() for c in r.cells] for r in shape.table.rows]
                parts.append(_md_table(rows))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append("Notes : " + notes)
        pages.append(Page(i, clean("\n".join(parts)), f"diapositive {i}"))
    return Extracted(pages, "présentation")


# ---------------------------------------------------------------- Tableurs
def _rows_to_text(rows: list[list[str]], sheet: str | None) -> str:
    """Chaque ligne devient un enregistrement « Colonne : valeur » (bien plus facile à retrouver)."""
    rows = [[str(c).strip() if c is not None else "" for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    header = rows[0]
    has_header = all(h and not re.fullmatch(r"[\d.,\s€$%-]+", h) for h in header if h) and sum(1 for h in header if h) >= 2
    out = []
    if sheet:
        out.append(f"## Feuille « {sheet} »")
    if has_header:
        out.append("Colonnes : " + " | ".join(h for h in header if h))
        body = rows[1:]
    else:
        header, body = [], rows
    for n, r in enumerate(body[:MAX_SHEET_ROWS], start=2 if has_header else 1):
        if header:
            fields = [f"{header[j] if j < len(header) and header[j] else f'col{j+1}'} : {v}" for j, v in enumerate(r) if v]
        else:
            fields = [v for v in r if v]
        out.append(f"Ligne {n} — " + " ; ".join(fields))
    if len(body) > MAX_SHEET_ROWS:
        out.append(f"(… {len(body) - MAX_SHEET_ROWS} lignes supplémentaires non indexées)")
    return "\n".join(out)


def _xlsx(path: Path) -> Extracted:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        raise ExtractError(f"Classeur illisible : {e}") from e
    pages = []
    try:
        for i, ws in enumerate(wb.worksheets, start=1):
            rows = []
            for r in ws.iter_rows(values_only=True):
                rows.append(["" if v is None else str(v) for v in r])
                if len(rows) > MAX_SHEET_ROWS + 1:
                    break
            text = _rows_to_text(rows, ws.title)
            if text:
                pages.append(Page(i, text, f"feuille « {ws.title} »"))
    finally:
        wb.close()
    return Extracted(pages, "tableau")


def _csv(path: Path) -> str:
    text = read_text(path)
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    return _rows_to_text(rows, None)


# ---------------------------------------------------------------- Divers
def _pandoc(path: Path) -> str:
    fmt = {".htm": "html", ".tex": "latex"}.get(path.suffix.lower(), path.suffix.lower().lstrip("."))
    with tempfile.TemporaryDirectory() as tmp:
        out = _run(
            ["pandoc", "-f", fmt, "-t", "gfm-raw_html", "--wrap=none", f"--extract-media={tmp}", str(path)],
            timeout=180,
        )
    out = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", out)  # images
    return clean(out)


def _eml(path: Path) -> str:
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    head = [f"{k} : {msg[k]}" for k in ("From", "To", "Cc", "Date", "Subject") if msg[k]]
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        body = part.get_content()
        if part.get_content_type() == "text/html":
            body = re.sub(r"<[^>]+>", " ", body)
    attachments = [a.get_filename() for a in msg.iter_attachments() if a.get_filename()]
    if attachments:
        head.append("Pièces jointes : " + ", ".join(attachments))
    return clean("\n".join(head) + "\n\n" + body)
