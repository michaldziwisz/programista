from __future__ import annotations

import re
from datetime import date, time, timedelta
import unicodedata
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from tvguide_app.core.http import HttpClient
from tvguide_app.core.models import NowTvItem, ProviderId, ScheduleItem, Source, SourceId, TvCategory
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.util import POLISH_MONTHS_GENITIVE, clean_text, parse_time_hhmm


TELEMAN_BASE = "https://www.teleman.pl"
_TELEMAN_NOW_PARSE_ERROR = (
    "Nie udało się odczytać sekcji „Teraz w TV” z serwisu Teleman.pl. "
    "Układ strony mógł się zmienić."
)
_TELEMAN_CATEGORIES_PARSE_ERROR = (
    "Nie udało się odczytać kategorii stacji z serwisu Teleman.pl. "
    "Układ strony mógł się zmienić."
)
_TELEMAN_SPORT_PARSE_ERROR = (
    "Nie udało się odczytać listy transmisji sportowych z serwisu Teleman.pl. "
    "Układ strony mógł się zmienić."
)
_TIME_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,2}[:.]\d{2})(?!\d)")
_MAX_SPORT_PAGES = 25
_POLISH_MONTH_BY_NAME = {name.casefold(): number for number, name in POLISH_MONTHS_GENITIVE.items()}


class TelemanNowScheduleParseError(ValueError):
    pass


class TelemanCategoryParseError(ValueError):
    pass


class TelemanSportScheduleParseError(ValueError):
    pass


class TelemanProvider(ScheduleProvider):
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @property
    def provider_id(self) -> str:
        return "teleman"

    @property
    def display_name(self) -> str:
        return "Telewizja"

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        html = self._http.get_text(
            TELEMAN_BASE + "/",
            cache_key="teleman:home",
            ttl_seconds=7 * 24 * 3600,
            force_refresh=force_refresh,
        )
        stations = parse_teleman_stations(html)
        return [
            Source(provider_id=ProviderId(self.provider_id), id=SourceId(slug), name=name)
            for slug, name in stations
        ]

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        # Teleman offers 14 days; we expose "today .. today+13"
        today = date.today()
        return [today.fromordinal(today.toordinal() + i) for i in range(14)]

    def list_tv_categories(self, *, force_refresh: bool = False) -> list[TvCategory]:
        html = self._get_teleman_text(
            TELEMAN_BASE + "/moje-stacje",
            cache_key="teleman:station-categories",
            ttl_seconds=7 * 24 * 3600,
            force_refresh=force_refresh,
        )
        return parse_teleman_tv_categories(html)

    def get_now_schedule(self, *, force_refresh: bool = False) -> list[NowTvItem]:
        html = self._get_teleman_text(
            TELEMAN_BASE + "/teraz",
            cache_key="teleman:now",
            ttl_seconds=60,
            force_refresh=force_refresh,
        )
        return parse_teleman_now_schedule(html)

    def get_live_sport_schedule(self, *, force_refresh: bool = False) -> list[ScheduleItem]:
        url = TELEMAN_BASE + "/sport?live=1"
        reference_date = date.today()
        visited: set[str] = set()
        seen_items: set[tuple[date, time | None, str, str | None]] = set()
        items: list[ScheduleItem] = []

        for _page_count in range(_MAX_SPORT_PAGES):
            if url in visited:
                raise TelemanSportScheduleParseError(
                    "Teleman.pl zwrócił zapętloną paginację transmisji sportowych."
                )
            visited.add(url)

            page_number = _teleman_live_sport_page_number(url)
            html = self._get_teleman_text(
                url,
                cache_key=f"teleman:sport:live:{page_number}",
                ttl_seconds=60,
                force_refresh=force_refresh,
            )
            page_items, next_href = _parse_teleman_sport_page(
                html,
                reference_date=reference_date,
            )
            for item in page_items:
                key = (
                    item.day,
                    item.start_time,
                    str(item.source.id),
                    item.details_ref,
                )
                if key in seen_items:
                    continue
                seen_items.add(key)
                items.append(item)

            if not next_href:
                return items

            next_url = _safe_teleman_live_sport_url(next_href, current_url=url)
            if next_url is None:
                raise TelemanSportScheduleParseError(
                    "Teleman.pl zwrócił nieprawidłowy link do następnej strony transmisji."
                )
            url = next_url

        raise TelemanSportScheduleParseError(
            f"Lista transmisji sportowych przekroczyła bezpieczny limit {_MAX_SPORT_PAGES} stron."
        )

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        url = f"{TELEMAN_BASE}/program-tv/stacje/{source.id}?date={day.isoformat()}"
        cache_key = f"teleman:station:{source.id}:{day.isoformat()}"
        html = self._get_teleman_text(
            url,
            cache_key=cache_key,
            ttl_seconds=60 * 60,
            force_refresh=force_refresh,
        )
        parsed_items = parse_teleman_station_schedule(html)
        items: list[ScheduleItem] = []
        for it in parsed_items:
            items.append(
                ScheduleItem(
                    provider_id=ProviderId(self.provider_id),
                    source=source,
                    day=day,
                    start_time=it.start_time,
                    end_time=it.end_time,
                    title=it.title,
                    subtitle=it.subtitle,
                    details_ref=it.details_ref,
                    details_summary=it.summary,
                )
            )
        return items

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        if not item.details_ref:
            return item.details_summary or item.title
        url = urljoin(TELEMAN_BASE, item.details_ref)
        cache_key = f"teleman:show:{item.details_ref}"
        html = self._get_teleman_text(
            url,
            cache_key=cache_key,
            ttl_seconds=30 * 24 * 3600,
            force_refresh=force_refresh,
        )
        return parse_teleman_show_details(html) or (item.details_summary or item.title)

    def get_now_item_details(self, item: NowTvItem, *, force_refresh: bool = False) -> str:
        if not item.details_ref:
            return item.subtitle or item.title
        url = urljoin(TELEMAN_BASE, item.details_ref)
        cache_key = f"teleman:show:{item.details_ref}"
        html = self._get_teleman_text(
            url,
            cache_key=cache_key,
            ttl_seconds=30 * 24 * 3600,
            force_refresh=force_refresh,
        )
        return parse_teleman_show_details(html) or (item.subtitle or item.title)

    def _get_teleman_text(
        self,
        url: str,
        *,
        cache_key: str,
        ttl_seconds: int,
        force_refresh: bool,
    ) -> str:
        try:
            return self._http.get_text(
                url,
                cache_key=cache_key,
                ttl_seconds=ttl_seconds,
                force_refresh=force_refresh,
            )
        except requests.Timeout as exc:
            raise RuntimeError(
                "Przekroczono czas oczekiwania na odpowiedź serwisu Teleman.pl."
            ) from exc
        except requests.ConnectionError as exc:
            raise RuntimeError(
                "Nie udało się połączyć z serwisem Teleman.pl. "
                "Sprawdź połączenie z internetem."
            ) from exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            suffix = f" {status}" if status is not None else ""
            raise RuntimeError(f"Serwis Teleman.pl zwrócił błąd HTTP{suffix}.") from exc
        except requests.RequestException as exc:
            raise RuntimeError("Nie udało się pobrać danych z serwisu Teleman.pl.") from exc


