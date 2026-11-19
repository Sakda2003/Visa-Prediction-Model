import os
import uuid
import logging
import tempfile
import threading
import gc
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Callable

# =========================================================
# LOAD PROJECT .ENV BEFORE READING CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def _load_env_file() -> None:
    """
    Load the .env file located beside this ocr_api.py.

    By default, values in the project .env override inherited shell values.
    Set OCR_DOTENV_OVERRIDE=false in the process environment if deployment
    environment variables should take precedence.
    """
    override = os.getenv("OCR_DOTENV_OVERRIDE", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }

    if not ENV_FILE.is_file():
        print(f"[VisaDoc OCR] .env file not found at: {ENV_FILE}")
        print("[VisaDoc OCR] Using process environment/default settings.")
        return

    try:
        # Prefer python-dotenv when it is installed.
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=ENV_FILE, override=override)
        print(f"[VisaDoc OCR] Loaded environment file: {ENV_FILE}")
        return
    except ImportError:
        # Small fallback parser so python-dotenv is not a hard dependency.
        pass
    except Exception as exc:
        print(f"[VisaDoc OCR] python-dotenv failed: {exc}")

    try:
        for raw_line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            if line.startswith("export "):
                line = line[7:].strip()

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if not key:
                continue

            # Remove matching surrounding quotes.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            else:
                # Remove unquoted inline comments.
                marker = value.find(" #")
                if marker >= 0:
                    value = value[:marker].rstrip()

            if override or key not in os.environ:
                os.environ[key] = value

        print(f"[VisaDoc OCR] Loaded environment file: {ENV_FILE}")
    except Exception as exc:
        raise RuntimeError(f"Unable to load environment file {ENV_FILE}: {exc}") from exc


_load_env_file()

# Import heavy dependencies after loading environment settings.
import fitz
import paddle

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
)

from paddleocr import PaddleOCR


# =========================================================
# CONFIGURATION
# =========================================================

SERVICE_NAME = "VisaDoc AI GPU OCR Service"


def _positive_integer_setting(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()

    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be a positive integer; received {raw_value!r}."
        ) from exc

    if parsed < 1:
        raise ValueError(
            f"{name} must be a positive integer; received {parsed}."
        )

    return parsed


def _boolean_setting(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)

    if raw_value is None:
        return default

    value = raw_value.strip().lower()

    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"{name} must be true/false, yes/no, or 1/0; received {raw_value!r}."
    )


MAX_FILE_SIZE = _positive_integer_setting(
    "MAX_FILE_SIZE",
    10 * 1024 * 1024,
)
OCR_MAX_PAGES = _positive_integer_setting("OCR_MAX_PAGES", 100)
OCR_BATCH_SIZE = _positive_integer_setting("OCR_BATCH_SIZE", 10)
OCR_DPI = _positive_integer_setting("OCR_DPI", 180)
OCR_PROCESS_TIMEOUT_SECONDS = _positive_integer_setting(
    "OCR_PROCESS_TIMEOUT_SECONDS",
    900,
)

OCR_PRIMARY_ENGINE = os.getenv("OCR_PRIMARY_ENGINE", "paddleocr").strip().lower()
OCR_MODEL_SIZE = os.getenv("OCR_MODEL_SIZE", "auto").strip().lower()
OCR_TESSERACT_FALLBACK = _boolean_setting("OCR_TESSERACT_FALLBACK", True)

SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".pdf",
}

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

logger = logging.getLogger("visadoc-ocr")

logger.info("OCR environment file: %s", ENV_FILE)
logger.info("OCR_MAX_PAGES=%s", OCR_MAX_PAGES)
logger.info("OCR_BATCH_SIZE=%s", OCR_BATCH_SIZE)
logger.info("MAX_FILE_SIZE=%s bytes", MAX_FILE_SIZE)
logger.info("OCR_DPI=%s", OCR_DPI)
logger.info("OCR_PRIMARY_ENGINE=%s", OCR_PRIMARY_ENGINE)
logger.info("OCR_TESSERACT_FALLBACK=%s", OCR_TESSERACT_FALLBACK)


