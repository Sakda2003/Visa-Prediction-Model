from __future__ import annotations

import os
from typing import Any, Dict, Optional

FORMAT_BY_EXTENSION = {
    ".pdf": "pdf",
    ".jpg": "jpg",
    ".jpeg": "jpeg",
    ".png": "png",
}

MIME_BY_FORMAT = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


def validate_uploaded_document(
    file: Any,
    requirement: Optional[Dict[str, Any]] = None,
    maximum_size: int = 15 * 1024 * 1024,
) -> Dict[str, Any]:
    if not hasattr(file, "buffer") or not file.buffer:
        return {"error": "The uploaded file is empty or invalid"}

    file_size = len(file.buffer)
    if getattr(file, "size", None) is not None and int(file.size) != file_size:
        return {"error": "The uploaded file size could not be verified"}

    requirement = requirement or {}
    limit = min(int(requirement.get("maxFileSize", maximum_size)) or maximum_size, maximum_size)
    if file_size > limit:
        return {"error": f"File exceeds the {max(1, int(limit / 1024 / 1024))} MB limit for this requirement"}

    extension = FORMAT_BY_EXTENSION.get(os.path.splitext(str(getattr(file, "filename", ""))).lower())
    if not extension:
        return {"error": "Only PDF, JPG, JPEG, and PNG file extensions are supported"}

    mime_type = MIME_BY_FORMAT.get(extension)
    if getattr(file, "content_type", None) not in (mime_type, None):
        return {"error": "File extension and MIME type do not match"}

    accepted_formats = [str(item).lower() for item in requirement.get("acceptedFormats", ["pdf", "jpg", "jpeg", "png"]) ]
    if extension not in accepted_formats and not (extension == "jpg" and "jpeg" in accepted_formats) and not (extension == "jpeg" and "jpg" in accepted_formats):
        return {"error": f"This requirement accepts only: {', '.join(sorted(set(accepted_formats))).upper()}"}

    buffer = file.buffer
    signature_matches = (
        mime_type == "application/pdf" and buffer[:5] == b"%PDF-"
    ) or (
        mime_type == "image/png" and buffer[:8] == b"\x89PNG\r\n\x1a\n"
    ) or (
        mime_type == "image/jpeg" and len(buffer) >= 3 and buffer[0] == 0xFF and buffer[1] == 0xD8 and buffer[2] == 0xFF
    )
    if not signature_matches:
        return {"error": "File content does not match its declared format"}

    return {"format": extension, "mimeType": mime_type, "size": file_size}