class _TelemanParsedItem:
    def __init__(
        self,
        *,
        start_time: time | None,
        end_time: time | None,
        title: str,
        subtitle: str | None,
        summary: str | None,
        details_ref: str | None,
    ) -> None:
        self.start_time = start_time
        self.end_time = end_time
        self.title = title
        self.subtitle = subtitle
        self.summary = summary
        self.details_ref = details_ref


def parse_teleman_now_schedule(html: str) -> list[NowTvItem]:
    soup = BeautifulSoup(html, "lxml")
    table = _find_teleman_now_table(soup)
    if table is None:
        raise TelemanNowScheduleParseError(_TELEMAN_NOW_PARSE_ERROR)

    items: list[NowTvItem] = []
    for row in table.find_all("tr"):
        cell = row.find("td")
        if cell is None:
            continue

        header = row.find("th")
        header_text = clean_text(header.get_text(" ")) if header else ""
        header_times = _parse_time_tokens(header_text)
        header_start = header_times[0] if header_times else None

        for title_link in cell.find_all("a", href=True):
            if not _is_now_title_link(title_link):
                continue

            details_ref = _get_string_attribute(title_link, "href")
            title = clean_text(title_link.get_text(" "))
            if not title or not details_ref:
                continue

            container = _find_teleman_now_item_container(title_link, cell)
            if container is None:
                continue
            station_link, station_slug = _find_teleman_station_link(container)
            if station_link is None or station_slug is None:
                continue
            station_name = clean_text(station_link.get_text(" "))
            if not station_name:
                continue

            range_times = _parse_time_tokens(_get_string_attribute(title_link, "data-time"))
            start_time = range_times[0] if range_times else header_start
            end_time = range_times[1] if len(range_times) > 1 else None

            genre = container.select_one(".genre")
            subtitle = clean_text(genre.get_text(" ")) if genre else None
            subtitle = subtitle or None

            source = Source(
                provider_id=ProviderId("teleman"),
                id=SourceId(station_slug),
                name=station_name,
            )
            items.append(
                NowTvItem(
                    provider_id=ProviderId("teleman"),
                    source=source,
                    start_time=start_time,
                    end_time=end_time,
                    title=title,
                    subtitle=subtitle,
                    details_ref=details_ref,
                    is_current=_is_teleman_now_item_current(container, header_text),
                )
            )

    if not items:
        raise TelemanNowScheduleParseError(_TELEMAN_NOW_PARSE_ERROR)
    return items


