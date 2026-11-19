from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import numpy as np
import psutil
import pymupdf
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError


logger = logging.getLogger("visadoc-ocr.engine")


@dataclass
class OCRPage:
    page: int
    text: str = ""
    confidence: float | None = None
    method: str = "ocr"
    boxes: list[dict[str, Any]] = field(default_factory=list)
    render_seconds: float = 0.0
    ocr_seconds: float = 0.0
    status: str = "OCR_COMPLETED"
    error: str | None = None


@dataclass
class OCRDocument:
    pages: list[OCRPage]
    metrics: dict[str, Any]
    failed_pages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        sections = []
        for page in self.pages:
            marker = f"--- PAGE {page.page} ---"
            sections.append(f"{marker}\n{page.text}" if page.text else f"{marker} UNREADABLE")
        return "\n\n".join(sections).strip()

    @property
    def confidence(self) -> float:
        scores = [page.confidence for page in self.pages if page.confidence is not None]
        return round(sum(scores) / len(scores), 3) if scores else 0.0


class OCRProcessingError(RuntimeError):
    pass


class OCRCancelledError(OCRProcessingError):
    pass


class PDFPageLimitError(ValueError):
    def __init__(self, total_pages: int, maximum_pages: int) -> None:
        self.total_pages = total_pages
        self.maximum_pages = maximum_pages
        super().__init__(
            f"PDF contains {total_pages} pages; the maximum allowed is "
            f"{maximum_pages}. Please upload a PDF with {maximum_pages} pages "
            "or fewer, or split it into smaller files."
        )


