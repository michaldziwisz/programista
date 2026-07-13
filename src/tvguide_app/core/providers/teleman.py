from __future__ import annotations

import re
from datetime import date, time
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from tvguide_app.core.http import HttpClient
from tvguide_app.core.models import NowTvItem, ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.util import clean_text, parse_time_hhmm


TELEMAN_BASE = "https://www.teleman.pl"
_TELEMAN_NOW_PARSE_ERROR = (
    "Nie udało się odczytać sekcji „Teraz w TV” z serwisu Teleman.pl. "
    "Układ strony mógł się zmienić."
)
_TIME_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,2}[:.]\d{2})(?!\d)")


class TelemanNowScheduleParseError(ValueError):
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

    def get_now_schedule(self, *, force_refresh: bool = False) -> list[NowTvItem]:
        html = self._get_teleman_text(
            TELEMAN_BASE + "/teraz",
            cache_key="teleman:now",
            ttl_seconds=60,
            force_refresh=force_refresh,
        )
        return parse_teleman_now_schedule(html)

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        url = f"{TELEMAN_BASE}/program-tv/stacje/{source.id}?date={day.isoformat()}"
        cache_key = f"teleman:station:{source.id}:{day.isoformat()}"
        html = self._http.get_text(
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
        html = self._http.get_text(
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