# =========================================================
# GLOBAL STATE
# =========================================================

ocr = None

# In-memory job storage (kept compatible with the existing API).
jobs: dict[str, dict[str, Any]] = {}

# Prevent simultaneous access to PaddleOCR.
ocr_lock = threading.Lock()


class PDFPageLimitError(ValueError):
    def __init__(self, total_pages: int) -> None:
        self.total_pages = total_pages
        self.maximum_pages = OCR_MAX_PAGES
        super().__init__(
            f"PDF contains {total_pages} pages; the configured maximum is "
            f"{OCR_MAX_PAGES}. Please upload a PDF with {OCR_MAX_PAGES} pages "
            "or fewer, or split it into smaller files."
        )


# =========================================================
# OCR INITIALIZATION
# =========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ocr

    logger.info("Initializing PaddleOCR...")
    logger.info(
        "Effective OCR configuration: max_pages=%d, batch_size=%d, dpi=%d",
        OCR_MAX_PAGES,
        OCR_BATCH_SIZE,
        OCR_DPI,
    )

    try:
        if not paddle.is_compiled_with_cuda():
            raise RuntimeError(
                "PaddlePaddle is not compiled with CUDA support. "
                "Install a compatible PaddlePaddle GPU build or update the "
                "OCR initialization for CPU mode."
            )

        paddle.device.set_device("gpu:0")

        ocr = PaddleOCR(
            lang="en",
            device="gpu:0",
        )

        logger.info("PaddleOCR GPU initialized successfully")
        logger.info("Paddle version: %s", paddle.__version__)
        logger.info("Device: %s", paddle.device.get_device())

    except Exception:
        logger.exception("Failed to initialize PaddleOCR")
        raise

    yield

    logger.info("Shutting down VisaDoc OCR service")
    ocr = None


app = FastAPI(
    title=SERVICE_NAME,
    description="GPU accelerated OCR API for VisaDoc AI",
    version="4.2.0",
    lifespan=lifespan,
)


# =========================================================
# FILE VALIDATION
# =========================================================

async def read_upload(file: UploadFile):
    filename = file.filename or "document"
    extension = Path(filename).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {extension}",
        )

    content = await file.read(MAX_FILE_SIZE + 1)

    if not content:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty.",
        )

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File exceeds the configured upload limit "
                f"({MAX_FILE_SIZE} bytes)."
            ),
        )

    return filename, extension, content


# =========================================================
# OCR IMAGE PROCESSING
# =========================================================

def _normalise_ocr_result(result: Any) -> dict[str, Any] | None:
    """
    Normalise PaddleOCR result objects across supported result wrappers.
    """
    if hasattr(result, "json"):
        result = result.json

    if callable(result):
        result = result()

    if not isinstance(result, dict):
        return None

    data = result.get("res", result)

    if not isinstance(data, dict):
        data = result

    return data


def extract_image(path: str) -> list[dict[str, Any]]:
    if ocr is None:
        raise RuntimeError("OCR engine is not initialized.")

    extracted: list[dict[str, Any]] = []

    with ocr_lock:
        results = ocr.predict(path)

        if results is None:
            return extracted

        for result in results:
            data = _normalise_ocr_result(result)

            if not isinstance(data, dict):
                continue

            texts = data.get("rec_texts") or []
            scores = data.get("rec_scores") or []

            # Compatibility with older PaddleOCR output structures.
            if not texts and isinstance(data.get("rec_text"), list):
                texts = data.get("rec_text", [])
            if not scores and isinstance(data.get("rec_score"), list):
                scores = data.get("rec_score", [])

            for index, text_value in enumerate(texts):
                if text_value is None:
                    continue

                try:
                    score = float(scores[index]) if index < len(scores) else 0.0
                except (TypeError, ValueError):
                    score = 0.0

                extracted.append(
                    {
                        "text": str(text_value),
                        "confidence": score,
                    }
                )

    return extracted


