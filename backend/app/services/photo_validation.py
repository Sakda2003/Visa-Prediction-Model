from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


_SUPPORTED_FORMATS = {"JPEG": {".jpg", ".jpeg"}, "PNG": {".png"}}
_DEFAULT_RULES = {
    "minWidth": 600,
    "minHeight": 600,
    "minSharpness": 50.0,
    "minBrightness": 40.0,
    "maxBrightness": 220.0,
    "faceCenterTolerance": 0.25,
    "minFaceHeightRatio": 0.2,
    "manualReviewRequired": False,
}
_CONFIGURABLE_RULES = set(_DEFAULT_RULES)


def is_photo_document(
    document: dict[str, Any],
    requirement: dict[str, Any] | None = None,
) -> bool:
    requirement = requirement or {}
    values = (
        document.get("requirementCode"),
        requirement.get("code"),
        requirement.get("name"),
        document.get("requirementName"),
    )
    return any("photo" in str(value or "").casefold() for value in values)


def _check(code: str, label: str, status: str, reason: str) -> dict[str, str]:
    return {"code": code, "label": label, "status": status, "reason": reason, "fields": []}


def _rules(value: Any) -> dict[str, Any]:
    configured = (
        {key: item for key, item in value.items() if key in _CONFIGURABLE_RULES}
        if isinstance(value, dict)
        else {}
    )
    rules = {**_DEFAULT_RULES, **configured}
    for field in ("minWidth", "minHeight"):
        try:
            rules[field] = max(1, int(rules[field]))
        except (TypeError, ValueError):
            rules[field] = _DEFAULT_RULES[field]
    for field in (
        "minSharpness",
        "minBrightness",
        "maxBrightness",
        "faceCenterTolerance",
        "minFaceHeightRatio",
    ):
        try:
            rules[field] = max(0.0, float(rules[field]))
        except (TypeError, ValueError):
            rules[field] = _DEFAULT_RULES[field]
    rules["manualReviewRequired"] = rules["manualReviewRequired"] is True
    return rules


