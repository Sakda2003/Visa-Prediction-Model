
import argparse
import json
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pytesseract
from PIL import (
    Image,
    ImageOps,
    ImageEnhance,
    ImageFilter,
    UnidentifiedImageError,
)
from pytesseract import Output
from pdf2image import convert_from_path, pdfinfo_from_path


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

TESSERACT_PATH = Path(
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

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
MRZ_PSM_MODES = (6, 7, 11, 12, 13)

PDF_DPI = 250
OCR_TIMEOUT = 60
MAX_PDF_PAGES = 100

MRZ_TARGET_WIDTH = 3000
MRZ_CROP_TOP_RATIO = 0.45

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png",
    ".tif", ".tiff", ".bmp", ".webp"
}

MRZ_WEIGHTS = (7, 3, 1)

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
    global OCR_LANGUAGE

    if not TESSERACT_PATH.is_file():
        raise FileNotFoundError(
            f"Tesseract not found: {TESSERACT_PATH}"
        )

    pytesseract.pytesseract.tesseract_cmd = str(
        TESSERACT_PATH
    )

    available = set(
        pytesseract.get_languages(config="")
    )

    if "eng" not in available:
        raise RuntimeError(
            "English Tesseract language pack is required."
        )

    selected = ["eng"]

    if "khm" in available:
        selected.append("khm")
    else:
        logger.warning(
            "Khmer language pack is missing."
        )

    OCR_LANGUAGE = "+".join(selected)

    logger.info(
        "Tesseract ready. OCR language: %s",
        OCR_LANGUAGE
    )

    logger.info(
        "MRZ language: %s",
        MRZ_LANGUAGE
    )


def initialize_poppler():
    if not POPPLER_PATH.is_dir():
        raise FileNotFoundError(
            f"Poppler folder not found: {POPPLER_PATH}"
        )

    for executable in ("pdfinfo.exe", "pdftoppm.exe"):
        if not (POPPLER_PATH / executable).is_file():
            raise FileNotFoundError(
                f"Missing Poppler executable: {executable}"
            )

    logger.info(
        "Poppler ready: %s",
        POPPLER_PATH
    )


# ============================================================
# GENERAL IMAGE PREPROCESSING
# ============================================================

def preprocess_image(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")

    if image.width < 2000:
        scale = 2000 / image.width

        image = image.resize(
            (
                2000,
                max(1, int(image.height * scale))
            ),
            Image.Resampling.LANCZOS
        )

    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)

    gray = ImageEnhance.Contrast(
        gray
    ).enhance(1.5)

    gray = ImageEnhance.Sharpness(
        gray
    ).enhance(1.5)

    return gray


def extract_text(
    image: Image.Image,
    language=None,
    config=None
) -> str:

    return pytesseract.image_to_string(
        image,
        lang=language or OCR_LANGUAGE,
        config=config or OCR_CONFIG,
        timeout=OCR_TIMEOUT
    )


def extract_confidence(
    image: Image.Image,
    language=None
) -> float:

    data = pytesseract.image_to_data(
        image,
        lang=language or OCR_LANGUAGE,
        config=OCR_CONFIG,
        output_type=Output.DICT,
        timeout=OCR_TIMEOUT
    )

    values = []

    for text, raw_conf in zip(
        data.get("text", []),
        data.get("conf", [])
    ):
        if not str(text).strip():
            continue

        try:
            confidence = float(raw_conf)
        except (TypeError, ValueError):
            continue

        if confidence >= 0:
            values.append(confidence)

    if not values:
        return 0.0

    return round(
        sum(values) / len(values),
        2
    )


# ============================================================
# ICAO MRZ VALIDATION
# ============================================================

def normalize_mrz_line(text: str) -> str:
    """
    Keep only valid MRZ characters.
    Do not automatically replace O with 0 or I with 1.
    """

    text = text.upper().strip()

    return re.sub(
        r"[^A-Z0-9<]",
        "",
        text
    )


def mrz_char_value(char: str) -> int:

    if char == "<":
        return 0

    if char.isdigit():
        return int(char)

    if "A" <= char <= "Z":
        return ord(char) - ord("A") + 10

    raise ValueError(
        f"Invalid MRZ character: {char}"
    )


def calculate_check_digit(value: str) -> str:

    total = 0

    for index, char in enumerate(value):
        total += (
            mrz_char_value(char)
            * MRZ_WEIGHTS[index % 3]
        )

    return str(total % 10)


def check_digit_valid(value: str, digit: str) -> bool:

    if len(digit) != 1 or not digit.isdigit():
        return False

    try:
        return calculate_check_digit(value) == digit
    except ValueError:
        return False


