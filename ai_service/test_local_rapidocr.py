"""Synthetic-only tests for the optional offline CPU OCR mode."""
from io import BytesIO
import importlib.util
import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont
from app.ocr_engine import OCRService


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("rapidocr_onnxruntime") is None,
    reason="Install ai_service/requirements-local.txt to exercise optional local OCR",
)


def local_service(monkeypatch):
    monkeypatch.setenv("OCR_PRIMARY_ENGINE", "rapidocr")
    service = OCRService()
    service.initialize()
    return service


def test_explicit_local_engine_uses_bundled_models_and_reports_limit(monkeypatch):
    service = local_service(monkeypatch)
    runtime = service.runtime_info
    assert runtime["engine"] == "rapidocr"
    assert runtime["initialized"] is True
    assert runtime["device"] == "cpu"
    assert runtime["model_source"] == "installed-package-local-files"
    assert "Khmer" in runtime["language_limitations"]


def test_generated_employment_image_is_read_with_actual_boxes(monkeypatch):
    service = local_service(monkeypatch)
    image = Image.new("RGB", (1200, 500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=42)
    draw.text((60, 60), "EMPLOYMENT LETTER", font=font, fill="black")
    draw.text((60, 170), "Employee: Example Person", font=font, fill="black")
    draw.text((60, 280), "Monthly Salary USD 1500", font=font, fill="black")
    stream = BytesIO()
    image.save(stream, format="PNG")
    result = service.process_document(stream.getvalue(), "png")
    assert "EMPLOYMENT" in result.text.upper()
    assert "1500" in result.text
    assert result.metrics["ocr_engine"] == "rapidocr"
    assert result.pages[0].boxes
    assert all(0 <= box["confidence"] <= 1 for box in result.pages[0].boxes)
    assert all(len(box["points"]) == 4 for box in result.pages[0].boxes)
    assert result.pages[0].status == "OCR_COMPLETED"


def test_native_pdf_stays_native_in_local_mode(monkeypatch):
    service = local_service(monkeypatch)
    monkeypatch.setattr(service, "_recognize_batch", lambda images: pytest.fail("Native PDF must not run OCR"))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Synthetic employment evidence with a monthly salary of USD 1500.")
    result = service.process_document(doc.tobytes(), "pdf")
    assert result.pages[0].method == "embedded_text"
    assert "1500" in result.text
    assert result.pages[0].confidence is None


def test_measured_detection_values_are_retained_without_paddle(monkeypatch):
    service = local_service(monkeypatch)
    polygon = [[10, 20], [100, 20], [100, 45], [10, 45]]
    service._model = lambda image: ([[polygon, "SYNTHETIC", .875]], [0, 0, 0])
    text, confidence, boxes = service._recognize(np.zeros((60, 120, 3), dtype=np.uint8))
    assert text == "SYNTHETIC"
    assert confidence == .875
    assert boxes[0]["confidence"] == .875
    assert boxes[0]["points"] == polygon
