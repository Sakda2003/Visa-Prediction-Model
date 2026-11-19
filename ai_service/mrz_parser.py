import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WEIGHTS = (7, 3, 1)
ALLOWED_STARTS = ("P<", "PN", "I<", "A<", "C<")
_CAMBODIA_TIMEZONE = ZoneInfo("Asia/Phnom_Penh")


def clean_mrz_line(line: str) -> str:
    """Normalize whitespace and remove characters not allowed in an ICAO MRZ."""
    return re.sub(r"[^A-Z0-9<]", "", line.upper())


def is_td3_first_line(line: str) -> bool:
    """Accept ICAO passport variants such as P< and PN without guessing data."""
    return (
        len(line) == 44
        and line.startswith("P")
        and line[2:5].isalpha()
    )


def mrz_value(character: str) -> int:
    if character == "<":
        return 0
    if character.isdigit():
        return int(character)
    if "A" <= character <= "Z":
        return ord(character) - ord("A") + 10
    raise ValueError(f"Invalid MRZ character: {character!r}")


def calculate_check_digit(value: str) -> str:
    total = sum(mrz_value(ch) * WEIGHTS[i % 3] for i, ch in enumerate(value))
    return str(total % 10)


def validate_check_digit(value: str, digit: str) -> bool:
    return len(digit) == 1 and digit.isdigit() and calculate_check_digit(value) == digit


def _date_candidates(yy: int):
    return (1900 + yy, 2000 + yy, 2100 + yy)


def parse_mrz_date(value: str, date_type: str, today: date | None = None):
    """Resolve YYMMDD conservatively; return ISO date or None."""
    if not re.fullmatch(r"\d{6}", value):
        return None

    yy, mm, dd = int(value[:2]), int(value[2:4]), int(value[4:6])
    today = today or datetime.now(_CAMBODIA_TIMEZONE).date()
    valid = []

    for year in _date_candidates(yy):
        try:
            candidate = date(year, mm, dd)
        except ValueError:
            continue

        if date_type == "birth":
            age = (today - candidate).days / 365.2425
            if 0 <= age <= 120:
                valid.append(candidate)
        elif date_type == "expiry":
            # Accept recently expired and reasonably future-dated documents.
            if today.year - 20 <= year <= today.year + 20:
                valid.append(candidate)

    if not valid:
        return None

    if date_type == "birth":
        chosen = max(valid)  # Prefer the most recent plausible birth year.
    else:
        chosen = min(valid, key=lambda d: abs((d - today).days))
    return chosen.isoformat()


def normalize_name(value: str) -> str:
    # A run of three or more fillers is the end of the name zone. OCR often
    # converts the remaining fillers to K/E/6; never expose that tail as a
    # person's name.
    value = re.split(r"<{3,}", value, maxsplit=1)[0]
    parts = [part for part in value.replace("<", " ").split() if part]
    parts = [part for part in parts if not re.fullmatch(r"[KE6]{3,}", part)]
    return " ".join(parts)


def _optional_check(value: str, digit: str):
    # ICAO permits an unused optional-data check digit to be represented by "<".
    if value and set(value) == {"<"} and digit == "<":
        return None
    return validate_check_digit(value, digit)


def _common_result(fmt, document_code, issuing_country, surname, given_names,
                   document_number, nationality, dob_raw, sex, expiry_raw, checks):
    return {
        "format": fmt,
        "document_code": document_code.replace("<", ""),
        "issuing_country": issuing_country,
        "surname": surname,
        "given_names": given_names,
        "passport_number": document_number.replace("<", ""),
        "nationality": nationality,
        "date_of_birth": parse_mrz_date(dob_raw, "birth"),
        "sex": sex if sex in ("M", "F", "<") else None,
        "expiry_date": parse_mrz_date(expiry_raw, "expiry"),
        "check_digits": checks,
    }


