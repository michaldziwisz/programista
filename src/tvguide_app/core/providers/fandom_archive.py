from __future__ import annotations

import re
from datetime import date, time
from urllib.parse import urlencode

from tvguide_app.core.http import HttpClient
from tvguide_app.core.models import ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.archive_base import ArchiveProvider
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.util import (
    POLISH_MONTHS_GENITIVE,
    clean_text,
    parse_time_hhmm,
)


FANDOM_API = "https://staratelewizja.fandom.com/pl/api.php"

MIN_ARCHIVE_YEAR = 1950
DEFAULT_SINGLE_CHANNEL_SOURCE_NAME = "TVP 1"


def date_to_fandom_page_title_candidates(d: date) -> list[str]:
    """
    Some pages use leading zeros in day numbers (e.g. "01 Stycznia 2013"),
    while others omit them ("1 Stycznia 2013"). Try both.
    """
    month_name = POLISH_MONTHS_GENITIVE[d.month]
    month_name_lower = month_name.lower()
    candidates = [
        f"{d.day:02d} {month_name} {d.year}",
        f"{d.day} {month_name} {d.year}",
        f"{d.day:02d} {month_name_lower} {d.year}",
        f"{d.day} {month_name_lower} {d.year}",
    ]
    unique: list[str] = []
    for t in candidates:
        if t not in unique:
            unique.append(t)
    return unique


