"""Lokale Auslese: Textschicht aus PDFs (pdftotext), sonst OCR mit Tesseract.

Belege verlassen den Server nicht. Externe Programme laufen ohne Shell, mit Timeout,
auf Temp-Dateien in /tmp (tmpfs im Container).
"""

import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from .parse import parse_fields

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:  # HEIC dann nur ohne Auslese
    pass

OCR_LANG = "deu+eng"
MAX_PDF_PAGES = 3
MIN_TEXT_CHARS = 40
TIMEOUT = 60


def _run(args):
    return subprocess.run(args, capture_output=True, timeout=TIMEOUT, check=True)


def _ocr_image(img):
    img = ImageOps.exif_transpose(img).convert("L")
    # Tesseract mag ~300 dpi; kleine Fotos/Scans hochskalieren.
    if img.width < 1600:
        f = 1600 / img.width
        img = img.resize((1600, int(img.height * f)), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "page.png"
        img.save(p)
        out = _run(["tesseract", str(p), "stdout", "-l", OCR_LANG, "--psm", "4"])
    return out.stdout.decode("utf-8", "replace")


def pdf_text(content):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "in.pdf"
        p.write_bytes(content)
        try:
            out = _run(["pdftotext", "-layout", "-l", str(MAX_PDF_PAGES), str(p), "-"])
            text = out.stdout.decode("utf-8", "replace")
        except (subprocess.SubprocessError, OSError):
            text = ""
        if len(text.strip()) >= MIN_TEXT_CHARS:
            return text, "pdf-text"
        # Gescanntes PDF: Seiten rendern und per OCR lesen.
        _run(["pdftoppm", "-r", "300", "-gray", "-png", "-l", str(MAX_PDF_PAGES), str(p), str(Path(tmp) / "pg")])
        pages = sorted(Path(tmp).glob("pg*.png"))
        return "\n".join(_ocr_image(Image.open(pg)) for pg in pages), "pdf-ocr"


def image_text(content):
    with Image.open(io.BytesIO(content)) as img:
        img.load()
        return _ocr_image(img), "bild-ocr"


class TesseractExtractor:
    name = "tesseract"

    def extract(self, content, kind, vendors, own_names):
        if kind == "pdf":
            text, method = pdf_text(content)
        else:
            text, method = image_text(content)
        result = parse_fields(text, vendors, own_names)
        result["method"] = method
        result["text_chars"] = len(text.strip())
        if result["text_chars"] < MIN_TEXT_CHARS:
            result["warnings"].append("Kaum Text erkannt – Foto unscharf oder zu dunkel?")
        return result