def _find_teleman_now_table(soup: BeautifulSoup):
    table = soup.select_one("table#now")
    if table is not None:
        return table
    for candidate in soup.find_all("table"):
        if any(_is_now_title_link(link) for link in candidate.find_all("a", href=True)):
            return candidate
    return None


def _is_now_title_link(link) -> bool:
    href = _get_string_attribute(link, "href")
    if not href or not _is_safe_teleman_href(href):
        return False
    classes = link.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    return "prog-title" in classes or _is_teleman_show_href(href)


def _is_safe_teleman_href(href: str) -> bool:
    parsed = urlsplit(href)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").casefold()
    return not hostname or hostname in {"teleman.pl", "www.teleman.pl"}


def _is_teleman_show_href(href: str) -> bool:
    if not _is_safe_teleman_href(href):
        return False
    return urlsplit(href).path.startswith("/tv/")


def _find_teleman_now_item_container(title_link, cell):
    node = title_link.parent
    while node is not None and node is not cell:
        _station_link, station_slug = _find_teleman_station_link(node)
        if station_slug is not None:
            return node
        node = node.parent
    return None


def _find_teleman_station_link(container):
    for link in container.find_all("a", href=True):
        href = _get_string_attribute(link, "href")
        slug = _teleman_station_slug(href)
        if slug:
            return link, slug
    return None, None


def _teleman_station_slug(href: str) -> str | None:
    if not _is_safe_teleman_href(href):
        return None
    path = urlsplit(href).path
    marker = "/program-tv/stacje/"
    marker_index = path.casefold().find(marker)
    if marker_index < 0:
        return None
    remainder = path[marker_index + len(marker) :].strip("/")
    slug = remainder.split("/", 1)[0]
    return slug or None


def _parse_time_tokens(text: str) -> list[time]:
    parsed: list[time] = []
    for token in _TIME_TOKEN_RE.findall(text):
        value = parse_time_hhmm(token)
        if value is not None:
            parsed.append(value)
    return parsed


def _get_string_attribute(tag, name: str) -> str:
    value = tag.get(name)
    return value.strip() if isinstance(value, str) else ""


def _is_teleman_now_item_current(container, header_text: str) -> bool:
    for marker in container.find_all(attrs={"title": True}):
        if clean_text(_get_string_attribute(marker, "title")).casefold() == "trwa":
            return True
    if container.select_one(".bar") is not None:
        return True
    return re.match(r"^od\b", header_text.casefold()) is not None


def parse_teleman_tv_categories(html: str) -> list[TvCategory]:
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one("#user-stations-form")
    if container is None:
        raise TelemanCategoryParseError(_TELEMAN_CATEGORIES_PARSE_ERROR)

    stations_by_name: dict[str, tuple[str, str]] = {}
    for slug, name in parse_teleman_stations(html):
        stations_by_name.setdefault(name.casefold(), (slug, name))
    if not stations_by_name:
        raise TelemanCategoryParseError(_TELEMAN_CATEGORIES_PARSE_ERROR)

    sections = []
    for candidate in container.find_all(["div", "section", "fieldset"]):
        heading = candidate.find(["h2", "h3"], recursive=False)
        station_input = candidate.find("input", attrs={"name": "station"})
        if heading is not None and station_input is not None:
            sections.append((candidate, heading))

    categories: list[TvCategory] = []
    seen_category_ids: set[str] = set()
    for section, heading in sections:
        category_name = clean_text(heading.get_text(" "))
        category_id = _teleman_category_id(category_name)
        if not category_name or not category_id or category_id in seen_category_ids:
            continue

        sources: list[Source] = []
        seen_source_ids: set[str] = set()
        for label in section.find_all("label"):
            station_input = label.find("input", attrs={"name": "station"})
            if station_input is None:
                continue
            name_span = next(
                (
                    span
                    for span in label.find_all("span")
                    if "new" not in (span.get("class") or [])
                ),
                None,
            )
            station_name = clean_text(name_span.get_text(" ")) if name_span is not None else ""
            station = stations_by_name.get(station_name.casefold())
            if station is None:
                continue
            slug, canonical_name = station
            if slug in seen_source_ids:
                continue
            seen_source_ids.add(slug)
            sources.append(
                Source(
                    provider_id=ProviderId("teleman"),
                    id=SourceId(slug),
                    name=canonical_name,
                )
            )

        if not sources:
            continue
        seen_category_ids.add(category_id)
        categories.append(
            TvCategory(
                id=category_id,
                name=category_name,
                sources=tuple(sources),
            )
        )

    if not categories:
        raise TelemanCategoryParseError(_TELEMAN_CATEGORIES_PARSE_ERROR)
    return categories