class FandomArchiveProvider(ScheduleProvider, ArchiveProvider):
    def __init__(self, http: HttpClient, *, year: int) -> None:
        self._http = http
        self._year = year

    @property
    def provider_id(self) -> str:
        return "fandom-archive"

    @property
    def display_name(self) -> str:
        return "Programy archiwalne"

    def set_year(self, year: int) -> None:
        self._year = year

    @property
    def year(self) -> int:
        return self._year

    def list_years(self) -> list[int]:
        # Keep a simple, deterministic year range; not all years have pages.
        current_year = date.today().year
        return list(range(MIN_ARCHIVE_YEAR, current_year + 1))

    def list_days_in_month(
        self,
        year: int,
        month: int,
        *,
        force_refresh: bool = False,
    ) -> list[date]:
        existing: set[date] = set()
        month_days = self._iter_month_days(year, month)

        titles: list[str] = []
        for d in month_days:
            titles.extend(date_to_fandom_page_title_candidates(d))

        for page in self._query_pages_info(
            titles,
            cache_scope=f"v2:{year}:{month:02d}",
            force_refresh=force_refresh,
        ):
            if page.get("missing"):
                continue
            title = page.get("title")
            if isinstance(title, str):
                parsed = self._page_title_to_date(title)
                if parsed:
                    existing.add(parsed)

        return sorted(existing)

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        year = self._year
        categories = self._search_categories_for_year(year, force_refresh=force_refresh)
        sources: list[Source] = []
        for channel_name in sorted(categories.keys(), key=str.casefold):
            sources.append(
                Source(
                    provider_id=ProviderId(self.provider_id),
                    id=SourceId(channel_name),
                    name=channel_name,
                )
            )
        return sources

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        year = self._year
        cache_key = f"fandom:days:v2:{year}"
        cached = self._http._cache.get_json(cache_key) if not force_refresh else None  # noqa: SLF001
        if isinstance(cached, list):
            return [date.fromisoformat(x) for x in cached]

        existing: set[date] = set()
        for month in range(1, 13):
            month_days = self._iter_month_days(year, month)
            titles: list[str] = []
            for d in month_days:
                titles.extend(date_to_fandom_page_title_candidates(d))
            for page in self._query_pages_info(
                titles,
                cache_scope=f"v2:{year}:{month:02d}",
                force_refresh=force_refresh,
            ):
                if not page.get("missing"):
                    title = page.get("title")
                    if isinstance(title, str):
                        parsed = self._page_title_to_date(title)
                        if parsed:
                            existing.add(parsed)

        existing_sorted = sorted(existing)
        self._http._cache.set_json(  # noqa: SLF001
            cache_key,
            [d.isoformat() for d in existing_sorted],
            ttl_seconds=7 * 24 * 3600,
        )
        return existing_sorted

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        wikitext = self._get_day_wikitext(day, force_refresh=force_refresh)
        items_text = extract_channel_schedule_from_wikitext(wikitext, source.name)
        parsed: list[tuple[time | None, str, str | None, str]] = []
        for entry in split_schedule_entries(items_text):
            start, rest = parse_entry_start_and_rest(entry)
            if not rest:
                continue
            title, subtitle = split_title_subtitle(rest)
            parsed.append((start, title, subtitle, rest))

        items: list[ScheduleItem] = []
        for idx, (start, title, subtitle, rest) in enumerate(parsed):
            end_time = None
            if idx + 1 < len(parsed):
                next_start = parsed[idx + 1][0]
                if start and next_start:
                    end_time = next_start
            items.append(
                ScheduleItem(
                    provider_id=ProviderId(self.provider_id),
                    source=source,
                    day=day,
                    start_time=start,
                    end_time=end_time,
                    title=title,
                    subtitle=subtitle,
                    details_ref=None,
                    details_summary=rest,
                )
            )
        return items

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        title = item.title.strip()
        parts: list[str] = [title]
        if item.start_time:
            parts.insert(0, item.start_time.strftime("%H:%M"))
        if item.subtitle:
            parts.append(item.subtitle.strip())
        if item.details_summary and item.subtitle != item.details_summary:
            parts.append(item.details_summary.strip())
        return "\n".join([p for p in parts if p])

    def list_days_for_source(self, source: Source, *, force_refresh: bool = False) -> list[date]:
        year = self._year
        categories = self._search_categories_for_year(year, force_refresh=force_refresh)
        category = categories.get(source.name)
        if not category:
            return []
        titles = self._list_category_members(category, force_refresh=force_refresh)
        days: list[date] = []
        for title in titles:
            parsed = self._page_title_to_date(title)
            if parsed:
                days.append(parsed)
        days.sort()
        return days

    def list_sources_for_day(self, day: date, *, force_refresh: bool = False) -> list[Source]:
        wikitext = self._get_day_wikitext(day, force_refresh=force_refresh)
        channel_names = extract_channels_from_wikitext(wikitext)
        return [
            Source(
                provider_id=ProviderId(self.provider_id),
                id=SourceId(name),
                name=name,
            )
            for name in channel_names
        ]

    def _get_day_wikitext(self, day: date, *, force_refresh: bool) -> str:
        # Prefer titles with leading zeros (common on this wiki), but fall back
        # to a non-zero variant if needed.
        for title in date_to_fandom_page_title_candidates(day):
            wikitext = self._get_page_wikitext(title, force_refresh=force_refresh)
            if wikitext:
                return wikitext
        return ""

    def _search_categories_for_year(self, year: int, *, force_refresh: bool) -> dict[str, str]:
        cache_key = f"fandom:categories:{year}"
        cached = self._http._cache.get_json(cache_key) if not force_refresh else None  # noqa: SLF001
        if isinstance(cached, dict):
            return {str(k): str(v) for k, v in cached.items()}

        categories: dict[str, str] = {}
        sroffset = 0
        while True:
            query = {
                "action": "query",
                "format": "json",
                "list": "search",
                "srnamespace": 14,
                "srlimit": 50,
                "sroffset": sroffset,
                "srsearch": f'intitle:"Ramówki" intitle:"{year}"',
            }
            url = f"{FANDOM_API}?{urlencode(query)}"
            text = self._http.get_text(
                url,
                cache_key=f"{cache_key}:search:{sroffset}",
                ttl_seconds=7 * 24 * 3600,
                force_refresh=force_refresh,
            )
            data = json_loads(text)
            results = data.get("query", {}).get("search", [])
            for r in results:
                title = r.get("title")
                if not isinstance(title, str):
                    continue
                channel_name = parse_channel_from_category_title(title, year)
                if channel_name:
                    categories[channel_name] = title

            cont = data.get("continue", {})
            if not isinstance(cont, dict) or "sroffset" not in cont:
                break
            sroffset = int(cont["sroffset"])

        self._http._cache.set_json(  # noqa: SLF001
            cache_key,
            categories,
            ttl_seconds=30 * 24 * 3600,
        )
        return categories

    def _list_category_members(self, category_title: str, *, force_refresh: bool) -> list[str]:
        cache_key = f"fandom:catmembers:{category_title}"
        cached = self._http._cache.get_json(cache_key) if not force_refresh else None  # noqa: SLF001
        if isinstance(cached, list):
            return [str(x) for x in cached]

        titles: list[str] = []
        cmcontinue: str | None = None
        while True:
            query = {
                "action": "query",
                "format": "json",
                "list": "categorymembers",
                "cmnamespace": 0,
                "cmlimit": 500,
                "cmtitle": category_title,
            }
            if cmcontinue:
                query["cmcontinue"] = cmcontinue

            url = f"{FANDOM_API}?{urlencode(query)}"
            text = self._http.get_text(
                url,
                cache_key=f"{cache_key}:{cmcontinue or 'start'}",
                ttl_seconds=30 * 24 * 3600,
                force_refresh=force_refresh,
            )
            data = json_loads(text)
            for cm in data.get("query", {}).get("categorymembers", []):
                t = cm.get("title")
                if isinstance(t, str):
                    titles.append(t)

            cont = data.get("continue", {})
            if not isinstance(cont, dict) or "cmcontinue" not in cont:
                break
            cmcontinue = str(cont["cmcontinue"])

        self._http._cache.set_json(  # noqa: SLF001
            cache_key,
            titles,
            ttl_seconds=30 * 24 * 3600,
        )
        return titles

    def _get_page_wikitext(self, title: str, *, force_refresh: bool) -> str:
        cache_key = f"fandom:wikitext:{title}"
        cached = self._http._cache.get_text(cache_key) if not force_refresh else None  # noqa: SLF001
        if cached is not None:
            return cached

        query = {
            "action": "query",
            "format": "json",
            "redirects": 1,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "formatversion": 2,
            "titles": title,
        }
        url = f"{FANDOM_API}?{urlencode(query)}"
        text = self._http.get_text(
            url,
            cache_key=f"{cache_key}:json",
            ttl_seconds=30 * 24 * 3600,
            force_refresh=force_refresh,
        )
        data = json_loads(text)
        pages = data.get("query", {}).get("pages", [])
        if not pages:
            return ""
        revs = pages[0].get("revisions", [])
        if not revs:
            return ""
        content = revs[0].get("slots", {}).get("main", {}).get("content")
        if not isinstance(content, str):
            return ""
        self._http._cache.set_text(cache_key, content, ttl_seconds=30 * 24 * 3600)  # noqa: SLF001
        return content

    def _query_pages_info(
        self,
        titles: list[str],
        *,
        cache_scope: str,
        force_refresh: bool,
    ) -> list[dict]:
        pages: list[dict] = []
        batch_size = 50
        for i in range(0, len(titles), batch_size):
            batch = titles[i : i + batch_size]
            query = {
                "action": "query",
                "format": "json",
                "prop": "info",
                "formatversion": 2,
                "titles": "|".join(batch),
            }
            url = f"{FANDOM_API}?{urlencode(query)}"
            text = self._http.get_text(
                url,
                cache_key=f"fandom:pageinfo:{cache_scope}:{i}",
                ttl_seconds=7 * 24 * 3600,
                force_refresh=force_refresh,
            )
            data = json_loads(text)
            pages.extend(data.get("query", {}).get("pages", []))
        return pages

    @staticmethod
    def _iter_month_days(year: int, month: int) -> list[date]:
        first = date(year, month, 1)
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        days = (next_month - first).days
        return [date(year, month, d) for d in range(1, days + 1)]

    @staticmethod
    def _date_to_page_title(d: date) -> str:
        month_name = POLISH_MONTHS_GENITIVE[d.month]
        return f"{d.day} {month_name} {d.year}"

    @staticmethod
    def _page_title_to_date(title: str) -> date | None:
        # Example: "27 Lutego 2013"
        m = re.match(r"^\s*(\d{1,2})\s+([^\d]+?)\s+(\d{4})\s*$", title)
        if not m:
            return None
        day_s, month_s, year_s = m.group(1), m.group(2), m.group(3)
        try:
            day_i = int(day_s)
            year_i = int(year_s)
        except ValueError:
            return None

        month_s_norm = month_s.strip()
        month_num = None
        for k, v in POLISH_MONTHS_GENITIVE.items():
            if v.casefold() == month_s_norm.casefold():
                month_num = k
                break
        if not month_num:
            return None
        try:
            return date(year_i, month_num, day_i)
        except ValueError:
            return None


