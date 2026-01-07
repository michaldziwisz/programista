from __future__ import annotations

import re
from datetime import date, time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from tvguide_app.core.http import HttpClient
from tvguide_app.core.models import ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.util import clean_text, parse_time_hhmm


TELEMAN_BASE = "https://www.teleman.pl"


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