def validate_td3(lines):

    if len(lines) != 2:
        return False, {}

    line1, line2 = lines

    if len(line1) != 44 or len(line2) != 44:
        return False, {}

    if not line1.startswith("P"):
        return False, {}

    checks = {
        "passport_number": check_digit_valid(
            line2[0:9], line2[9]
        ),

        "date_of_birth": check_digit_valid(
            line2[13:19], line2[19]
        ),

        "expiry_date": check_digit_valid(
            line2[21:27], line2[27]
        ),

        "optional_data": check_digit_valid(
            line2[28:42], line2[42]
        ),

        "composite": check_digit_valid(
            line2[0:10]
            + line2[13:20]
            + line2[21:43],
            line2[43]
        )
    }

    return all(checks.values()), checks


def validate_td2(lines):

    if len(lines) != 2:
        return False, {}

    line1, line2 = lines

    if len(line1) != 36 or len(line2) != 36:
        return False, {}

    if not line1.startswith("P"):
        return False, {}

    checks = {
        "passport_number": check_digit_valid(
            line2[0:9], line2[9]
        ),

        "date_of_birth": check_digit_valid(
            line2[13:19], line2[19]
        ),

        "expiry_date": check_digit_valid(
            line2[21:27], line2[27]
        )
    }

    return all(checks.values()), checks


def validate_td1(lines):

    if len(lines) != 3:
        return False, {}

    line1, line2, line3 = lines

    if any(len(line) != 30 for line in lines):
        return False, {}

    checks = {
        "document_number": check_digit_valid(
            line1[5:14], line1[14]
        ),

        "date_of_birth": check_digit_valid(
            line2[0:6], line2[6]
        ),

        "expiry_date": check_digit_valid(
            line2[8:14], line2[14]
        ),

        "composite": check_digit_valid(
            line1[5:30]
            + line2[0:7]
            + line2[8:15]
            + line2[18:29],
            line2[29]
        )
    }

    return all(checks.values()), checks


# ============================================================
# MRZ CANDIDATE DETECTION
# ============================================================

def find_mrz_candidates(text: str):

    raw_lines = []

    for line in text.splitlines():

        normalized = normalize_mrz_line(line)

        if normalized:
            raw_lines.append(normalized)

    candidates = []

    # TD3: 2 lines x 44 characters
    for index in range(len(raw_lines) - 1):

        lines = raw_lines[index:index + 2]

        if (
            len(lines[0]) == 44
            and len(lines[1]) == 44
            and lines[0].startswith("P")
        ):

            valid, checks = validate_td3(lines)

            candidates.append({
                "format": "TD3",
                "lines": lines,
                "valid": valid,
                "checks": checks
            })

    # TD2: 2 lines x 36 characters
    for index in range(len(raw_lines) - 1):

        lines = raw_lines[index:index + 2]

        if (
            len(lines[0]) == 36
            and len(lines[1]) == 36
            and lines[0].startswith("P")
        ):

            valid, checks = validate_td2(lines)

            candidates.append({
                "format": "TD2",
                "lines": lines,
                "valid": valid,
                "checks": checks
            })

    # TD1: 3 lines x 30 characters
    for index in range(len(raw_lines) - 2):

        lines = raw_lines[index:index + 3]

        if all(len(line) == 30 for line in lines):

            valid, checks = validate_td1(lines)

            candidates.append({
                "format": "TD1",
                "lines": lines,
                "valid": valid,
                "checks": checks
            })

    return candidates


# ============================================================
# MRZ FIELD EXTRACTION
# ============================================================

def parse_mrz_date(value: str, date_type: str):

    if not re.fullmatch(r"\d{6}", value):
        return None

    year = int(value[0:2])
    month = int(value[2:4])
    day = int(value[4:6])

    if date_type == "birth":
        current_year = date.today().year % 100

        century = (
            2000 if year <= current_year
            else 1900
        )
    else:
        century = (
            2000 if year <= 79
            else 1900
        )

    full_year = century + year

    try:
        parsed = date(
            full_year,
            month,
            day
        )

        return parsed.isoformat()

    except ValueError:
        return None


def clean_mrz_name(value: str):

    parts = value.split("<<", 1)

    surname = parts[0].replace("<", " ").strip()

    given_names = ""

    if len(parts) > 1:
        given_names = (
            parts[1]
            .replace("<", " ")
            .strip()
        )

    return {
        "surname": surname,
        "given_names": given_names
    }