def parse_channel_from_category_title(category_title: str, year: int) -> str | None:
    # "Kategoria:Ramówki TVP 1 HD z 2013 roku"
    prefix = "Kategoria:Ramówki "
    suffix = f" z {year} roku"
    if not category_title.startswith(prefix) or not category_title.endswith(suffix):
        return None
    channel = category_title[len(prefix) : -len(suffix)].strip()
    return channel or None


def json_loads(text: str) -> dict:
    import json

    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Invalid JSON response (expected object).")
    return data


def strip_wiki_markup(text: str) -> str:
    if not text:
        return ""

    # Remove file/image links.
    text = re.sub(r"\[\[(Plik|File):[^\]]+\]\]", "", text, flags=re.IGNORECASE)
    # Replace [[Page|Text]] -> Text, [[Page]] -> Page
    text = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    # Remove templates {{...}}
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    # Remove bold/italic markup.
    text = text.replace("'''", "").replace("''", "")
    # Remove HTML tags.
    text = re.sub(r"<[^>]+>", "", text)
    return clean_text(text)


def is_default_single_channel_name(name: str) -> bool:
    if not name:
        return False
    compact = re.sub(r"\s+", "", name.casefold())
    return compact == "tvp1"


def extract_time_lines_from_wikitext(wikitext: str) -> list[str]:
    if not wikitext:
        return []

    normalized = re.sub(r"<br\s*/?>", "\n", wikitext, flags=re.IGNORECASE)
    time_start_re = re.compile(r"^\s*\d{1,2}(?:[:.]|\s)\d{2}\b")

    lines: list[str] = []
    for raw_line in normalized.splitlines():
        line = clean_text(strip_wiki_markup(raw_line))
        if not line:
            continue
        if not time_start_re.match(line):
            continue
        lines.append(line)
    return lines


