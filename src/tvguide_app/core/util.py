from __future__ import annotations

import html
import re
from datetime import time


POLISH_MONTHS_GENITIVE: dict[int, str] = {
    1: "Stycznia",
    2: "Lutego",
    3: "Marca",
    4: "Kwietnia",
    5: "Maja",
    6: "Czerwca",
    7: "Lipca",
    8: "Sierpnia",
    9: "Września",
    10: "Października",
    11: "Listopada",
    12: "Grudnia",
}

POLISH_MONTHS_NOMINATIVE: dict[int, str] = {
    1: "Styczeń",
    2: "Luty",
    3: "Marzec",
    4: "Kwiecień",
    5: "Maj",
    6: "Czerwiec",
    7: "Lipiec",
    8: "Sierpień",
    9: "Wrzesień",
    10: "Październik",
    11: "Listopad",
    12: "Grudzień",
}


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_multiline_text(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def parse_time_hhmm(text: str) -> time | None:
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", text)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return time(hour=hh, minute=mm)


def title_case_first(text: str) -> str:
    if not text:
        return ""
    return text[0].upper() + text[1:]