# =========================================================
# DOCUMENT PROCESSING
# =========================================================

def _notify_progress(
    callback: Callable | None,
    processed: int,
    total: int,
    failed_pages: list[dict[str, Any]],
) -> None:
    if callback is None:
        return

    try:
        callback(processed, total, list(failed_pages))
    except Exception:
        logger.exception("Progress callback failed")


def _render_and_ocr_pdf_page(
    document: fitz.Document,
    page_index: int,
    temp_dir: str,
) -> list[dict[str, Any]]:
    page_number = page_index + 1
    image_path = os.path.join(temp_dir, f"page_{page_number}.png")

    page = None
    pix = None

    try:
        page = document.load_page(page_index)

        # PyMuPDF uses 72 DPI as its base resolution.
        scale = OCR_DPI / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            alpha=False,
        )
        pix.save(image_path)

        # Release PDF rendering objects before invoking the OCR engine.
        del pix
        pix = None
        del page
        page = None

        return extract_image(image_path)

    finally:
        if pix is not None:
            del pix
        if page is not None:
            del page
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except OSError:
                logger.warning("Could not remove temporary image: %s", image_path)


def process_document(
    filename: str,
    content: bytes,
    extension: str,
    progress_callback: Callable | None = None,
):
    output: list[dict[str, Any]] = []
    failed_pages: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="visadoc_") as temp_dir:
        if extension.lower() == ".pdf":
            try:
                document = fitz.open(
                    stream=content,
                    filetype="pdf",
                )
            except Exception as exc:
                raise ValueError(f"Invalid PDF document: {exc}") from exc

            try:
                page_count = len(document)

                if page_count == 0:
                    raise ValueError("PDF contains no pages.")

                # This is the only PDF page-limit check.
                # It uses the effective value loaded from the project .env.
                if page_count > OCR_MAX_PAGES:
                    raise PDFPageLimitError(page_count)

                logger.info(
                    "Processing PDF %s: %d pages (limit=%d, batch=%d)",
                    filename,
                    page_count,
                    OCR_MAX_PAGES,
                    OCR_BATCH_SIZE,
                )

                _notify_progress(progress_callback, 0, page_count, [])

                # Process a bounded number of pages per batch.
                # Pages are OCR-processed sequentially within each batch so
                # multiple full-resolution page images are never held at once.
                for batch_start in range(0, page_count, OCR_BATCH_SIZE):
                    batch_end = min(batch_start + OCR_BATCH_SIZE, page_count)

                    logger.info(
                        "OCR batch: pages %d-%d of %d",
                        batch_start + 1,
                        batch_end,
                        page_count,
                    )

                    for page_index in range(batch_start, batch_end):
                        page_number = page_index + 1

                        try:
                            page_results = _render_and_ocr_pdf_page(
                                document,
                                page_index,
                                temp_dir,
                            )

                            output.append(
                                {
                                    "page": page_number,
                                    "results": page_results,
                                    "status": "OCR_COMPLETED",
                                }
                            )

                        except Exception as exc:
                            logger.exception(
                                "OCR failed for PDF page %d",
                                page_number,
                            )

                            error_message = (
                                str(exc) or "OCR failed for this page."
                            )

                            failure = {
                                "page": page_number,
                                "error": error_message,
                            }
                            failed_pages.append(failure)

                            output.append(
                                {
                                    "page": page_number,
                                    "results": [],
                                    "status": "OCR_FAILED",
                                    "error": error_message,
                                }
                            )

                        _notify_progress(
                            progress_callback,
                            len(output),
                            page_count,
                            failed_pages,
                        )

                    # Encourage release of Python-side temporary objects
                    # between batches. The OCR model remains loaded.
                    gc.collect()

            finally:
                document.close()

        else:
            image_path = os.path.join(
                temp_dir,
                "document" + extension.lower(),
            )

            with open(image_path, "wb") as image_file:
                image_file.write(content)

            _notify_progress(progress_callback, 0, 1, [])

            try:
                results = extract_image(image_path)

                output.append(
                    {
                        "page": 1,
                        "results": results,
                        "status": "OCR_COMPLETED",
                    }
                )

            except Exception as exc:
                logger.exception("OCR failed for image")

                error_message = str(exc) or "OCR failed for this image."
                failed_pages.append(
                    {
                        "page": 1,
                        "error": error_message,
                    }
                )

                output.append(
                    {
                        "page": 1,
                        "results": [],
                        "status": "OCR_FAILED",
                        "error": error_message,
                    }
                )

            _notify_progress(
                progress_callback,
                len(output),
                1,
                failed_pages,
            )

    total_texts = sum(
        len(page["results"])
        for page in output
    )

    full_text = "\n".join(
        item["text"]
        for page in output
        for item in page["results"]
    )

    completed_pages = sum(
        1 for page in output
        if page["status"] == "OCR_COMPLETED"
    )

    failed_count = len(failed_pages)

    if failed_count == 0:
        result_status = "completed"
    elif completed_pages > 0:
        result_status = "partial"
    else:
        result_status = "failed"

    try:
        device_name = paddle.device.get_device()
    except Exception:
        device_name = "unknown"

    return {
        "filename": filename,
        "device": device_name,
        "total_pages": len(output),
        "total_texts": total_texts,
        "full_text": full_text,
        "pages": output,
        "failed_pages": failed_pages,
        "completed_pages": completed_pages,
        "status": result_status,
    }