def extract_channels_from_category_links(wikitext: str) -> list[str]:
    if not wikitext:
        return []

    # Example: [[Kategoria:Ramówki TVP 1 z 1997 roku]]
    cat_re = re.compile(
        r"\[\[(?:Kategoria|Category):Ramówki\s+(.+?)\s+z\s+(\d{4})\s+roku(?:\|[^\]]*)?\]\]",
        re.IGNORECASE,
    )
    seen: set[str] = set()
    channels: list[str] = []
    for m in cat_re.finditer(wikitext):
        channel = strip_wiki_markup(m.group(1))
        channel_norm = channel.casefold().strip()
        if not channel_norm or channel_norm in seen:
            continue
        seen.add(channel_norm)
        channels.append(channel)
    return channels


def split_wikitext_file_blocks(wikitext: str) -> list[str]:
    """
    Older pages (e.g. 1997) often contain blocks separated by channel logo files:

      [[File:...]]
      <br />07.00 ...
      [[File:...]]
      <br />...

    This returns the per-channel raw blocks (without the file marker lines).
    """
    if not wikitext:
        return []

    # Match a File/Plik link at the start of a line and capture the remainder.
    file_start_re = re.compile(r"^\s*\[\[(?:Plik|File):[^\]]+\]\]\s*(?P<rest>.*)$", re.IGNORECASE)
    time_hint_re = re.compile(r"\b\d{1,2}[:.]\d{2}\b")

    blocks: list[str] = []
    current: list[str] = []
    started = False

    for line in wikitext.splitlines():
        stripped = line.strip()
        if stripped.startswith("[[Kategoria:") or stripped.startswith("[[Category:"):
            break

        m = file_start_re.match(stripped)
        if m:
            if started:
                block = "\n".join(current).strip()
                if block and time_hint_re.search(block):
                    blocks.append(block)
            current = []
            started = True
            rest = m.group("rest").strip()
            if rest:
                current.append(rest)
            continue

        if not started:
            continue
        current.append(line)

    if started:
        block = "\n".join(current).strip()
        if block and time_hint_re.search(block):
            blocks.append(block)

    return blocks


