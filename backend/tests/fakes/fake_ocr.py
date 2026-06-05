"""Deterministic OCR fake — returns fixed text and records call count."""


class FakeOcrEngine:
    def __init__(self, text: str = "ocr extracted text") -> None:
        self.text = text
        self.calls = 0

    def image_to_text(self, image: bytes) -> str:
        self.calls += 1
        return self.text