# =========================================================
# JOB PROCESSOR
# =========================================================

def process_job(
    job_id: str,
    filename: str,
    content: bytes,
    extension: str,
):
    job = jobs.get(job_id)

    if job is None:
        logger.warning("OCR job %s no longer exists.", job_id)
        return

    logger.info("Starting OCR job: %s", job_id)

    job.update(
        {
            "status": "processing",
            "message": "Reading document...",
        }
    )

    def update_progress(
        pages_processed: int,
        total_pages: int,
        failed_pages: list[dict[str, Any]],
    ) -> None:
        current_job = jobs.get(job_id)
        if current_job is None:
            return

        current_job.update(
            {
                "pages_processed": pages_processed,
                "total_pages": total_pages,
                "failed_pages": failed_pages,
                "progress_percent": (
                    round(100 * pages_processed / total_pages)
                    if total_pages
                    else 0
                ),
                "message": "Reading document...",
            }
        )

    try:
        result = process_document(
            filename,
            content,
            extension,
            update_progress,
        )

        result_status = result["status"]
        is_partial = result_status == "partial"
        is_failed = result_status == "failed"

        job.update(
            {
                "status": result_status,
                "success": result_status == "completed",
                "result": result,
                "failed_pages": result["failed_pages"],
                "pages_processed": result["total_pages"],
                "total_pages": result["total_pages"],
                "progress_percent": 100,
                "message": (
                    "Some pages could not be read. Retry OCR before verification."
                    if is_partial
                    else "Document reading failed. Please retry."
                    if is_failed
                    else "OCR completed. Ready for verification."
                ),
                "error": (
                    {
                        "code": "OCR_PARTIAL",
                        "message": "One or more pages could not be read.",
                        "failedPages": result["failed_pages"],
                    }
                    if is_partial
                    else {
                        "code": "OCR_PROCESSING_ERROR",
                        "message": "OCR failed for every page.",
                        "failedPages": result["failed_pages"],
                    }
                    if is_failed
                    else None
                ),
            }
        )

        logger.info(
            "OCR job %s finished with status=%s, pages=%s, failed=%s",
            job_id,
            result_status,
            result["total_pages"],
            len(result["failed_pages"]),
        )

    except Exception as exc:
        logger.exception("OCR job failed: %s", job_id)

        if isinstance(exc, PDFPageLimitError):
            error = {
                "code": "PDF_PAGE_LIMIT_EXCEEDED",
                "message": str(exc),
                "totalPages": exc.total_pages,
                "maximumAllowedPages": exc.maximum_pages,
            }
            job["total_pages"] = exc.total_pages
        else:
            error = {
                "code": "OCR_PROCESSING_ERROR",
                "message": str(exc) or "OCR processing failed.",
            }

        job.update(
            {
                "status": "failed",
                "success": False,
                "message": "Document reading failed. Please retry.",
                "error": error,
                "result": None,
            }
        )


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/health")
def health():
    try:
        device_name = paddle.device.get_device()
    except Exception:
        device_name = "unknown"

    return {
        "status": "healthy" if ocr is not None else "initializing",
        "service": SERVICE_NAME,
        "version": "4.2.0",
        "paddle_version": paddle.__version__,
        "device": device_name,
        "cuda": paddle.is_compiled_with_cuda(),
        "ocr_initialized": ocr is not None,
        "ocr_max_pages": OCR_MAX_PAGES,
        "ocr_batch_size": OCR_BATCH_SIZE,
        "ocr_dpi": OCR_DPI,
        "max_file_size": MAX_FILE_SIZE,
        "environment_file_loaded": ENV_FILE.is_file(),
    }


