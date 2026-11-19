import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageStat, UnidentifiedImageError
from pytesseract import Output

from pdf2image import convert_from_path, pdfinfo_from_path

from mrz_parser import parse_mrz, save_mrz_json


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

TESSERACT_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
POPPLER_PATH = Path(
    r"C:\Users\ASUS\AppData\Local\Microsoft\WinGet\Packages"
    r"\oschwartz10612.Poppler_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\poppler-25.07.0\Library\bin"
)

DEFAULT_IMAGE = BASE_DIR / "passport.jpg"
OUTPUT_DIR = BASE_DIR / "ocr_results"

OCR_LANGUAGE = "eng+khm"
MRZ_LANGUAGE = "eng"
OCR_CONFIG = "--oem 3 --psm 3"
MRZ_PSM_MODES = (6, 7, 11, 13)

PDF_DPI = 200
OCR_TIMEOUT = 60
PDF_TIMEOUT = 120
MRZ_CROP_TOP_RATIO = 0.55
MRZ_TARGET_WIDTH = 2200

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("visa_ocr")


# ============================================================
# INITIALIZATION
# ============================================================

def initialize_ocr():
    if not TESSERACT_PATH.is_file():
        raise FileNotFoundError(
            f"Tesseract executable not found: {TESSERACT_PATH}"
        )

    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)

    try:
        available = set(pytesseract.get_languages(config=""))
    except Exception as exc:
        raise RuntimeError(f"Cannot read Tesseract language packs: {exc}") from exc

    missing = [lang for lang in ("eng", "khm") if lang not in available]
    if missing:
        logger.warning("Missing Tesseract language pack(s): %s", ", ".join(missing))

    selected = "+".join(lang for lang in ("eng", "khm") if lang in available)
    if not selected:
        raise RuntimeError("Neither eng nor khm Tesseract language pack is installed.")

    logger.info("Tesseract ready. OCR language: %s", selected)
    return selected


def initialize_poppler():
    if not POPPLER_PATH.is_dir():
        raise FileNotFoundError(f"Poppler folder not found: {POPPLER_PATH}")

    pdfinfo = POPPLER_PATH / "pdfinfo.exe"
    pdftoppm = POPPLER_PATH / "pdftoppm.exe"
    if not pdfinfo.is_file() or not pdftoppm.is_file():
        raise FileNotFoundError(
            "Poppler folder must contain pdfinfo.exe and pdftoppm.exe."
        )
    logger.info("Poppler ready: %s", POPPLER_PATH)


# ============================================================
# IMAGE PREPROCESSING AND OCR
# ============================================================

def preprocess_image(image: Image.Image) -> Image.Image:
    """General OCR preprocessing; returns a new RGB image."""
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")

    if image.width < 1800:
        scale = 1800 / image.width
        image = image.resize(
            (1800, max(1, int(image.height * scale))),
            Image.Resampling.LANCZOS,
        )

    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.5)
    gray = ImageEnhance.Sharpness(gray).enhance(1.5)
    return gray


def extract_text(image: Image.Image, language=None, config=None) -> str:
    language = language or OCR_LANGUAGE
    config = config or OCR_CONFIG
    return pytesseract.image_to_string(
        image,
        lang=language,
        config=config,
        timeout=OCR_TIMEOUT,
    )


def extract_confidence(image: Image.Image, language=None) -> float:
    """Average confidence of recognized non-empty words; not a validity score."""
    language = language or OCR_LANGUAGE
    data = pytesseract.image_to_data(
        image,
        lang=language,
        config="--oem 3 --psm 3",
        output_type=Output.DICT,
        timeout=OCR_TIMEOUT,
    )
    values = []
    for text, raw_conf in zip(data.get("text", []), data.get("conf", [])):
        if not str(text).strip():
            continue
        try:
            confidence = float(raw_conf)
        except (TypeError, ValueError):
            continue
        if confidence >= 0:
            values.append(confidence)
    return round(sum(values) / len(values), 2) if values else 0.0