def validate_applicant_photo(
    content: bytes,
    file_name: str,
    mime_type: str,
    photo_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess photo file quality and face placement, not identity or authenticity."""
    rules = _rules(photo_rules)
    extension = Path(file_name).suffix.lower()
    mime_format = {
        "image/jpeg": "JPEG",
        "image/jpg": "JPEG",
        "image/png": "PNG",
    }.get(str(mime_type or "").casefold())
    extension_format = next(
        (image_format for image_format, extensions in _SUPPORTED_FORMATS.items() if extension in extensions),
        None,
    )
    supported = bool(content) and extension_format is not None and mime_format == extension_format
    checks = [
        _check(
            "PHOTO_FORMAT",
            "Supported image format",
            "PASSED" if supported else "FAILED",
            "The file extension and image type are JPG/JPEG or PNG."
            if supported
            else "Applicant photos must use matching JPG/JPEG or PNG file and image types.",
        )
    ]
    if not supported:
        checks.extend(
            [
                _check("PHOTO_DECODE", "Image can be opened", "INSUFFICIENT_DATA", "Image decoding was skipped because the file type is unsupported."),
                _check("PHOTO_RESOLUTION", "Minimum image resolution", "INSUFFICIENT_DATA", "Image dimensions are unavailable."),
                _check("PHOTO_CLARITY", "Image clarity", "INSUFFICIENT_DATA", "Image clarity could not be assessed."),
                _check("PHOTO_EXPOSURE", "Image exposure", "INSUFFICIENT_DATA", "Image exposure could not be assessed."),
                _check("PHOTO_FACE_VISIBILITY", "Face visibility", "INSUFFICIENT_DATA", "Face visibility could not be assessed."),
                _check("PHOTO_FACE_POSITION", "Face position", "INSUFFICIENT_DATA", "Face position could not be assessed."),
            ]
        )
        return {"checks": checks, "width": None, "height": None}

    try:
        with Image.open(BytesIO(content)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
        if image_format != extension_format or width * height > 40_000_000:
            raise ValueError("The image type or dimensions are not supported.")
        with Image.open(BytesIO(content)) as image:
            image.load()
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            width, height = normalized.size
            rgb = np.asarray(normalized)
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        checks.extend(
            [
                _check("PHOTO_DECODE", "Image can be opened", "FAILED", "The uploaded file is not a decodable image."),
                _check("PHOTO_RESOLUTION", "Minimum image resolution", "INSUFFICIENT_DATA", "Image dimensions are unavailable."),
                _check("PHOTO_CLARITY", "Image clarity", "INSUFFICIENT_DATA", "Image clarity could not be assessed."),
                _check("PHOTO_EXPOSURE", "Image exposure", "INSUFFICIENT_DATA", "Image exposure could not be assessed."),
                _check("PHOTO_FACE_VISIBILITY", "Face visibility", "INSUFFICIENT_DATA", "Face visibility could not be assessed."),
                _check("PHOTO_FACE_POSITION", "Face position", "INSUFFICIENT_DATA", "Face position could not be assessed."),
            ]
        )
        return {"checks": checks, "width": None, "height": None}

    checks.append(_check("PHOTO_DECODE", "Image can be opened", "PASSED", "The image was opened and decoded successfully."))
    resolution_ok = width >= rules["minWidth"] and height >= rules["minHeight"]
    checks.append(
        _check(
            "PHOTO_RESOLUTION",
            "Minimum image resolution",
            "PASSED" if resolution_ok else "FAILED",
            f"Image resolution is {width} x {height}; minimum is {rules['minWidth']} x {rules['minHeight']}."
            if resolution_ok
            else f"Image resolution is {width} x {height}; minimum required is {rules['minWidth']} x {rules['minHeight']}.",
        )
    )
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    clarity_status = "PASSED" if sharpness >= rules["minSharpness"] else "NEEDS_REVIEW"
    checks.append(
        _check(
            "PHOTO_CLARITY",
            "Image clarity",
            clarity_status,
            f"Sharpness score {sharpness:.1f} meets the configured minimum of {rules['minSharpness']:.1f}."
            if clarity_status == "PASSED"
            else f"Sharpness score {sharpness:.1f} is below the configured minimum of {rules['minSharpness']:.1f}; inspect image clarity manually.",
        )
    )
    exposure_ok = rules["minBrightness"] <= brightness <= rules["maxBrightness"]
    checks.append(
        _check(
            "PHOTO_EXPOSURE",
            "Image exposure",
            "PASSED" if exposure_ok else "NEEDS_REVIEW",
            f"Average brightness {brightness:.1f} is within the configured range."
            if exposure_ok
            else f"Average brightness {brightness:.1f} is outside the configured range ({rules['minBrightness']:.1f} to {rules['maxBrightness']:.1f}); inspect the photo manually.",
        )
    )

    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    try:
        detector = cv2.CascadeClassifier(cascade_path)
        faces = (
            None
            if detector.empty()
            else detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(30, 30),
            )
        )
    except cv2.error:
        faces = None
    if faces is None:
        checks.extend(
            [
                _check("PHOTO_FACE_VISIBILITY", "Face visibility", "NEEDS_REVIEW", "Automatic face detection could not assess visibility; staff must inspect the photo."),
                _check("PHOTO_FACE_POSITION", "Face position", "NEEDS_REVIEW", "Automatic face detection could not assess position; staff must inspect the photo."),
            ]
        )
    else:
        if len(faces) == 0:
            checks.extend(
                [
                    _check("PHOTO_FACE_VISIBILITY", "Face visibility", "NEEDS_REVIEW", "No face was detected; visibility is uncertain and requires staff inspection."),
                    _check("PHOTO_FACE_POSITION", "Face position", "INSUFFICIENT_DATA", "Face position cannot be assessed without a detected face."),
                ]
            )
        else:
            primary_face = max(faces, key=lambda face: int(face[2]) * int(face[3]))
            face_x, face_y, face_width, face_height = (int(value) for value in primary_face)
            face_visible = face_height / height >= rules["minFaceHeightRatio"]
            checks.append(
                _check(
                    "PHOTO_FACE_VISIBILITY",
                    "Face visibility",
                    "PASSED" if face_visible else "NEEDS_REVIEW",
                    "A frontal face is detected at a usable size; automated detection does not identify the person."
                    if face_visible
                    else "The detected face is small or visibility is uncertain; staff must inspect the photo.",
                )
            )
            center_x = (face_x + face_width / 2) / width
            center_y = (face_y + face_height / 2) / height
            centered = (
                abs(center_x - 0.5) <= rules["faceCenterTolerance"]
                and abs(center_y - 0.5) <= rules["faceCenterTolerance"]
            )
            checks.append(
                _check(
                    "PHOTO_FACE_POSITION",
                    "Face position",
                    "PASSED" if centered else "NEEDS_REVIEW",
                    "The detected face is positioned within the configured center tolerance."
                    if centered
                    else "The detected face is off-center; staff must inspect its position.",
                )
            )

    return {
        "checks": checks,
        "width": width,
        "height": height,
        "sharpness": round(sharpness, 2),
        "brightness": round(brightness, 2),
        "manualReviewRequired": rules["manualReviewRequired"],
    }