def parse_td3(lines):
    """ICAO TD3: two lines of 44 characters."""
    line1, line2 = lines
    if len(line1) != 44 or len(line2) != 44 or not is_td3_first_line(line1):
        raise ValueError("Invalid TD3 line length or document-code prefix.")

    name_parts = line1[5:].split("<<", 1)
    if len(name_parts) != 2:
        raise ValueError("TD3 name separator '<<' was not detected.")

    passport_number = line2[0:9]
    dob_raw = line2[13:19]
    expiry_raw = line2[21:27]
    optional_check = _optional_check(line2[28:42], line2[42])

    checks = {
        "passport_number": validate_check_digit(passport_number, line2[9]),
        "date_of_birth": validate_check_digit(dob_raw, line2[19]),
        "expiry_date": validate_check_digit(expiry_raw, line2[27]),
        "optional_data": optional_check,
        "composite": validate_check_digit(
            line2[0:10] + line2[13:20] + line2[21:43], line2[43]
        ),
    }

    return _common_result(
        "TD3", line1[0:2], line1[2:5],
        normalize_name(name_parts[0]), normalize_name(name_parts[1]),
        passport_number, line2[10:13], dob_raw, line2[20], expiry_raw, checks
    )


def parse_td3_line2(line2: str):
    """Parse the TD3 data line when the name line is unreadable.

    This is deliberately marked for manual review. It exposes only values that
    are independently present and checkable in line 2; it never invents a name.
    """
    if len(line2) != 44:
        raise ValueError("Invalid TD3 data-line length.")
    passport_number = line2[0:9]
    dob_raw = line2[13:19]
    expiry_raw = line2[21:27]
    optional_check = _optional_check(line2[28:42], line2[42])
    checks = {
        "passport_number": validate_check_digit(passport_number, line2[9]),
        "date_of_birth": validate_check_digit(dob_raw, line2[19]),
        "expiry_date": validate_check_digit(expiry_raw, line2[27]),
        "optional_data": optional_check,
        "composite": validate_check_digit(
            line2[0:10] + line2[13:20] + line2[21:43], line2[43]
        ),
    }
    return _common_result(
        "TD3", "PN", line2[10:13], "", "", passport_number,
        line2[10:13], dob_raw, line2[20], expiry_raw, checks
    )


def parse_td2(lines):
    """ICAO TD2: two lines of 36 characters. TD2 has no composite check digit."""
    line1, line2 = lines
    if len(line1) != 36 or len(line2) != 36:
        raise ValueError("Invalid TD2 line length.")

    name_parts = line1[5:].split("<<", 1)
    if len(name_parts) != 2:
        raise ValueError("TD2 name separator '<<' was not detected.")

    passport_number = line2[0:9]
    dob_raw = line2[13:19]
    expiry_raw = line2[21:27]
    checks = {
        "passport_number": validate_check_digit(passport_number, line2[9]),
        "date_of_birth": validate_check_digit(dob_raw, line2[19]),
        "expiry_date": validate_check_digit(expiry_raw, line2[27]),
    }

    return _common_result(
        "TD2", line1[0:2], line1[2:5],
        normalize_name(name_parts[0]), normalize_name(name_parts[1]),
        passport_number, line2[10:13], dob_raw, line2[20], expiry_raw, checks
    )


def parse_td1(lines):
    """ICAO TD1: three lines of 30 characters."""
    line1, line2, line3 = lines
    if any(len(line) != 30 for line in lines):
        raise ValueError("Invalid TD1 line length.")

    document_number = line1[5:14]
    dob_raw = line2[0:6]
    expiry_raw = line2[8:14]
    checks = {
        "document_number": validate_check_digit(document_number, line1[14]),
        "date_of_birth": validate_check_digit(dob_raw, line2[6]),
        "expiry_date": validate_check_digit(expiry_raw, line2[14]),
        "composite": validate_check_digit(
            line1[5:30] + line2[0:7] + line2[8:15], line2[29]
        ),
    }

    name_parts = line3.split("<<", 1)
    if len(name_parts) != 2:
        raise ValueError("TD1 name separator '<<' was not detected.")

    return _common_result(
        "TD1", line1[0:2], line1[2:5],
        normalize_name(name_parts[0]), normalize_name(name_parts[1]),
        document_number, line2[15:18], dob_raw, line2[7], expiry_raw, checks
    )