class OCRService:
    def __init__(self) -> None:
        self.dpi = max(72, int(os.getenv("OCR_DPI", "180")))
        self.max_pdf_pages = self._positive_integer(
            os.getenv("OCR_MAX_PAGES", "100"), "OCR_MAX_PAGES"
        )
        self.batch_size = self._positive_integer(
            os.getenv("OCR_BATCH_SIZE", "10"), "OCR_BATCH_SIZE"
        )
        self.max_image_side = max(1000, int(os.getenv("OCR_MAX_IMAGE_SIDE", "3200")))
        self.min_image_side = max(
            800,
            min(
                self.max_image_side,
                int(os.getenv("OCR_MIN_IMAGE_SIDE", "1400")),
            ),
        )
        self.detection_limit_side_len = max(
            320, int(os.getenv("OCR_DETECTION_LIMIT_SIDE_LEN", "960"))
        )
        self.workers = max(1, min(2, int(os.getenv("OCR_WORKERS", "2"))))
        self.model_size = os.getenv("OCR_MODEL_SIZE", "auto").strip().lower()
        self.primary_engine = os.getenv(
            "OCR_PRIMARY_ENGINE", "paddleocr"
        ).strip().lower()
        if self.primary_engine not in {"paddleocr", "tesseract", "rapidocr"}:
            raise ValueError("OCR_PRIMARY_ENGINE must be paddleocr, tesseract, or rapidocr.")
        self.tesseract_fallback_enabled = os.getenv(
            "OCR_TESSERACT_FALLBACK", "true"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.tesseract_language = os.getenv("TESSERACT_LANG", "eng+khm")
        self.tesseract_path = os.getenv(
            "TESSERACT_PATH", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        )
        self.tesseract_timeout = max(1, int(os.getenv("TESSERACT_TIMEOUT", "45")))
        self.process_timeout = max(1, int(os.getenv("OCR_PROCESS_TIMEOUT_SECONDS", "900")))
        self._model: Any = None
        self._paddle: Any = None
        self._model_lock = threading.Lock()
        self._job_lock = threading.BoundedSemaphore(1)
        self._initialized = False
        self._cpu_retry_attempted = False
        self._tesseract_ready = False
        self._hardware: dict[str, Any] = {}
        self._runtime: dict[str, Any] = {
            "engine": "unavailable",
            "model": None,
            "model_version": None,
            "device": "none",
            "fallback_reason": None,
            "initialized": False,
        }

    @staticmethod
    def _positive_integer(value: str, name: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a positive integer.") from exc
        if parsed < 1:
            raise ValueError(f"{name} must be a positive integer.")
        return parsed

    @property
    def runtime_info(self) -> dict[str, Any]:
        return {**self._runtime, "hardware": self._hardware}

    def initialize(self) -> None:
        if self._initialized:
            return

        self._hardware = self._detect_hardware()
        available_ram = self._hardware.get("available_ram_mb") or 0
        if self.model_size in {"tiny", "small"}:
            model_size = self.model_size
        else:
            model_size = "tiny" if not available_ram or available_ram < 4096 else "small"

        if self.primary_engine == "rapidocr":
            # This explicit local mode uses only ONNX files shipped inside the
            # installed package. It does not download models or send documents.
            try:
                import rapidocr_onnxruntime
                from rapidocr_onnxruntime import RapidOCR

                model_dir = Path(rapidocr_onnxruntime.__file__).resolve().parent / "models"
                models = {}
                for stage, pattern in (("det", "*det*.onnx"), ("rec", "*rec*.onnx"), ("cls", "*cls*.onnx")):
                    candidates = sorted(model_dir.glob(pattern))
                    if len(candidates) != 1:
                        raise FileNotFoundError(f"Expected one bundled {stage} model in the local RapidOCR installation")
                    models[f"{stage}_model_path"] = str(candidates[0])
                self._model = RapidOCR(
                    **models,
                    intra_op_num_threads=min(2, self._hardware.get("logical_cpu_count") or 1),
                    inter_op_num_threads=1,
                )
            except Exception as exc:
                self._model = None
                raise OCRProcessingError(
                    "Local RapidOCR could not initialize. Install ai_service/requirements-local.txt "
                    "and verify its bundled ONNX model files."
                ) from exc
            self._runtime.update(
                engine="rapidocr",
                model="bundled-PP-OCRv3-onnx",
                model_version=self._package_version("rapidocr-onnxruntime"),
                device="cpu",
                initialized=True,
                language_limitations="English/Latin text supported; Khmer transcription is not guaranteed. Use a Khmer-capable engine or staff review.",
                model_source="installed-package-local-files",
            )
            self._initialized = True
            return

        if self.primary_engine == "tesseract":
            self._prepare_tesseract()
            if not self._tesseract_ready:
                raise OCRProcessingError(
                    "Tesseract is configured as the primary OCR engine but is unavailable."
                )
            self._runtime.update(
                engine="tesseract",
                model="tesseract-ocr",
                model_version=self._package_version("pytesseract"),
                device="cpu",
                initialized=True,
            )
            self._initialized = True
            return

        cpu_count = self._hardware.get("logical_cpu_count") or 2
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        os.environ.setdefault(
            "PADDLE_PDX_CPU_NUM_THREADS", str(min(4, max(1, cpu_count)))
        )
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "false")
        if self.tesseract_fallback_enabled:
            self._prepare_tesseract()

        try:
            import paddle
            from paddleocr import PaddleOCR

            self._paddle = paddle
            paddle_cuda_supported = bool(paddle.is_compiled_with_cuda())
            try:
                paddle_gpu_count = (
                    int(paddle.device.cuda.device_count())
                    if paddle_cuda_supported
                    else 0
                )
            except Exception as exc:
                logger.warning("Paddle CUDA device probe failed: %s", exc)
                paddle_gpu_count = 0
            self._hardware.update(
                paddle_cuda_supported=paddle_cuda_supported,
                paddle_gpu_count=paddle_gpu_count,
            )
            gpu_ready = (
                self._hardware["nvidia_gpu_available"]
                and paddle_cuda_supported
                and paddle_gpu_count > 0
            )
            device = "gpu:0" if gpu_ready else "cpu"
            fallback_reason = None
            try:
                self._create_paddle_model(PaddleOCR, model_size, device)
            except Exception as gpu_error:
                if device != "gpu:0":
                    raise
                logger.exception("Paddle GPU initialization failed; retrying with CPU")
                self._model = None
                self._create_paddle_model(PaddleOCR, model_size, "cpu")
                device = "cpu"
                fallback_reason = (
                    f"GPU model initialization failed; retried with CPU: {gpu_error}"
                )
            self._runtime.update(
                engine="paddleocr",
                model=f"PP-OCRv6_{model_size}",
                model_version=self._package_version("paddleocr"),
                device=device,
                fallback_reason=fallback_reason,
                initialized=True,
            )
            logger.info(
                "Initialized PaddleOCR model=%s device=%s workers=%s",
                self._runtime["model"],
                device,
                self.workers,
            )
        except Exception as exc:
            reason = f"PaddleOCR initialization failed: {exc}"
            logger.exception("%s", reason)
            self._model = None
            self._runtime.update(
                engine="unavailable",
                model=None,
                model_version=None,
                device="none",
                fallback_reason=reason,
            )
            self._initialize_tesseract_fallback(reason)

        self._initialized = True
        self._runtime["initialized"] = self._runtime["engine"] != "unavailable"

    def _create_paddle_model(self, paddle_ocr_type: Any, model_size: str, device: str) -> None:
        self._model = paddle_ocr_type(
            text_detection_model_name=f"PP-OCRv6_{model_size}_det",
            text_recognition_model_name=f"PP-OCRv6_{model_size}_rec",
            # Keep PaddleOCR as the primary engine while allowing it to
            # correct rotated camera captures and mildly warped scans.
            use_doc_orientation_classify=True,
            use_doc_unwarping=True,
            use_textline_orientation=True,
            text_recognition_batch_size=self.workers,
            text_det_limit_side_len=self.detection_limit_side_len,
            text_det_limit_type="max",
            device=device,
        )

    def _prepare_tesseract(self) -> None:
        if (
            not self.tesseract_fallback_enabled
            and self.primary_engine != "tesseract"
        ):
            return
        if os.path.isfile(self.tesseract_path):
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
        try:
            installed = set(pytesseract.get_languages(config=""))
            languages = [
                language
                for language in self.tesseract_language.split("+")
                if language in installed
            ]
            if not languages:
                raise RuntimeError(
                    f"No requested Tesseract language is installed: {self.tesseract_language}"
                )
            self.tesseract_language = "+".join(languages)
            self._tesseract_ready = True
        except Exception as exc:
            logger.warning("Tesseract fallback is not available: %s", exc)

    def _initialize_tesseract_fallback(self, reason: str) -> None:
        if not self.tesseract_fallback_enabled:
            return
        if not self._tesseract_ready:
            self._prepare_tesseract()
        try:
            if not self._tesseract_ready:
                raise RuntimeError("No requested Tesseract language is installed.")
            self._runtime.update(
                engine="tesseract",
                model="tesseract-ocr",
                model_version=self._package_version("pytesseract"),
                device="cpu",
                fallback_reason=reason,
                initialized=True,
            )
            logger.warning("Using Tesseract fallback: %s", reason)
        except Exception as exc:
            logger.exception("Tesseract fallback initialization failed")
            self._runtime["fallback_reason"] = f"{reason}; Tesseract fallback failed: {exc}"

    def _detect_hardware(self) -> dict[str, Any]:
        cpu_info: dict[str, Any] = {}
        try:
            import cpuinfo

            details = cpuinfo.get_cpu_info()
            cpu_info = {
                "brand": details.get("brand_raw"),
                "architecture": details.get("arch"),
                "flags": details.get("flags", []),
            }
        except Exception as exc:
            logger.warning("Could not read CPU feature flags: %s", exc)
            cpu_info = {"brand": None, "architecture": None, "flags": []}

        try:
            memory = psutil.virtual_memory()
            total_ram_mb = round(memory.total / (1024 * 1024))
            available_ram_mb = round(memory.available / (1024 * 1024))
        except Exception as exc:
            logger.warning("Could not read system RAM: %s", exc)
            total_ram_mb = None
            available_ram_mb = None

        nvidia_smi = shutil.which("nvidia-smi")
        gpu_name = None
        if nvidia_smi:
            try:
                result = subprocess.run(
                    [
                        nvidia_smi,
                        "--query-gpu=name",
                        "--format=csv,noheader",
                    ],
                    capture_output=True,
                    check=True,
                    text=True,
                    timeout=3,
                )
                gpu_name = result.stdout.strip().splitlines()[0] or None
            except (OSError, subprocess.SubprocessError, IndexError) as exc:
                logger.info("NVIDIA GPU probe did not find a usable device: %s", exc)

        return {
            "cpu": cpu_info,
            "logical_cpu_count": os.cpu_count(),
            "total_ram_mb": total_ram_mb,
            "available_ram_mb": available_ram_mb,
            "nvidia_gpu_available": gpu_name is not None,
            "nvidia_gpu_name": gpu_name,
            "paddle_cuda_supported": None,
            "openvino_available": importlib.util.find_spec("openvino") is not None,
            "openvino_selected": False,
        }

    @staticmethod
    def _package_version(package: str) -> str | None:
        try:
            return importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            return None

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()
        if self._runtime["engine"] == "unavailable":
            raise OCRProcessingError(
                self._runtime.get("fallback_reason") or "No OCR engine is available."
            )

    def process_document(
        self,
        data: bytes,
        file_type: str,
        cancellation_event: threading.Event | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> OCRDocument:
        self._ensure_initialized()
        self._check_cancelled(cancellation_event)
        if not self._job_lock.acquire(timeout=self.process_timeout):
            raise TimeoutError("Another OCR job is still running.")

        started = time.perf_counter()
        process = psutil.Process()
        cpu_before = process.cpu_times()
        rendered_seconds = 0.0
        ocr_seconds = 0.0
        try:
            if file_type == "pdf":
                pages, failed_pages = self._process_pdf(
                    data, cancellation_event, progress_callback
                )
            else:
                page = self._process_image(data, progress_callback)
                pages = [page]
                failed_pages = []
                ocr_seconds = page.ocr_seconds
            self._check_cancelled(cancellation_event)

            rendered_seconds = sum(page.render_seconds for page in pages)
            ocr_seconds = sum(page.ocr_seconds for page in pages)
            elapsed = time.perf_counter() - started
            cpu_after = process.cpu_times()
            cpu_seconds = (
                cpu_after.user
                + cpu_after.system
                - cpu_before.user
                - cpu_before.system
            )
            cpu_percent = round(
                100 * cpu_seconds / max(elapsed * (os.cpu_count() or 1), 0.001), 2
            )
            ram_mb = round(process.memory_info().rss / (1024 * 1024), 2)
            metrics = {
                "pdf_render_seconds": round(rendered_seconds, 3),
                "ocr_seconds": round(ocr_seconds, 3),
                "total_processing_seconds": round(elapsed, 3),
                "cpu_utilization_percent": cpu_percent,
                "ram_usage_mb": ram_mb,
                "inference_device": self._runtime["device"],
                "ocr_engine": self._runtime["engine"],
                "ocr_model": self._runtime["model"],
                "ocr_model_version": self._runtime["model_version"],
                "hardware": self._hardware,
                "page_count": len(pages),
                "pages_processed": len(pages),
                "failed_pages": failed_pages,
                "status": (
                    "OCR_PARTIAL"
                    if failed_pages
                    else "OCR_COMPLETED"
                ),
            }
            return OCRDocument(
                pages=pages,
                metrics=metrics,
                failed_pages=failed_pages,
            )
        finally:
            self._job_lock.release()

    def _process_image(
        self,
        data: bytes,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> OCRPage:
        image, render_seconds = self._decode_image(data)
        if progress_callback:
            progress_callback(0, 1)
        started = time.perf_counter()
        text, confidence, boxes = self._recognize(image)
        if progress_callback:
            progress_callback(1, 1)
        return OCRPage(
            page=1,
            text=text,
            confidence=confidence,
            method="ocr",
            boxes=boxes,
            render_seconds=render_seconds,
            ocr_seconds=round(time.perf_counter() - started, 3),
        )

    def _process_pdf(
        self,
        data: bytes,
        cancellation_event: threading.Event | None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> tuple[list[OCRPage], list[dict[str, Any]]]:
        pages: list[OCRPage] = []
        failed_pages: list[dict[str, Any]] = []
        scanned_indices: list[int] = []
        processed_count = 0
        try:
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass:
                    raise ValueError("Password-protected PDF is not supported.")
                page_count = len(document)
                if page_count == 0:
                    raise ValueError("PDF contains no pages.")
                if progress_callback:
                    progress_callback(0, page_count)
                if page_count > self.max_pdf_pages:
                    raise PDFPageLimitError(page_count, self.max_pdf_pages)
                for index in range(page_count):
                    self._check_cancelled(cancellation_event)
                    try:
                        native_text = document.load_page(index).get_text("text").strip()
                    except Exception as exc:
                        logger.exception(
                            "Could not read PDF page text: page=%s", index + 1
                        )
                        message = f"Could not read page text: {exc}"
                        pages.append(
                            OCRPage(
                                page=index + 1,
                                method="ocr",
                                status="OCR_FAILED",
                                error=message,
                            )
                        )
                        failed_pages.append({"page": index + 1, "error": message})
                        processed_count += 1
                        if progress_callback:
                            progress_callback(processed_count, page_count)
                        continue
                    if len(native_text) >= 20:
                        pages.append(
                            OCRPage(
                                page=index + 1,
                                text=native_text,
                                confidence=None,
                                method="embedded_text",
                                status="OCR_COMPLETED",
                            )
                        )
                        processed_count += 1
                        if progress_callback:
                            progress_callback(processed_count, page_count)
                    else:
                        pages.append(
                            OCRPage(
                                page=index + 1,
                                method="ocr",
                                status="OCR_PROCESSING",
                            )
                        )
                        scanned_indices.append(index)
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise OCRProcessingError(f"PDF processing failed: {exc}") from exc

        for offset in range(0, len(scanned_indices), self.batch_size):
            self._check_cancelled(cancellation_event)
            batch_indices = scanned_indices[offset : offset + self.batch_size]
            render_started = time.perf_counter()
            rendered: list[tuple[int, np.ndarray, float]] = []
            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {
                    executor.submit(self._render_pdf_page, data, index): index
                    for index in batch_indices
                }
                for future in as_completed(futures):
                    page_index = futures[future]
                    try:
                        image, render_seconds = future.result()
                        rendered.append((page_index, image, render_seconds))
                        del image
                    except Exception as exc:
                        logger.exception(
                            "Could not render PDF page: page=%s", page_index + 1
                        )
                        message = str(exc) or "PDF page rendering failed."
                        pages[page_index] = OCRPage(
                            page=page_index + 1,
                            method="ocr",
                            status="OCR_FAILED",
                            error=message,
                        )
                        failed_pages.append(
                            {"page": page_index + 1, "error": message}
                        )
                        processed_count += 1
                        if progress_callback:
                            progress_callback(processed_count, len(pages))
            batch_render_seconds = time.perf_counter() - render_started
            if not rendered:
                del futures
                continue
            rendered.sort(key=lambda item: item[0])
            images = [item[1] for item in rendered]
            batch_started = time.perf_counter()
            try:
                recognized = self._recognize_batch(images)
                if len(recognized) != len(rendered):
                    raise OCRProcessingError(
                        f"OCR returned {len(recognized)} results for "
                        f"{len(rendered)} rendered pages."
                    )
            except Exception as batch_error:
                logger.warning(
                    "OCR batch failed; retrying pages individually: %s",
                    batch_error,
                )
                recognized = []
                for page_index, image, render_seconds in rendered:
                    page_started = time.perf_counter()
                    try:
                        text, confidence, boxes = self._recognize(image)
                        pages[page_index] = OCRPage(
                            page=page_index + 1,
                            text=text,
                            confidence=confidence,
                            method="ocr",
                            boxes=boxes,
                            render_seconds=round(render_seconds, 3),
                            ocr_seconds=round(time.perf_counter() - page_started, 3),
                        )
                    except Exception as exc:
                        logger.exception(
                            "Could not recognize PDF page: page=%s", page_index + 1
                        )
                        message = str(exc) or "OCR failed for this page."
                        pages[page_index] = OCRPage(
                            page=page_index + 1,
                            method="ocr",
                            status="OCR_FAILED",
                            error=message,
                            render_seconds=round(render_seconds, 3),
                            ocr_seconds=round(time.perf_counter() - page_started, 3),
                        )
                        failed_pages.append(
                            {"page": page_index + 1, "error": message}
                        )
                    processed_count += 1
                    if progress_callback:
                        progress_callback(processed_count, len(pages))
            else:
                batch_ocr_seconds = time.perf_counter() - batch_started
                per_page_ocr = batch_ocr_seconds / max(len(rendered), 1)
                for position, (page_index, _image, render_seconds) in enumerate(
                    rendered
                ):
                    text, confidence, boxes = recognized[position]
                    pages[page_index] = OCRPage(
                        page=page_index + 1,
                        text=text,
                        confidence=confidence,
                        method="ocr",
                        boxes=boxes,
                        render_seconds=round(render_seconds, 3),
                        ocr_seconds=round(per_page_ocr, 3),
                    )
                    processed_count += 1
                    if progress_callback:
                        progress_callback(processed_count, len(pages))
            logger.info(
                "Processed PDF pages %s-%s/%s render=%.3fs OCR=%.3fs",
                batch_indices[0] + 1,
                batch_indices[-1] + 1,
                len(pages),
                batch_render_seconds,
                time.perf_counter() - batch_started,
            )
            del futures, future, images, rendered, recognized
        failed_pages.sort(key=lambda item: item["page"])
        return pages, failed_pages

    def _render_pdf_page(self, data: bytes, page_index: int) -> tuple[np.ndarray, float]:
        started = time.perf_counter()
        try:
            with pymupdf.open(stream=data, filetype="pdf") as document:
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(self.dpi / 72, self.dpi / 72),
                    colorspace=pymupdf.csRGB,
                    alpha=False,
                )
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                if max(image.size) > self.max_image_side:
                    image.thumbnail(
                        (self.max_image_side, self.max_image_side),
                        Image.Resampling.LANCZOS,
                    )
                return np.asarray(image), time.perf_counter() - started
        except Exception as exc:
            raise OCRProcessingError(
                f"Could not render PDF page {page_index + 1}: {exc}"
            ) from exc

    def _decode_image(self, data: bytes) -> tuple[np.ndarray, float]:
        started = time.perf_counter()
        try:
            with Image.open(BytesIO(data)) as source:
                source.verify()
            with Image.open(BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                # Phone uploads are frequently 500–900px wide. Upscale small
                # captures before detection so passport MRZ characters are not
                # discarded, while keeping the original aspect ratio.
                if max(image.size) < self.min_image_side:
                    scale = self.min_image_side / max(image.size)
                    image = image.resize(
                        (round(image.width * scale), round(image.height * scale)),
                        Image.Resampling.LANCZOS,
                    )
                # Mild enhancement improves photographed security backgrounds
                # without inventing text or changing the source file.
                image = ImageOps.autocontrast(image, cutoff=1)
                image = ImageEnhance.Contrast(image).enhance(1.12)
                image = ImageEnhance.Sharpness(image).enhance(1.25)
                if max(image.size) > self.max_image_side:
                    image.thumbnail(
                        (self.max_image_side, self.max_image_side),
                        Image.Resampling.LANCZOS,
                    )
                return np.asarray(image), time.perf_counter() - started
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ValueError("Invalid or damaged image file.") from exc

    def _recognize_batch(
        self, images: list[np.ndarray]
    ) -> list[tuple[str, float, list[dict[str, Any]]]]:
        self._ensure_initialized()
        if self._runtime["engine"] == "rapidocr":
            with self._model_lock:
                results = []
                for image in images:
                    try:
                        detections, _timing = self._model(image)
                    except Exception as exc:
                        raise OCRProcessingError("Local RapidOCR inference failed.") from exc
                    mapping = {"rec_texts": [], "rec_scores": [], "rec_polys": []}
                    for polygon, text, confidence in detections or []:
                        mapping["rec_texts"].append(text)
                        mapping["rec_scores"].append(confidence)
                        mapping["rec_polys"].append(polygon)
                    # Preserve the established page/box contract and measured
                    # confidence values used by extraction and review.
                    results.append(self._parse_paddle_result(mapping))
                return results
        if self._runtime["engine"] == "tesseract":
            with ThreadPoolExecutor(
                max_workers=min(self.workers, len(images))
            ) as executor:
                return list(executor.map(self._recognize_tesseract, images))

        with self._model_lock:
            try:
                output = list(self._model.predict(input=images))
            except Exception as exc:
                if self._runtime["device"].startswith("gpu") and not self._cpu_retry_attempted:
                    self._cpu_retry_attempted = True
                    logger.exception("GPU inference failed; retrying with Paddle CPU")
                    self._switch_to_cpu()
                    try:
                        output = list(self._model.predict(input=images))
                    except Exception as cpu_error:
                        if self._tesseract_ready:
                            logger.exception("Paddle CPU retry failed; switching to Tesseract")
                            self._switch_to_tesseract(str(cpu_error))
                            return [self._recognize_tesseract(image) for image in images]
                        raise OCRProcessingError(
                            f"PaddleOCR CPU retry failed: {cpu_error}"
                        ) from cpu_error
                elif self._tesseract_ready:
                    logger.exception("PaddleOCR inference failed; switching to Tesseract")
                    self._switch_to_tesseract(str(exc))
                    return [self._recognize_tesseract(image) for image in images]
                else:
                    raise OCRProcessingError(f"PaddleOCR inference failed: {exc}") from exc

        if len(output) != len(images):
            raise OCRProcessingError(
                f"PaddleOCR returned {len(output)} results for {len(images)} pages."
            )
        return [self._parse_paddle_result(item) for item in output]

    def _switch_to_tesseract(self, reason: str) -> None:
        self._model = None
        self._runtime.update(
            engine="tesseract",
            model="tesseract-ocr",
            model_version=self._package_version("pytesseract"),
            device="cpu",
            fallback_reason=f"PaddleOCR inference failed: {reason}",
        )

    def _recognize(self, image: np.ndarray) -> tuple[str, float, list[dict[str, Any]]]:
        return self._recognize_batch([image])[0]

    def _switch_to_cpu(self) -> None:
        from paddleocr import PaddleOCR

        model_name = str(self._runtime.get("model") or "PP-OCRv6_small")
        model_size = "tiny" if "_tiny" in model_name else "small"
        self._create_paddle_model(PaddleOCR, model_size, "cpu")
        self._runtime["device"] = "cpu"
        self._runtime["fallback_reason"] = "GPU inference failed; retried with CPU."

    @staticmethod
    def _result_mapping(result: Any) -> dict[str, Any]:
        value = getattr(result, "json", result)
        if callable(value):
            value = value()
        if isinstance(value, dict) and isinstance(value.get("res"), dict):
            value = value["res"]
        if isinstance(value, dict):
            return value
        if hasattr(result, "to_dict"):
            converted = result.to_dict()
            if isinstance(converted, dict):
                return converted
        raise OCRProcessingError("PaddleOCR returned an unsupported result format.")

    @classmethod
    def _parse_paddle_result(
        cls, result: Any
    ) -> tuple[str, float, list[dict[str, Any]]]:
        mapping = cls._result_mapping(result)
        texts = list(mapping.get("rec_texts") or [])
        raw_scores = mapping.get("rec_scores")
        scores = list(raw_scores) if raw_scores is not None else []
        raw_polygons = (
            mapping.get("rec_polys")
            if mapping.get("rec_polys") is not None
            else mapping.get("dt_polys")
        )
        if raw_polygons is None:
            raw_polygons = mapping.get("text_det_polys")
        polygons = list(raw_polygons) if raw_polygons is not None else []

        entries = []
        for index, text_value in enumerate(texts):
            text = str(text_value).strip()
            if not text:
                continue
            try:
                score = float(scores[index]) if index < len(scores) else 0.0
            except (TypeError, ValueError):
                score = 0.0
            polygon = polygons[index] if index < len(polygons) else None
            points = []
            if polygon is not None:
                for point in np.asarray(polygon).tolist():
                    if isinstance(point, (list, tuple)) and len(point) >= 2:
                        points.append([round(float(point[0]), 2), round(float(point[1]), 2)])
            top = min((point[1] for point in points), default=float(index))
            left = min((point[0] for point in points), default=0.0)
            entries.append(
                (
                    top,
                    left,
                    text,
                    max(0.0, min(1.0, score)),
                    {"text": text, "confidence": round(score, 4), "points": points},
                )
            )
        entries.sort(key=lambda item: (item[0], item[1]))
        text = "\n".join(item[2] for item in entries)
        scores_for_page = [item[3] for item in entries]
        confidence = (
            round(sum(scores_for_page) / len(scores_for_page), 3)
            if scores_for_page
            else 0.0
        )
        return text, confidence, [item[4] for item in entries]

    def _recognize_tesseract(
        self, image_data: np.ndarray
    ) -> tuple[str, float, list[dict[str, Any]]]:
        image = Image.fromarray(image_data).convert("L")
        image = ImageOps.autocontrast(image, cutoff=1)
        image = ImageEnhance.Contrast(image).enhance(1.5)
        image = ImageEnhance.Sharpness(image).enhance(1.5)
        image = image.filter(ImageFilter.MedianFilter(size=3))
        try:
            result = pytesseract.image_to_data(
                image,
                lang=self.tesseract_language,
                config="--oem 3 --psm 6",
                output_type=pytesseract.Output.DICT,
                timeout=self.tesseract_timeout,
            )
        except pytesseract.TesseractNotFoundError as exc:
            raise OCRProcessingError("Tesseract executable not found.") from exc
        except RuntimeError as exc:
            raise OCRProcessingError(f"Tesseract OCR failed: {exc}") from exc

        lines: dict[tuple[int, int, int], dict[str, Any]] = {}
        confidences = []
        for index, value in enumerate(result.get("text", [])):
            text = str(value).strip()
            try:
                score = float(result["conf"][index]) / 100
            except (KeyError, IndexError, TypeError, ValueError):
                score = -1
            if not text or score < 0:
                continue
            key = (
                int(result.get("block_num", [0])[index]),
                int(result.get("par_num", [0])[index]),
                int(result.get("line_num", [0])[index]),
            )
            left = int(result.get("left", [0])[index])
            top = int(result.get("top", [0])[index])
            right = left + int(result.get("width", [0])[index])
            bottom = top + int(result.get("height", [0])[index])
            line = lines.setdefault(
                key, {"words": [], "scores": [], "left": left, "top": top, "right": right, "bottom": bottom}
            )
            line["words"].append(text)
            line["scores"].append(score)
            line["left"] = min(line["left"], left)
            line["top"] = min(line["top"], top)
            line["right"] = max(line["right"], right)
            line["bottom"] = max(line["bottom"], bottom)
            confidences.append(score)

        ordered = sorted(lines.values(), key=lambda line: (line["top"], line["left"]))
        boxes = []
        for line in ordered:
            line_text = " ".join(line["words"])
            boxes.append(
                {
                    "text": line_text,
                    "confidence": round(sum(line["scores"]) / len(line["scores"]), 4),
                    "bbox": {
                        "x": line["left"],
                        "y": line["top"],
                        "width": line["right"] - line["left"],
                        "height": line["bottom"] - line["top"],
                    },
                }
            )
        text = "\n".join(box["text"] for box in boxes)
        confidence = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
        return text, confidence, boxes

    @staticmethod
    def _check_cancelled(event: threading.Event | None) -> None:
        if event is not None and event.is_set():
            raise OCRCancelledError("OCR processing was cancelled.")