# =========================================================
# ASYNCHRONOUS OCR JOB API
# =========================================================

@app.post("/ocr/jobs", status_code=202)
async def create_ocr_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    filename, extension, content = await read_upload(file)
    await file.close()

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "success": None,
        "filename": filename,
        "result": None,
        "error": None,
        "message": "OCR job queued",
        "pages_processed": 0,
        "total_pages": 0,
        "failed_pages": [],
        "progress_percent": 0,
    }

    background_tasks.add_task(
        process_job,
        job_id,
        filename,
        content,
        extension,
    )

    logger.info(
        "OCR job created: id=%s filename=%s max_pages=%s batch_size=%s",
        job_id,
        filename,
        OCR_MAX_PAGES,
        OCR_BATCH_SIZE,
    )

    return {
        "success": True,
        "job_id": job_id,
        "status": "queued",
        "pages_processed": 0,
        "total_pages": 0,
        "failed_pages": [],
        "progress_percent": 0,
        "message": "OCR job submitted successfully",
        "status_url": f"/ocr/jobs/{job_id}",
    }


# =========================================================
# GET OCR JOB STATUS
# =========================================================

@app.get("/ocr/jobs/{job_id}")
def get_ocr_job(job_id: str):
    job = jobs.get(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="OCR job not found",
        )

    return job


# =========================================================
# SYNCHRONOUS OCR API (BACKWARD COMPATIBILITY)
# =========================================================

@app.post("/ocr/extract")
async def extract_document(
    file: UploadFile = File(...),
):
    filename, extension, content = await read_upload(file)
    await file.close()

    try:
        result = process_document(
            filename,
            content,
            extension,
        )

        return {
            "success": result["status"] == "completed",
            **result,
        }

    except PDFPageLimitError as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "PDF_PAGE_LIMIT_EXCEEDED",
                "message": str(exc),
                "totalPages": exc.total_pages,
                "maximumAllowedPages": exc.maximum_pages,
            },
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception("OCR extraction failed")

        raise HTTPException(
            status_code=500,
            detail="OCR processing failed",
        ) from exc


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():
    return {
        "service": SERVICE_NAME,
        "version": "4.2.0",
        "status": "running",
        "configuration": {
            "ocr_max_pages": OCR_MAX_PAGES,
            "ocr_batch_size": OCR_BATCH_SIZE,
            "ocr_dpi": OCR_DPI,
            "max_file_size": MAX_FILE_SIZE,
        },
        "endpoints": {
            "health": "/health",
            "create_job": "/ocr/jobs",
            "job_status": "/ocr/jobs/{job_id}",
            "extract": "/ocr/extract",
            "documentation": "/docs",
        },
    }