def parse_mrz_candidate(candidate):

    lines = candidate["lines"]
    fmt = candidate["format"]

    if fmt == "TD3":

        line1, line2 = lines

        names = clean_mrz_name(
            line1[5:44]
        )

        return {
            "document_code": line1[0:2].replace("<", ""),
            "issuing_country": line1[2:5],
            **names,
            "passport_number": line2[0:9].replace("<", ""),
            "nationality": line2[10:13],
            "date_of_birth": parse_mrz_date(
                line2[13:19], "birth"
            ),
            "sex": line2[20],
            "expiry_date": parse_mrz_date(
                line2[21:27], "expiry"
            ),
            "optional_data": line2[28:42]
        }

    if fmt == "TD2":

        line1, line2 = lines

        names = clean_mrz_name(
            line1[5:36]
        )

        return {
            "document_code": line1[0:2].replace("<", ""),
            "issuing_country": line1[2:5],
            **names,
            "passport_number": line2[0:9].replace("<", ""),
            "nationality": line2[10:13],
            "date_of_birth": parse_mrz_date(
                line2[13:19], "birth"
            ),
            "sex": line2[20],
            "expiry_date": parse_mrz_date(
                line2[21:27], "expiry"
            ),
            "optional_data": line2[28:36]
        }

    if fmt == "TD1":

        line1, line2, line3 = lines

        names = clean_mrz_name(line3)

        return {
            "document_code": line1[0:2].replace("<", ""),
            "issuing_country": line1[2:5],
            **names,
            "passport_number": line1[5:14].replace("<", ""),
            "nationality": line2[15:18],
            "date_of_birth": parse_mrz_date(
                line2[0:6], "birth"
            ),
            "sex": line2[7],
            "expiry_date": parse_mrz_date(
                line2[8:14], "expiry"
            ),
            "optional_data": (
                line1[15:30] + line2[18:29]
            )
        }

    return {}


def parse_mrz(text: str):

    candidates = find_mrz_candidates(text)

    if not candidates:

        return {
            "status": "NEEDS_MANUAL_REVIEW",
            "format": None,
            "message": (
                "No complete, structurally valid "
                "TD1, TD2 or TD3 MRZ was detected."
            ),
            "data": None,
            "mrz_lines": [],
            "check_digits": {}
        }

    candidates.sort(
        key=lambda item: (
            item["valid"],
            sum(item["checks"].values())
        ),
        reverse=True
    )

    best = candidates[0]

    if best["valid"]:

        return {
            "status": "VALIDATED",
            "format": best["format"],
            "message": (
                "MRZ structure and applicable "
                "ICAO check digits passed."
            ),
            "data": parse_mrz_candidate(best),
            "mrz_lines": best["lines"],
            "check_digits": best["checks"]
        }

    return {
        "status": "NEEDS_MANUAL_REVIEW",
        "format": best["format"],
        "message": (
            "MRZ candidate detected, but one or "
            "more check digits failed."
        ),
        "data": None,
        "mrz_lines": best["lines"],
        "check_digits": best["checks"]
    }


# ============================================================
# MRZ IMAGE PREPROCESSING
# ============================================================

def crop_mrz(image: Image.Image):

    image = ImageOps.exif_transpose(image)

    top = int(
        image.height * MRZ_CROP_TOP_RATIO
    )

    crop = image.crop(
        (0, top, image.width, image.height)
    )

    if crop.width < MRZ_TARGET_WIDTH:

        scale = MRZ_TARGET_WIDTH / crop.width

        crop = crop.resize(
            (
                MRZ_TARGET_WIDTH,
                max(1, int(crop.height * scale))
            ),
            Image.Resampling.LANCZOS
        )

    return crop


def make_mrz_variants(crop: Image.Image):

    gray = ImageOps.grayscale(crop)

    gray = ImageOps.autocontrast(
        gray,
        cutoff=1
    )

    gray = ImageEnhance.Contrast(
        gray
    ).enhance(2.0)

    gray = ImageEnhance.Sharpness(
        gray
    ).enhance(2.0)

    gray = gray.filter(
        ImageFilter.UnsharpMask(
            radius=2,
            percent=150,
            threshold=3
        )
    )

    variants = [
        ("original", gray)
    ]

    for threshold in (125, 145, 165, 185):

        binary = gray.point(
            lambda px, t=threshold:
            255 if px > t else 0
        )

        variants.append(
            (f"threshold_{threshold}", binary)
        )

    return variants


