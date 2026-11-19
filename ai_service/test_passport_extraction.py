from mrz_parser import calculate_check_digit, parse_mrz


def _td3_sample():
    line1 = "PNKHMKONG<<RATHA".ljust(44, "<")
    number = "N02619339"
    birth = "860513"
    expiry = "340429"
    optional = "N0002996823<<<"
    line2 = (
        number
        + calculate_check_digit(number)
        + "KHM"
        + birth
        + calculate_check_digit(birth)
        + "F"
        + expiry
        + calculate_check_digit(expiry)
        + optional
        + calculate_check_digit(optional)
    )
    composite = (
        number
        + calculate_check_digit(number)
        + birth
        + calculate_check_digit(birth)
        + expiry
        + calculate_check_digit(expiry)
        + optional
        + calculate_check_digit(optional)
    )
    # The TD3 composite check digit is the final character.
    line2 = line2[:43] + calculate_check_digit(composite)
    return line1, line2


def test_td3_parser_extracts_passport_fields_and_validates_check_digits():
    line1, line2 = _td3_sample()
    result = parse_mrz(f"{line1}\n{line2}")

    assert result["format"] == "TD3"
    assert result["status"] == "VALID"
    assert result["data"]["passport_number"] == "N02619339"
    assert result["data"]["nationality"] == "KHM"
    assert result["data"]["surname"] == "KONG"
    assert result["data"]["given_names"] == "RATHA"
    assert result["data"]["sex"] == "F"
    assert result["data"]["date_of_birth"] == "1986-05-13"
    assert result["data"]["expiry_date"] == "2034-04-29"
    assert all(result["data"]["check_digits"].values())


def test_td3_parser_keeps_data_but_requests_review_when_a_check_digit_fails():
    line1, line2 = _td3_sample()
    invalid = line2[:9] + ("0" if line2[9] != "0" else "1") + line2[10:]
    result = parse_mrz(f"{line1}\n{invalid}")

    assert result["status"] == "NEEDS_MANUAL_REVIEW"
    assert result["data"] is not None
    assert result["data"]["passport_number"] == "N02619339"
    assert result["data"]["check_digits"]["passport_number"] is False


def test_unreadable_or_incomplete_mrz_does_not_invent_fields():
    result = parse_mrz("P<KHMKONG<<RATHA\nNO26193396KHM")

    assert result["status"] == "NEEDS_MANUAL_REVIEW"
    assert result["data"] is None
    assert result["mrz_lines"] == []


def test_td3_data_line_fallback_extracts_checkable_fields_without_inventing_name():
    _, line2 = _td3_sample()
    result = parse_mrz(line2)

    assert result["status"] == "NEEDS_MANUAL_REVIEW"
    assert result["data"]["passport_number"] == "N02619339"
    assert result["data"]["date_of_birth"] == "1986-05-13"
    assert result["data"]["expiry_date"] == "2034-04-29"
    assert result["data"]["surname"] == ""


def test_jpeg_preprocessing_upscales_without_changing_aspect_ratio():
    from io import BytesIO

    from PIL import Image

    from app.ocr_engine import OCRService

    source = Image.new("RGB", (320, 200), "white")
    buffer = BytesIO()
    source.save(buffer, format="JPEG")
    image, _ = OCRService()._decode_image(buffer.getvalue())

    height, width = image.shape[:2]
    assert max(width, height) >= OCRService().min_image_side
    assert abs((width / height) - (320 / 200)) < 0.02


def test_passport_layout_with_ocr_filler_noise_still_extracts_fields():
    from app.main import _build_ocr_response
    from app.ocr_engine import OCRDocument, OCRPage

    text = "\n".join(
        [
            "RATHA",
            "KONG",
            "CAMBODIAN",
            "13 May 1986",
            "29 Apr 2024",
            "PNKHMKONG<<RATHAK<<<<<<< <<< KK 6K KK KKK KKK KKK KKK KKK",
            "N026193396KHM8605135F3404296N0002996823<<<88",
        ]
    )
    response = _build_ocr_response(
        "IMG_0064.jpeg",
        "passport",
        OCRDocument(
            pages=[OCRPage(page=1, text=text, confidence=0.91)],
            metrics={"page_count": 1},
        ),
    )

    assert response.fields["passportNumber"] == "N02619339"
    assert response.fields["fullName"] == "KONG RATHAK"
    assert response.fields["dateOfBirth"] == "1986-05-13"
    assert response.fields["expiryDate"] == "2034-04-29"
    assert response.fields["nationality"] == "KHM"
    assert response.fields["gender"] == "F"
    assert response.missing_fields == []


def test_jpeg_passport_upload_runs_ocr_and_returns_structured_fields(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app, ocr_service
    from app.ocr_engine import OCRDocument, OCRPage

    line1, line2 = _td3_sample()

    def fake_initialize():
        ocr_service._initialized = True
        ocr_service._runtime.update(
            engine="paddleocr",
            model="PP-OCRv6_tiny",
            model_version="test",
            device="cpu",
            initialized=True,
        )

    def fake_process(_data, _file_type, _cancellation_event):
        return OCRDocument(
            pages=[OCRPage(page=1, text=f"{line1}\n{line2}", confidence=0.91)],
            metrics={"page_count": 1},
        )

    monkeypatch.setattr(ocr_service, "initialize", fake_initialize)
    monkeypatch.setattr(ocr_service, "process_document", fake_process)
    with TestClient(app) as client:
        response = client.post(
            "/ocr/extract",
            files={"file": ("IMG_0064.jpeg", b"\xff\xd8\xffjpeg", "image/jpeg")},
            data={"requirement_code": "passport"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "passport"
    assert payload["fields"]["passportNumber"] == "N02619339"
    assert payload["fields"]["dateOfBirth"] == "1986-05-13"
    assert payload["fields"]["expiryDate"] == "2034-04-29"
    assert payload["fields"]["nationality"] == "KHM"
    assert payload["fields"]["gender"] == "F"
    assert payload["field_evidence"]["passportNumber"]["source"] == "mrz"


def test_unreadable_jpeg_upload_returns_ocr_failure_without_fields(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app, ocr_service
    from app.ocr_engine import OCRDocument, OCRPage

    def fake_initialize():
        ocr_service._initialized = True

    def fake_process(_data, _file_type, _cancellation_event):
        return OCRDocument(
            pages=[OCRPage(page=1, text="", confidence=0.0)],
            metrics={"page_count": 1},
        )

    monkeypatch.setattr(ocr_service, "initialize", fake_initialize)
    monkeypatch.setattr(ocr_service, "process_document", fake_process)
    with TestClient(app) as client:
        response = client.post(
            "/ocr/extract",
            files={"file": ("unreadable.jpeg", b"\xff\xd8\xffjpeg", "image/jpeg")},
            data={"requirement_code": "passport"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fields"] == {}
    assert payload["confidence"] == 0.0
    assert payload["status"] == "manual_review"