def _teleman_category_id(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name.casefold()).replace("ł", "l")
    ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    category_id = re.sub(r"[^a-z0-9]+", "-", ascii_like).strip("-")
    return "sport" if category_id == "sportowe" else category_id


def parse_teleman_sport_schedule(
    html: str,
    *,
    reference_date: date | None = None,
) -> list[ScheduleItem]:
    items, _next_href = _parse_teleman_sport_page(
        html,
        reference_date=reference_date or date.today(),
    )
    return items


def _parse_teleman_sport_page(
    html: str,
    *,
    reference_date: date,
) -> tuple[list[ScheduleItem], str | None]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table#sports-listing")
    if table is None:
        table = next(
            (candidate for candidate in soup.find_all("table") if _is_teleman_sport_table(candidate)),
            None,
        )
    if table is None or not _is_teleman_sport_table(table):
        raise TelemanSportScheduleParseError(_TELEMAN_SPORT_PARSE_ERROR)

    items: list[ScheduleItem] = []
    data_row_count = 0
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        data_row_count += 1
        if len(cells) < 4:
            continue

        station_link, station_slug = _find_teleman_station_link(cells[2])
        if station_link is None or station_slug is None:
            continue
        station_name = clean_text(station_link.get_text(" "))
        station_href = _get_string_attribute(station_link, "href")
        if not station_name or not station_href:
            continue

        title_link = next(
            (
                link
                for link in cells[3].find_all("a", href=True)
                if _is_teleman_show_href(_get_string_attribute(link, "href"))
            ),
            None,
        )
        if title_link is None:
            continue
        details_ref = _get_string_attribute(title_link, "href")
        title = clean_text(title_link.get_text(" "))
        if not title or not details_ref:
            continue

        item_day = _parse_teleman_sport_day(
            clean_text(cells[0].get_text(" ")),
            station_href=station_href,
            reference_date=reference_date,
        )
        if item_day is None:
            continue

        range_times = _parse_time_tokens(_get_string_attribute(title_link, "data-time"))
        fallback_times = _parse_time_tokens(clean_text(cells[1].get_text(" ")))
        start_time = range_times[0] if range_times else (fallback_times[0] if fallback_times else None)
        end_time = range_times[1] if len(range_times) > 1 else None

        subtitle_element = cells[3].find("em")
        subtitle = clean_text(subtitle_element.get_text(" ")) if subtitle_element else None
        subtitle = subtitle or None

        source = Source(
            provider_id=ProviderId("teleman"),
            id=SourceId(station_slug),
            name=station_name,
        )
        items.append(
            ScheduleItem(
                provider_id=ProviderId("teleman"),
                source=source,
                day=item_day,
                start_time=start_time,
                end_time=end_time,
                title=title,
                subtitle=subtitle,
                details_ref=details_ref,
                details_summary=subtitle,
            )
        )

    if data_row_count and not items:
        raise TelemanSportScheduleParseError(_TELEMAN_SPORT_PARSE_ERROR)

    next_link = soup.select_one(".pagination .next a[href]")
    next_href = _get_string_attribute(next_link, "href") if next_link is not None else None
    return items, next_href or None


def _is_teleman_sport_table(table) -> bool:
    header_row = table.find("tr")
    if header_row is None:
        return False
    headers = [clean_text(header.get_text(" ")).casefold() for header in header_row.find_all("th")]
    return headers[:4] == ["data", "godzina", "stacja", "tytuł"]