def run_mrz_ocr(crop: Image.Image):

    results = []
    candidates = []

    for variant_name, variant in make_mrz_variants(crop):

        for psm in MRZ_PSM_MODES:

            config = (
                f"--oem 1 --psm {psm} "
                "-c tessedit_char_whitelist="
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
            )

            try:

                text = pytesseract.image_to_string(
                    variant,
                    lang=MRZ_LANGUAGE,
                    config=config,
                    timeout=OCR_TIMEOUT
                ).strip()

                run_candidates = find_mrz_candidates(
                    text
                )

                results.append({
                    "variant": variant_name,
                    "psm": psm,
                    "text": text,
                    "candidate_count": len(run_candidates)
                })

                for candidate in run_candidates:

                    candidate["variant"] = variant_name
                    candidate["psm"] = psm

                    candidates.append(candidate)

            except Exception as exc:

                logger.warning(
                    "MRZ OCR failed: %s / PSM %s: %s",
                    variant_name,
                    psm,
                    exc
                )

    candidates.sort(
        key=lambda item: (
            item["valid"],
            sum(item["checks"].values())
        ),
        reverse=True
    )

    valid_candidates = [
        item for item in candidates
        if item["valid"]
    ]

    if valid_candidates:
        best = valid_candidates[0]
    elif candidates:
        best = candidates[0]
    else:
        best = None

    if best:
        selected_text = "\n".join(
            best["lines"]
        )
    else:
        # Preserve one OCR run for manual review.
        selected_text = max(
            results,
            key=lambda item: len(item["text"]),
            default={"text": ""}
        )["text"]

    return results, selected_text


# ============================================================
# PAGE PROCESSING
# ============================================================

def save_image(image: Image.Image, path: Path):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    image.save(
        path,
        format="PNG"
    )


def process_page(
    image: Image.Image,
    page_label: str,
    output_stem: str
):

    logger.info(
        "Processing %s",
        page_label
    )

    prepared = preprocess_image(image)

    page_text = extract_text(prepared)

    confidence = extract_confidence(prepared)

    mrz_image = crop_mrz(image)

    mrz_crop_path = (
        OUTPUT_DIR
        / "mrz_crops"
        / f"{output_stem}_{page_label}_mrz.png"
    )

    save_image(
        mrz_image,
        mrz_crop_path
    )

    mrz_runs, mrz_text = run_mrz_ocr(
        mrz_image
    )

    mrz_result = parse_mrz(
        mrz_text
    )

    logger.info(
        "%s MRZ status: %s",
        page_label,
        mrz_result["status"]
    )

    return {
        "page": page_label,
        "ocr_text": page_text,
        "ocr_confidence": confidence,
        "mrz_crop": str(mrz_crop_path),
        "mrz_ocr_runs": mrz_runs,
        "mrz_selected_text": mrz_text,
        "mrz": mrz_result
    }


# ============================================================
# RESULT EXPORT
# ============================================================

def combine_pages(pages):

    output = []

    for page in pages:

        output.append(
            f"\n========== {page['page']} ==========\n"
        )

        output.append(
            page["ocr_text"]
        )

    return "\n".join(output)


def save_result(
    input_path: Path,
    pages,
    document_type: str
):

    if not pages:
        raise RuntimeError(
            "No pages were processed."
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    text_path = (
        OUTPUT_DIR
        / f"{input_path.stem}_ocr.txt"
    )

    json_path = (
        OUTPUT_DIR
        / f"{input_path.stem}_result.json"
    )

    combined_text = combine_pages(pages)

    text_path.write_text(
        combined_text,
        encoding="utf-8-sig"
    )

    best_page = max(
        pages,
        key=lambda page: (
            page["mrz"]["status"] == "VALIDATED",
            sum(
                page["mrz"].get(
                    "check_digits", {}
                ).values()
            ),
            page["ocr_confidence"]
        )
    )

    result = {
        "document_type": document_type,
        "input_file": str(input_path.resolve()),
        "page_count": len(pages),

        "ocr_confidence": best_page[
            "ocr_confidence"
        ],

        "mrz_status": best_page[
            "mrz"
        ]["status"],

        "mrz": best_page["mrz"],

        "pages": pages,

        "extracted_text_file": str(
            text_path.resolve()
        ),

        "generated_at": datetime.now().isoformat(
            timespec="seconds"
        ),

        "validation_note": (
            "OCR and MRZ validation do not establish "
            "passport authenticity or visa approval."
        )
    }

    json_path.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=4
        ),
        encoding="utf-8"
    )

    logger.info(
        "OCR text saved: %s",
        text_path
    )

    logger.info(
        "JSON result saved: %s",
        json_path
    )

    return result


# ============================================================
# IMAGE PROCESSING
# ============================================================