def _line_candidates(text: str):
    """Find exact-length MRZ candidates without silently padding missing characters."""
    found = []
    for raw in text.splitlines():
        line = clean_mrz_line(raw)
        if len(line) in (30, 36, 44):
            found.append(line)
            continue

        # OCR sometimes prefixes a line with a stray label. Only trim when a valid
        # ICAO document prefix is present at the beginning of the cleaned line.
        for length in (44, 36, 30):
            if len(line) > length and line.startswith(ALLOWED_STARTS):
                found.append(line[:length])
                break
    return found


def _candidate_score(fmt, lines):
    try:
        parser = {"TD1": parse_td1, "TD2": parse_td2, "TD3": parse_td3}[fmt]
        parsed = parser(lines)
        checks = [v for v in parsed["check_digits"].values() if v is not None]
        score = sum(checks) * 10
        score += 5 if parsed["issuing_country"].isalpha() else 0
        score += 5 if parsed["nationality"].isalpha() else 0
        score += 5 if parsed["date_of_birth"] else 0
        score += 5 if parsed["expiry_date"] else 0
        return score, parsed
    except (ValueError, IndexError):
        return -1, None


def find_mrz_lines(text: str):
    candidates = _line_candidates(text)
    possible = []

    # TD3 and TD2 pairs.
    for i in range(len(candidates) - 1):
        first, second = candidates[i], candidates[i + 1]
        if len(first) == len(second) == 44 and is_td3_first_line(first):
            score, _ = _candidate_score("TD3", [first, second])
            possible.append((score, "TD3", [first, second]))
        if len(first) == len(second) == 36:
            score, _ = _candidate_score("TD2", [first, second])
            possible.append((score, "TD2", [first, second]))

    # TD1 triplets.
    for i in range(len(candidates) - 2):
        group = candidates[i:i + 3]
        if all(len(line) == 30 for line in group) and group[0].startswith(ALLOWED_STARTS):
            score, _ = _candidate_score("TD1", group)
            possible.append((score, "TD1", group))

    if not possible:
        # A camera capture can lose the name line while preserving the TD3
        # data line. Keep that evidence for field extraction, but callers will
        # receive NEEDS_MANUAL_REVIEW because the name line is unavailable.
        for candidate in candidates:
            if (
                len(candidate) == 44
                and candidate[10:13].isalpha()
                and candidate[13:19].isdigit()
                and candidate[20] in "MF<"
                and candidate[21:27].isdigit()
            ):
                return "TD3", [candidate]
        return None, []

    _, fmt, lines = max(possible, key=lambda item: item[0])
    return fmt, lines


def _correct_mrz_digits(value: str) -> str:
    return value.translate(str.maketrans({
        "O": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "L": "1",
        "Z": "2",
        "S": "5",
        "B": "8",
    }))


