"""Tesseract OCR adapter — the only file importing the OCR SDK.

Writes the rendered page image to a temp file and runs tesseract on it (no Pillow
dependency). The tesseract binary is installed in the worker image.
"""

import tempfile

import pytesseract


class TesseractOcrEngine:
    def image_to_text(self, image: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".png") as handle:
            handle.write(image)
            handle.flush()
            return str(pytesseract.image_to_string(handle.name))