def split_wikitext_plain_channel_sections(wikitext: str) -> list[tuple[str, str]]:
    """
    Some pages use a plain-text format without headings/categories, e.g.:

      TVP 1<br />07.00 ...<br />...
      (blank line)
      TVP 2<br />...

    This returns (channel_name, raw_block) pairs.
    """
    if not wikitext:
        return []

    # Normalize <br> into newlines so both variants work:
    # - "TVP 1<br />07.00 ..."<br />
    # - "TVP 1" on its own line, programmes below.
    normalized = re.sub(r"<br\s*/?>", "\n", wikitext, flags=re.IGNORECASE)

    # Accept "18.10", "18:10" and also editorially broken "18 10".
    time_start_re = re.compile(r"^\s*\d{1,2}(?:[:.]|\s)\d{2}\b")
    date_dot_re = re.compile(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b")
    weekday_re = re.compile(
        r"\b(poniedzia[łl]ek|wtorek|środa|sroda|czwartek|piątek|piatek|sobota|niedziela)\b",
        re.IGNORECASE,
    )

    raw_lines = normalized.splitlines()
    clean_lines = [clean_text(strip_wiki_markup(x)) for x in raw_lines]

    pairs: list[tuple[str, str]] = []
    current_channel: str | None = None
    current_lines: list[str] = []

    def is_header_line(text: str) -> bool:
        if not text:
            return True
        if date_dot_re.search(text):
            return True
        if weekday_re.search(text):
            return True
        return False

    def looks_like_channel_start(index: int, line_text: str) -> bool:
        if not line_text or time_start_re.match(line_text) or is_header_line(line_text):
            return False
        if len(line_text) > 50:
            return False
        if any(q in line_text for q in ('"', "„", "”", "«", "»")):
            return False
        if any(sep in line_text for sep in (" - ", " – ", " — ")):
            return False
        if len(line_text.split()) > 5:
            return False
        # A channel label should be followed by a time-starting line (ignoring blanks).
        for j in range(index + 1, len(clean_lines)):
            nxt = clean_lines[j]
            if not nxt:
                continue
            if nxt.startswith("[[Kategoria:") or nxt.startswith("[[Category:"):
                return False
            return bool(time_start_re.match(nxt))
        return False

    for i, line_text in enumerate(clean_lines):
        raw = raw_lines[i]
        stripped_raw = raw.strip()
        if stripped_raw.startswith("[[Kategoria:") or stripped_raw.startswith("[[Category:"):
            break

        if not line_text:
            continue

        if time_start_re.match(line_text):
            if current_channel is not None:
                current_lines.append(line_text)
            continue

        if not looks_like_channel_start(i, line_text):
            continue

        if current_channel is not None:
            block = "\n".join(current_lines).strip()
            if block:
                pairs.append((current_channel, block))
        current_channel = line_text
        current_lines = []

    if current_channel is not None:
        block = "\n".join(current_lines).strip()
        if block:
            pairs.append((current_channel, block))

    return pairs


def extract_channel_schedule_from_wikitext(wikitext: str, channel_name: str) -> str:
    """
    Returns raw schedule text block for a channel from a day page wikitext.
    """
    if not wikitext:
        return ""

    channel_norm = channel_name.casefold().strip()

    heading_re = re.compile(r"^(?P<eq>={3,6})\s*(?P<title>.*?)\s*(?P=eq)\s*$")

    collecting = False
    collected: list[str] = []

    for line in wikitext.splitlines():
        m = heading_re.match(line.strip())
        if m:
            heading_title = strip_wiki_markup(m.group("title"))
            heading_title_norm = heading_title.casefold().strip()

            # Ignore headings that look like file markers or are empty.
            if not heading_title_norm or any(
                x in heading_title_norm for x in ("plik:", "file:", ".png", ".jpg", ".svg")
            ):
                continue

            current_channel = heading_title
            collecting = heading_title_norm == channel_norm
            continue

        if collecting:
            collected.append(line)

    block = "\n".join(collected).strip()
    if block:
        return block

    # Fallback for older page formats without headings.
    channels = extract_channels_from_category_links(wikitext)
    blocks = split_wikitext_file_blocks(wikitext)
    if not channels or not blocks:
        # Another fallback for plain-text sections (no headings/categories).
        pairs = split_wikitext_plain_channel_sections(wikitext)
        for ch, b in pairs:
            if ch.casefold().strip() == channel_norm:
                return b

        # If the page contains schedule entries but no channel labels, assume
        # a single default channel ("TVP 1") for historical schedules.
        if is_default_single_channel_name(channel_name):
            time_lines = extract_time_lines_from_wikitext(wikitext)
            if time_lines:
                return "\n".join(time_lines)
        return ""

    idx = next((i for i, c in enumerate(channels) if c.casefold().strip() == channel_norm), None)
    if idx is None:
        return ""
    if idx >= len(blocks):
        return ""
    return blocks[idx]


def extract_channels_from_wikitext(wikitext: str) -> list[str]:
    if not wikitext:
        return []

    heading_re = re.compile(r"^(?P<eq>={3,6})\s*(?P<title>.*?)\s*(?P=eq)\s*$")

    seen: set[str] = set()
    channels: list[str] = []
    for line in wikitext.splitlines():
        m = heading_re.match(line.strip())
        if not m:
            continue
        heading_title = strip_wiki_markup(m.group("title"))
        heading_title_norm = heading_title.casefold().strip()
        if not heading_title_norm:
            continue
        if any(x in heading_title_norm for x in ("plik:", "file:", ".png", ".jpg", ".svg")):
            continue
        if heading_title_norm in seen:
            continue
        seen.add(heading_title_norm)
        channels.append(heading_title)
    if channels:
        return channels

    cat_channels = extract_channels_from_category_links(wikitext)
    if cat_channels:
        return cat_channels

    pairs = split_wikitext_plain_channel_sections(wikitext)
    if not pairs:
        time_lines = extract_time_lines_from_wikitext(wikitext)
        if time_lines:
            return [DEFAULT_SINGLE_CHANNEL_SOURCE_NAME]
        return []

    seen: set[str] = set()
    result: list[str] = []
    for ch, _block in pairs:
        norm = ch.casefold().strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(ch)
    return result


def split_schedule_entries(channel_block: str) -> list[str]:
    if not channel_block:
        return []
    # Many pages use <br /> inside one line; some use newlines.
    normalized = re.sub(r"<br\s*/?>", "\n", channel_block, flags=re.IGNORECASE)
    entries: list[str] = []
    for raw_line in normalized.splitlines():
        line = clean_text(strip_wiki_markup(raw_line))
        if not line:
            continue
        line_norm = line.casefold()
        if line_norm.startswith("kategoria:") or line_norm.startswith("category:"):
            continue
        if line_norm.startswith("plik:") or line_norm.startswith("file:"):
            continue
        entries.append(line)
    return entries


def parse_entry_start_and_rest(entry: str) -> tuple[time | None, str]:
    m = re.match(
        r"^\s*(\d{1,2})\s*(?:[:.]|\s)\s*(\d{2})(?:\s*[-–]\s*(\d{1,2})\s*(?:[:.]|\s)\s*(\d{2}))?\s*(?:[-–]\s*)?(.*)$",
        entry,
    )
    if not m:
        return None, clean_text(entry)
    hh, mm, rest = m.group(1), m.group(2), m.group(5)
    t = parse_time_hhmm(f"{hh}:{mm}")
    return t, clean_text(rest)


def split_title_subtitle(rest: str) -> tuple[str, str | None]:
    # Heuristic: split at first " - " or ";".
    for sep in (" - ", ";"):
        if sep in rest:
            title, tail = rest.split(sep, 1)
            title = clean_text(title)
            tail = clean_text(tail)
            return (title or rest, tail or None)
    return clean_text(rest), None
