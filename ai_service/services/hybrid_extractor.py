
from __future__ import annotations

import logging
import time
from pathlib import Path

import pymupdf

logger = logging.getLogger("visadoc-ocr.hybrid")


def extract_pdf_hybrid(
    pdf_path: str,
    ocr_engine=None,
    fast_mode: bool = False,
) -> str:
    """
    Fast PDF text extraction.

    Digital PDF:
        Extract embedded text directly using PyMuPDF.

    Scanned PDF:
        Use the existing OCRService only when fast_mode=False.

    fast_mode=True:
        Skip scanned pages to prioritize speed.
    """
    started = time.perf_counter()
    pdf_file = Path(pdf_path)

    if not pdf_file.is_file():
        logger.error("PDF file not found: %s", pdf_path)
        return ""

    try:
        with pymupdf.open(str(pdf_file)) as doc:
            if doc.needs_pass:
                logger.warning("Password-protected PDF: %s", pdf_path)
                return ""

            pages = []
            scanned_pages = []

            # Stage 1: Extract digital text as quickly as possible.
            for index, page in enumerate(doc):
                text = page.get_text("text", sort=True).strip()

                if len(text) >= 20:
                    pages.append(
                        f"--- Page {index + 1} (Digital) ---\n{text}"
                    )
                else:
                    pages.append(None)
                    scanned_pages.append(index)

            # All pages have embedded text: no OCR required.
            if not scanned_pages:
                result = "\n\n".join(pages)
                logger.info(
                    "Digital PDF extracted in %.4fs",
                    time.perf_counter() - started,
                )
                return result

            # Fast mode skips OCR for scanned pages.
            if fast_mode:
                for index in scanned_pages:
                    pages[index] = (
                        f"--- Page {index + 1} (Scanned - OCR skipped) ---"
                    )

                logger.info(
                    "Fast PDF extraction completed in %.4fs; "
                    "skipped %s scanned pages",
                    time.perf_counter() - started,
                    len(scanned_pages),
                )
                return "\n\n".join(pages)

    except Exception:
        logger.exception("PDF extraction failed: %s", pdf_path)
        return ""

    # Stage 2: Use existing OCRService for scanned PDF pages.
    if ocr_engine is None:
        logger.warning("Scanned pages found, but OCR engine is unavailable.")
        return "\n\n".join(
            page if page is not None else "--- Scanned page: OCR unavailable ---"
            for page in pages
        )

    try:
        pdf_bytes = pdf_file.read_bytes()

        # OCRService.process_document already supports:
        # embedded PDF text + scanned-page OCR + batch processing.
        document = ocr_engine.process_document(
            data=pdf_bytes,
            file_type="pdf",
        )

        logger.info(
            "Hybrid PDF extraction completed in %.3fs",
            time.perf_counter() - started,
        )

        return document.text

    except Exception:
        logger.exception("Scanned PDF OCR failed: %s", pdf_path)
        return "\n\n".join(
            page if page is not None else "--- Scanned page: OCR failed ---"
            for page in pages
        )