def _parse_teleman_sport_day(
    text: str,
    *,
    station_href: str,
    reference_date: date,
) -> date | None:
    query = parse_qs(urlsplit(station_href).query)
    date_values = query.get("date") or []
    if len(date_values) == 1:
        try:
            return date.fromisoformat(date_values[0])
        except ValueError:
            pass

    normalized_text = clean_text(text)
    match = re.search(
        r"(?:(\d{1,2})\s+na\s+)?(\d{1,2})\s+([^\W\d_]+)",
        normalized_text,
        flags=re.IGNORECASE,
    )
    if match:
        day_number = int(match.group(2))
        month_number = _POLISH_MONTH_BY_NAME.get(match.group(3).casefold())
        if month_number is not None:
            year_match = re.search(r"\b(\d{4})\b", normalized_text)
            if year_match:
                try:
                    return date(int(year_match.group(1)), month_number, day_number)
                except ValueError:
                    return None

            candidates: list[date] = []
            for year in (reference_date.year - 1, reference_date.year, reference_date.year + 1):
                try:
                    candidates.append(date(year, month_number, day_number))
                except ValueError:
                    continue
            if candidates:
                return min(candidates, key=lambda candidate: abs((candidate - reference_date).days))

    text_casefold = normalized_text.casefold()
    if text_casefold.startswith("dziś"):
        return reference_date
    if text_casefold.startswith("jutro"):
        return reference_date + timedelta(days=1)
    return None


def _safe_teleman_live_sport_url(href: str, *, current_url: str) -> str | None:
    if not href or not _is_safe_teleman_href(href):
        return None
    parsed = urlsplit(urljoin(current_url, href))
    if parsed.username or parsed.password or parsed.fragment:
        return None
    if (parsed.hostname or "").casefold() not in {"teleman.pl", "www.teleman.pl"}:
        return None
    if parsed.path.rstrip("/") != "/sport":
        return None

    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) - {"live", "page"} or query.get("live") != ["1"]:
        return None
    page_values = query.get("page")
    if not page_values or len(page_values) != 1 or not page_values[0].isdigit():
        return None
    page_number = int(page_values[0])
    if page_number < 1:
        return None
    return f"{TELEMAN_BASE}/sport?{urlencode({'live': '1', 'page': page_number})}"


def _teleman_live_sport_page_number(url: str) -> int:
    values = parse_qs(urlsplit(url).query).get("page") or ["1"]
    try:
        page_number = int(values[0])
    except (TypeError, ValueError):
        return 1
    return max(1, page_number)


def parse_teleman_stations(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    nav = soup.select_one("nav#stations-index")
    if not nav:
        return []
    stations: list[tuple[str, str]] = []
    for a in nav.select("a[href^='/program-tv/stacje/']"):
        href = a.get("href")
        if not href:
            continue
        slug = href.rsplit("/", 1)[-1]
        name = clean_text(a.get_text(" "))
        if slug and name:
            stations.append((slug, name))
    # Deduplicate by slug.
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for slug, name in stations:
        if slug in seen:
            continue
        seen.add(slug)
        out.append((slug, name))
    return out


def parse_teleman_station_schedule(html: str) -> list[_TelemanParsedItem]:
    soup = BeautifulSoup(html, "lxml")
    ul = soup.select_one("ul.stationItems")
    if not ul:
        return []

    items: list[_TelemanParsedItem] = []
    for li in ul.select("li[id^='prog']"):
        em = li.find("em")
        start = parse_time_hhmm(clean_text(em.get_text(" "))) if em else None

        detail = li.select_one("div.detail")
        if not detail:
            continue
        a = detail.find("a", href=True)
        title = clean_text(a.get_text(" ")) if a else ""
        href = a.get("href") if a else None

        genre_p = detail.select_one("p.genre")
        subtitle = clean_text(genre_p.get_text(" ")) if genre_p else None

        ps = detail.find_all("p")
        summary = None
        for p in ps:
            if "genre" in (p.get("class") or []):
                continue
            summary = clean_text(p.get_text(" "))
            if summary:
                break

        items.append(
            _TelemanParsedItem(
                start_time=start,
                end_time=None,
                title=title or (summary or ""),
                subtitle=subtitle,
                summary=summary,
                details_ref=href,
            )
        )

    # Infer end time from next start time.
    for i in range(len(items) - 1):
        if items[i].start_time and items[i + 1].start_time:
            items[i].end_time = items[i + 1].start_time  # type: ignore[attr-defined]
    return items


def parse_teleman_show_details(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    sections = []
    for h2 in soup.select("div.section > h2"):
        title = clean_text(h2.get_text(" "))
        if title not in {"Opis", "W tym odcinku"}:
            continue
        p = h2.find_next_sibling("p")
        if not p:
            continue
        body = clean_text(p.get_text(" "))
        if body:
            sections.append(f"{title}:\n{body}")
    return "\n\n".join(sections)
