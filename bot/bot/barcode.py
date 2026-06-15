"""Barcode decoding from photo bytes."""
import logging
from io import BytesIO

from PIL import Image
from pyzbar.pyzbar import decode as pyzbar_decode

log = logging.getLogger(__name__)


def decode_barcodes(image_bytes: bytes) -> list[str]:
    """Decode all barcodes/QR codes in an image. Returns list of string payloads.

    Empty list if no codes are detected — either because the image doesn't
    contain one, or it's too blurry/angled for pyzbar to read.
    """
    try:
        image = Image.open(BytesIO(image_bytes))
    except Exception as exc:
        log.warning("Couldn't open image: %s", exc)
        return []

    results = pyzbar_decode(image)
    return [r.data.decode("utf-8") for r in results]