def process_image_full(image_path: Path):

    image_path = Path(image_path)

    if not image_path.is_file():
        raise FileNotFoundError(
            f"Image not found: {image_path}"
        )

    try:

        with Image.open(image_path) as source:

            image = ImageOps.exif_transpose(
                source
            ).copy()

    except (
        UnidentifiedImageError,
        OSError
    ) as exc:

        raise ValueError(
            f"Unsupported or damaged image: {image_path}"
        ) from exc

    page = process_page(
        image,
        "page_1",
        image_path.stem
    )

    return save_result(
        image_path,
        [page],
        "image"
    )


# ============================================================
# PDF PROCESSING
# ============================================================

def process_pdf_full(pdf_path: Path):

    pdf_path = Path(pdf_path)

    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)

    try:

        info = pdfinfo_from_path(
            str(pdf_path),
            poppler_path=str(POPPLER_PATH)
        )

        page_count = int(
            info.get("Pages", 0)
        )

        if page_count <= 0:
            raise RuntimeError(
                "PDF contains no readable pages."
            )

        if page_count > MAX_PDF_PAGES:
            raise RuntimeError(
                f"PDF has {page_count} pages. "
                f"Maximum is {MAX_PDF_PAGES}."
            )

        images = convert_from_path(
            str(pdf_path),
            dpi=PDF_DPI,
            poppler_path=str(POPPLER_PATH),
            fmt="png",
            thread_count=2
        )

    except Exception as exc:

        raise RuntimeError(
            f"Could not convert PDF to images: {exc}"
        ) from exc

    pages = []

    try:

        for index, image in enumerate(
            images,
            start=1
        ):

            logger.info(
                "Processing PDF page %s/%s",
                index,
                len(images)
            )

            pages.append(
                process_page(
                    image,
                    f"page_{index}",
                    pdf_path.stem
                )
            )

    finally:

        for image in images:
            image.close()

    return save_result(
        pdf_path,
        pages,
        "pdf"
    )


# ============================================================
# BACKWARDS COMPATIBILITY
# ============================================================

def process_image(image_path):

    result = process_image_full(
        Path(image_path)
    )

    return combine_pages(
        result["pages"]
    )


def process_pdf(pdf_path):

    result = process_pdf_full(
        Path(pdf_path)
    )

    return combine_pages(
        result["pages"]
    )


# ============================================================
# OUTPUT SUMMARY
# ============================================================

def print_summary(result):

    print("\n========== OCR SUMMARY ==========")

    print(
        "Input:",
        result["input_file"]
    )

    print(
        "Type:",
        result["document_type"]
    )

    print(
        "Pages:",
        result["page_count"]
    )

    print(
        "OCR confidence:",
        result["ocr_confidence"],
        "%"
    )

    print(
        "MRZ status:",
        result["mrz_status"]
    )

    print(
        "Text file:",
        result["extracted_text_file"]
    )

    mrz = result["mrz"]

    print(
        "MRZ format:",
        mrz.get("format")
    )

    print(
        "MRZ message:",
        mrz.get("message")
    )

    if mrz.get("data"):

        print("\n========== MRZ DATA ==========")

        print(
            json.dumps(
                mrz["data"],
                ensure_ascii=False,
                indent=4
            )
        )

        print(
            "\nCheck digits:",
            json.dumps(
                mrz["check_digits"],
                indent=4
            )
        )

    print(
        "=================================\n"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Passport OCR with English/Khmer, "
            "TD1/TD2/TD3 MRZ and ICAO validation."
        )
    )

    parser.add_argument(
        "file",
        nargs="?",
        help="Image or PDF file path."
    )

    args = parser.parse_args()

    try:

        initialize_ocr()

        input_path = (
            Path(args.file).expanduser()
            if args.file
            else DEFAULT_IMAGE
        )

        if not input_path.is_absolute():

            input_path = (
                Path.cwd() / input_path
            ).resolve()

        if not input_path.is_file():

            raise FileNotFoundError(
                f"Input file not found: {input_path}"
            )

        suffix = input_path.suffix.lower()

        if suffix == ".pdf":

            initialize_poppler()

            result = process_pdf_full(
                input_path
            )

        elif suffix in IMAGE_EXTENSIONS:

            result = process_image_full(
                input_path
            )

        else:

            raise ValueError(
                f"Unsupported file type: {suffix}"
            )

        print_summary(result)

        return 0

    except KeyboardInterrupt:

        print("\nCancelled by user.")

        return 130

    except Exception as exc:

        logger.exception(
            "OCR processing failed"
        )

        print(
            f"\nERROR: {exc}",
            file=sys.stderr
        )

        return 1


if __name__ == "__main__":

    raise SystemExit(main())