def _parse_cambodian_national_id(text: str):
    rows = [
        (raw.strip(), clean_mrz_line(raw))
        for raw in str(text or "").splitlines()
        if raw.strip()
    ]
    number_row = None
    number_match = None
    number_text = ""
    for raw, line in rows:
        prefix = re.search(r"(?:[I1]D|I<)KHM", line)
        if not prefix:
            continue
        sequence = re.match(r"[0-9OQILSZB]{10,}", line[prefix.end():])
        if sequence:
            number_row = line
            number_match = sequence
            number_text = sequence.group(0)
            break

    if not number_row:
        return None

    date_row = None
    date_match = None
    date_pattern = re.compile(
        r"([0-9OQILSZB]{6})[0-9OQILSZB<]"
        r"([MF])"
        r"([0-9OQILSZB]{6})[0-9OQILSZB<]"
        r"(KHM)"
    )
    for raw, line in rows:
        match = date_pattern.search(line)
        if match:
            date_row = line
            date_match = match
            break

    data: dict[str, str | None] = {}
    field_lines: dict[str, str] = {}
    if number_row and number_match:
        digits = _correct_mrz_digits(number_text)
        national_id_number = digits[:9]
        if national_id_number.isdigit():
            data["national_id_number"] = national_id_number
            data["nationality"] = "KHM"
            field_lines["national_id_number"] = number_row
            field_lines["nationality"] = number_row

    if date_row and date_match:
        birth = parse_mrz_date(
            _correct_mrz_digits(date_match.group(1)),
            "birth",
        )
        expiry = parse_mrz_date(
            _correct_mrz_digits(date_match.group(3)),
            "expiry",
        )
        data.update(
            {
                "date_of_birth": birth,
                "sex": date_match.group(2),
                "expiry_date": expiry,
                "nationality": "KHM",
            }
        )
        field_lines.update(
            {
                "date_of_birth": date_row,
                "sex": date_row,
                "expiry_date": date_row,
                "nationality": date_row,
            }
        )

    for _raw, line in rows:
        if line.count("<") < 3:
            continue
        name_match = re.match(
            r"^([A-Z0-9]{2,})<+([A-Z0-9]{2,})(?:<+.*)?$",
            line,
        )
        if not name_match:
            continue
        surname = name_match.group(1).translate(str.maketrans({"0": "O", "1": "I"}))
        given_names = name_match.group(2).translate(
            str.maketrans({"0": "O", "1": "I"})
        )
        data["surname"] = surname
        data["given_names"] = given_names
        data["full_name"] = f"{surname} {given_names}"
        field_lines["full_name"] = line
        break

    if not data:
        return None

    return {
        "status": "NEEDS_MANUAL_REVIEW",
        "format": "KH_NATIONAL_ID",
        "data": data,
        "mrz_lines": list(dict.fromkeys(
            line for line in (number_row, date_row, field_lines.get("full_name")) if line
        )),
        "field_lines": field_lines,
        "message": (
            "Cambodian identity-card MRZ fields were parsed; its custom layout "
            "and document authenticity were not independently validated."
        ),
    }


def parse_mrz(text: str):
    cambodian_national_id = _parse_cambodian_national_id(text)
    if cambodian_national_id:
        return cambodian_national_id

    fmt, lines = find_mrz_lines(text)
    if not lines:
        return {
            "status": "NEEDS_MANUAL_REVIEW",
            "format": None,
            "message": "No complete, structurally valid TD1, TD2 or TD3 MRZ was detected.",
            "data": None,
            "mrz_lines": [],
        }

    if fmt == "TD3" and len(lines) == 1:
        try:
            data = parse_td3_line2(lines[0])
            checks = data["check_digits"]
            mandatory = [value for key, value in checks.items() if key != "optional_data"]
            valid_count = sum(mandatory)
            return {
                "status": "NEEDS_MANUAL_REVIEW",
                "format": fmt,
                "valid_check_digits": valid_count,
                "total_check_digits": len(mandatory),
                "data": data,
                "mrz_lines": lines,
                "message": "TD3 data line parsed, but the MRZ name line was unavailable.",
            }
        except (ValueError, IndexError) as error:
            return {
                "status": "NEEDS_MANUAL_REVIEW",
                "format": fmt,
                "message": str(error),
                "data": None,
                "mrz_lines": lines,
            }

    parser = {"TD1": parse_td1, "TD2": parse_td2, "TD3": parse_td3}[fmt]
    try:
        data = parser(lines)
        checks = data["check_digits"]
        mandatory = [value for key, value in checks.items()
                     if key != "optional_data" and value is not None]
        valid_count = sum(mandatory)
        total_count = len(mandatory)
        dates_valid = bool(data["date_of_birth"] and data["expiry_date"])
        all_valid = bool(mandatory) and all(mandatory) and dates_valid

        data["validation"] = {
            "check_digits_valid": all_valid and all(mandatory),
            "dates_valid": dates_valid,
            "manual_review_required": not all_valid,
        }

        return {
            "status": "VALID" if all_valid else "NEEDS_MANUAL_REVIEW",
            "format": fmt,
            "valid_check_digits": valid_count,
            "total_check_digits": total_count,
            "data": data,
            "mrz_lines": lines,
        }
    except (ValueError, IndexError) as error:
        return {
            "status": "NEEDS_MANUAL_REVIEW",
            "format": fmt,
            "message": str(error),
            "data": None,
            "mrz_lines": lines,
        }


def save_mrz_json(result, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=4),
        encoding="utf-8"
    )
