"""Bilder für den IN-Upload aufbereiten: HEIC -> JPG, sehr große Fotos verkleinern.

Das lokale Archiv behält immer das unveränderte Original (GoBD); umgewandelt wird nur die
Kopie, die an Invoice Ninja geht.
"""

import io

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_OK = True
except ImportError:
    HEIC_OK = False

MAX_SIDE = 3000          # längste Kante nach dem Verkleinern
MAX_BYTES = 4 * 1024 * 1024
JPEG_QUALITY = 85


def prepare_for_upload(content, ext, mime):
    """-> (content, ext, mime, hinweis|None). PDFs und kleine Bilder bleiben unverändert."""
    if ext == "pdf":
        return content, ext, mime, None
    if ext == "heic" and not HEIC_OK:
        return content, ext, mime, None
    with Image.open(io.BytesIO(content)) as img:
        too_big = max(img.size) > MAX_SIDE or len(content) > MAX_BYTES
        if ext != "heic" and not too_big:
            return content, ext, mime, None
        img = ImageOps.exif_transpose(img)
        if max(img.size) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    note = "HEIC als JPG" if ext == "heic" else "verkleinert"
    return out.getvalue(), "jpg", "image/jpeg", note
