from io import BytesIO

import numpy as np
from PIL import Image
import pytest

from backend.app.services import photo_validation
from backend.app.services.automated_document_verification import (
    evaluate_automated_document_verification,
)


class _FaceDetector:
    def __init__(self, faces):
        self.faces = faces

    def empty(self):
        return False

    def detectMultiScale(self, *_args, **_kwargs):
        return self.faces


def _image_bytes(*, blurry=False, image_format="PNG"):
    if blurry:
        image = Image.new("RGB", (800, 800), "gray")
    else:
        pixels = np.indices((800, 800)).sum(axis=0) % 2 * 255
        image = Image.fromarray(np.stack([pixels] * 3, axis=-1).astype("uint8"))
    output = BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


def _detect(monkeypatch, faces):
    monkeypatch.setattr(
        photo_validation.cv2,
        "CascadeClassifier",
        lambda _path: _FaceDetector(faces),
    )


def _check_result(photo, code):
    return next(check for check in photo["checks"] if check["code"] == code)


def test_valid_photo_with_centered_face_passes(monkeypatch):
    _detect(monkeypatch, np.array([[300, 250, 200, 250]]))
    result = photo_validation.validate_applicant_photo(
        _image_bytes(), "applicant.png", "image/png"
    )
    assert all(check["status"] == "PASSED" for check in result["checks"])
    assert not any(check["code"] == "PHOTO_FACE_COUNT" for check in result["checks"])


def test_blurry_photo_requires_review(monkeypatch):
    _detect(monkeypatch, np.array([[300, 250, 200, 250]]))
    result = photo_validation.validate_applicant_photo(
        _image_bytes(blurry=True), "applicant.png", "image/png"
    )
    assert _check_result(result, "PHOTO_CLARITY")["status"] == "NEEDS_REVIEW"


def test_low_resolution_photo_fails_and_missing_detector_requires_review(monkeypatch):
    _detect(monkeypatch, np.array([[300, 250, 200, 250]]))
    small = Image.new("RGB", (200, 200), "white")
    output = BytesIO()
    small.save(output, format="PNG")
    low_resolution = photo_validation.validate_applicant_photo(
        output.getvalue(), "applicant.png", "image/png"
    )
    _detect(monkeypatch, None)
    uncertain = photo_validation.validate_applicant_photo(
        _image_bytes(), "applicant.png", "image/png"
    )
    assert _check_result(low_resolution, "PHOTO_RESOLUTION")["status"] == "FAILED"
    assert not any(check["code"] == "PHOTO_FACE_COUNT" for check in uncertain["checks"])


@pytest.mark.parametrize(
    "faces, expected_visibility",
    [
        (np.empty((0, 4), dtype=int), "NEEDS_REVIEW"),
        (np.array([[300, 250, 200, 250]]), "PASSED"),
        (np.array([[300, 250, 200, 250], [300, 250, 200, 250]]), "PASSED"),
        (
            np.array(
                [
                    [300, 250, 200, 250],
                    [300, 250, 200, 250],
                    [300, 250, 200, 250],
                ]
            ),
            "PASSED",
        ),
    ],
)
def test_face_detection_count_does_not_fail_photo_or_reduce_score(
    monkeypatch, faces, expected_visibility
):
    _detect(monkeypatch, faces)
    validation = photo_validation.validate_applicant_photo(
        _image_bytes(), "applicant.png", "image/png"
    )
    result = evaluate_automated_document_verification(
        {
            "requirementCode": "photo",
            "ocrStatus": "OCR_COMPLETED",
            "ocrConfidence": 0.1,
            "photoValidation": validation,
        },
        {},
        {"code": "photo", "weight": 10},
    )
    assert not any(check["code"] == "PHOTO_FACE_COUNT" for check in validation["checks"])
    assert _check_result(validation, "PHOTO_FACE_VISIBILITY")["status"] == expected_visibility
    assert result["status"] != "REJECTED"
    if len(faces) > 0:
        assert result["status"] == "VERIFIED"
        assert result["photoPointsRatio"] == 1


def test_unsupported_format_and_corrupt_image_are_rejected():
    unsupported = photo_validation.validate_applicant_photo(
        _image_bytes(image_format="WEBP"), "applicant.webp", "image/webp"
    )
    corrupt = photo_validation.validate_applicant_photo(
        b"not an image", "applicant.jpg", "image/jpeg"
    )
    assert _check_result(unsupported, "PHOTO_FORMAT")["status"] == "FAILED"
    assert _check_result(corrupt, "PHOTO_DECODE")["status"] == "FAILED"


def test_low_ocr_confidence_does_not_reduce_valid_photo_result(monkeypatch):
    _detect(monkeypatch, np.array([[300, 250, 200, 250]]))
    validation = photo_validation.validate_applicant_photo(
        _image_bytes(), "applicant.png", "image/png"
    )
    result = evaluate_automated_document_verification(
        {
            "requirementCode": "photo",
            "ocrStatus": "OCR_COMPLETED",
            "ocrConfidence": 0.213,
            "photoValidation": validation,
        },
        {},
        {"code": "photo", "weight": 10},
    )
    assert result["status"] == "VERIFIED"
    assert result["photoPointsRatio"] == 1
    assert result["ocrConfidencePercent"] == 21.3


def test_configured_manual_review_keeps_valid_photo_pending(monkeypatch):
    _detect(monkeypatch, np.array([[300, 250, 200, 250]]))
    validation = photo_validation.validate_applicant_photo(
        _image_bytes(), "applicant.png", "image/png", {"manualReviewRequired": True}
    )
    result = evaluate_automated_document_verification(
        {"requirementCode": "photo", "photoValidation": validation},
        {},
        {"code": "photo"},
    )
    assert result["status"] == "NEEDS_REVIEW"
    assert result["photoPointsRatio"] == 1
    assert any(check["code"] == "PHOTO_MANUAL_REVIEW" for check in result["checks"])
