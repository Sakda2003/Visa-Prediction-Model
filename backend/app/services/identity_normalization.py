from __future__ import annotations

import re
import unicodedata
from typing import Any


def name_tokens(value: Any) -> set[str]:
    """Normalize name tokens across case, order, punctuation, and diacritic variants."""
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_marks = "".join(
        character for character in normalized
        if not unicodedata.combining(character)
    )
    return set(re.findall(r"[^\W_]+", without_marks, flags=re.UNICODE))