# ============================================================
# MRZ EXTRACTION
# ============================================================

def crop_mrz(image: Image.Image) -> Image.Image:
    """Crop the lower portion of a passport image where the MRZ normally appears."""
    image = ImageOps.exif_transpose(image)
    top = int(image.height * MRZ_CROP_TOP_RATIO)
    crop = image.crop((0, top, image.width, image.height))
    if crop.width < MRZ_TARGET_WIDTH:
        scale = MRZ_TARGET_WIDTH / crop.width
        crop = crop.resize(
            (MRZ_TARGET_WIDTH, max(1, int(crop.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return crop


def make_mrz_variants(crop: Image.Image):
    """Generate several readable MRZ variants without changing character content."""
    gray = ImageOps.grayscale(crop)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(2.0)
    gray = ImageEnhance.Sharpness(gray).enhance(2.0)

    variants = [("contrast", gray)]

    # Global threshold variants. Keep multiple versions because passport scans
    # can have shadows or uneven illumination.
    for threshold in (130, 155, 180):
        binary = gray.point(lambda px, t=threshold: 255 if px > t else 0)
        variants.append((f"threshold_{threshold}", binary))

    return variants


def run_mrz_ocr(crop: Image.Image):
    results = []
    all_text = []

    for variant_name, variant in make_mrz_variants(crop):
        for psm in MRZ_PSM_MODES:
            config = (
                f"--oem 3 --psm {psm} "
                "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
            )
            try:
                text = pytesseract.image_to_string(
                    variant,
                    lang=MRZ_LANGUAGE,
                    config=config,
                    timeout=OCR_TIMEOUT,
                )
                text = text.strip()
                results.append({
                    "variant": variant_name,
                    "psm": psm,
                    "text": text,
                })
                if text:
                    all_text.append(text)
            except pytesseract.TesseractError as exc:
                logger.warning("MRZ OCR failed (%s, PSM %s): %s",
                               variant_name, psm, exc)
            except RuntimeError as exc:
                logger.warning("MRZ OCR timeout (%s, PSM %s): %s",
                               variant_name, psm, exc)

    return results, "\n".join(all_text)


def save_image(image: Image.Image, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


# ============================================================
# PAGE / FILE PROCESSING
# ============================================================

def process_page(image: Image.Image, page_label: str, output_stem: str):
    prepared = preprocess_image(image)
    page_text = extract_text(prepared)
    confidence = extract_confidence(prepared)

    mrz_image = crop_mrz(image)
    mrz_crop_path = OUTPUT_DIR / "mrz_crops" / f"{output_stem}_{page_label}_mrz.png"
    save_image(mrz_image, mrz_crop_path)

    mrz_runs, mrz_text = run_mrz_ocr(mrz_image)
    mrz_result = parse_mrz(mrz_text)

    return {
        "page": page_label,
        "ocr_text": page_text,
        "ocr_confidence": confidence,
        "mrz_crop": str(mrz_crop_path),
        "mrz_ocr_runs": mrz_runs,
        "mrz": mrz_result,
    }


def _combine_pages(pages):
    combined_text = []
    for page in pages:
        combined_text.append(f"\n========== {page['page']} ==========\n")
        combined_text.append(page["ocr_text"])
    return "\n".join(combined_text)


def save_result(input_path: Path, pages, document_type: str):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    text_path = OUTPUT_DIR / f"{input_path.stem}_ocr.txt"
    json_path = OUTPUT_DIR / f"{input_path.stem}_result.json"

    combined_text = _combine_pages(pages)
    text_path.write_text(combined_text, encoding="utf-8")

    # Select the page with the strongest MRZ result. This is not an approval
    # decision; non-valid MRZ results remain flagged for manual review.
    best_page = max(
        pages,
        key=lambda page: (
            page["mrz"].get("valid_check_digits", 0),
            page["ocr_confidence"],
        ),
    )

    result = {
        "document_type": document_type,
        "input_file": str(input_path.resolve()),
        "page_count": len(pages),
        "ocr_confidence": best_page["ocr_confidence"],
        "mrz_status": best_page["mrz"].get("status", "NEEDS_MANUAL_REVIEW"),
        "mrz": best_page["mrz"],
        "pages": pages,
        "extracted_text_file": str(text_path.resolve()),
    }

    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=4),
        encoding="utf-8",
    )

    logger.info("OCR text saved: %s", text_path)
    logger.info("JSON result saved: %s", json_path)
    return result


def process_image_full(image_path: Path):
    image_path = Path(image_path)
    try:
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source).copy()
    except UnidentifiedImageError as exc:
        raise ValueError(f"Unsupported or damaged image: {image_path}") from exc

    page = process_page(image, "page_1", image_path.stem)
    return save_result(image_path, [page], "image")


def process_pdf_full(pdf_path: Path):
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    try:
        info = pdfinfo_from_path(str(pdf_path), poppler_path=str(POPPLER_PATH))
        page_count = int(info.get("Pages", 0))
        if page_count <= 0:
            raise RuntimeError("PDF contains no readable pages.")
        if page_count > 100:
            raise RuntimeError(
                f"PDF has {page_count} pages. Limit is 100 pages per test run."
            )

        images = convert_from_path(
            str(pdf_path),
            dpi=PDF_DPI,
            poppler_path=str(POPPLER_PATH),
            fmt="png",
            thread_count=2,
        )
    except Exception as exc:
        raise RuntimeError(f"Could not convert PDF to images: {exc}") from exc

    pages = []
    for index, image in enumerate(images, start=1):
        logger.info("Processing PDF page %s/%s", index, len(images))
        pages.append(process_page(image, f"page_{index}", pdf_path.stem))

    return save_result(pdf_path, pages, "pdf")


# Backwards-compatible functions: they continue returning OCR text.
def process_image(image_path):
    result = process_image_full(Path(image_path))
    return _combine_pages(result["pages"])


def process_pdf(pdf_path):
    result = process_pdf_full(Path(pdf_path))
    return _combine_pages(result["pages"])


def test_ocr():
    initialize_ocr()
    initialize_poppler()

    if not DEFAULT_IMAGE.is_file():
        raise FileNotFoundError(
            f"Default test image not found: {DEFAULT_IMAGE}\n"
            "Pass an image or PDF path on the command line."
        )

    result = process_image_full(DEFAULT_IMAGE)
    print_summary(result)


def print_summary(result):
    print("\n========== OCR SUMMARY ==========")
    print("Input:", result["input_file"])
    print("Type:", result["document_type"])
    print("OCR confidence:", result["ocr_confidence"], "%")
    print("MRZ status:", result["mrz_status"])
    print("Text file:", result["extracted_text_file"])
    if result.get("mrz", {}).get("data"):
        print("MRZ data:")
        print(json.dumps(result["mrz"]["data"], ensure_ascii=False, indent=2))
    else:
        print("MRZ message:", result.get("mrz", {}).get("message",
                                                     "MRZ needs manual review."))
    print("=================================\n")


def main():
    parser = argparse.ArgumentParser(
        description="Passport OCR + ICAO TD1/TD2/TD3 MRZ extraction"
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to an image or PDF. Defaults to passport.jpg.",
    )
    args = parser.parse_args()

    try:
        initialize_ocr()
        initialize_poppler()

        input_path = Path(args.file).expanduser() if args.file else DEFAULT_IMAGE
        if not input_path.is_absolute():
            input_path = (Path.cwd() / input_path).resolve()

        if not input_path.is_file():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        suffix = input_path.suffix.lower()
        if suffix == ".pdf":
            result = process_pdf_full(input_path)
        elif suffix in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"):
            result = process_image_full(input_path)
        else:
            raise ValueError(
                f"Unsupported file type: {suffix}. Use an image or PDF."
            )

        print_summary(result)

    except KeyboardInterrupt:
        print("\nCancelled by user.")
        return 130
    except Exception as exc:
        logger.exception("OCR processing failed")
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
