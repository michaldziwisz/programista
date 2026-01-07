from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from tvguide_app.core.http import HttpClient
from tvguide_app.core.models import ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.util import clean_multiline_text, clean_text, parse_time_hhmm


PR_BASE = "https://www.polskieradio.pl"
PR_MULTISCHEDULE_URL = (
    "https://www.polskieradio.pl/Portal/Schedule/AjaxPages/AjaxGetMultiScheduleView.aspx"
)
PR_DETAILS_URL = "https://www.polskieradio.pl/Portal/Schedule/AjaxPages/AjaxGetProgrammeDetails.aspx"


PR_CHANNELS: list[str] = ["Jedynka", "Dwójka", "Trójka", "Czwórka", "Radio Poland", "PR24"]


class PolskieRadioProvider(ScheduleProvider):
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @property
    def provider_id(self) -> str:
        return "polskieradio"

    @property
    def display_name(self) -> str:
        return "Radio"

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        return [
            Source(provider_id=ProviderId(self.provider_id), id=SourceId(name), name=name)
            for name in PR_CHANNELS
        ]

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        # Keep it simple: 14-day window centered on today.
        today = date.today()
        start = today - timedelta(days=7)
        return [start + timedelta(days=i) for i in range(14)]

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        all_by_channel = self._get_multischedule(day, force_refresh=force_refresh)
        items = all_by_channel.get(source.name, [])
        return [
            ScheduleItem(
                provider_id=ProviderId(self.provider_id),
                source=source,
                day=day,
                start_time=item.start_time,
                end_time=None,
                title=item.title,
                subtitle=None,
                details_ref=item.details_ref,
                details_summary=None,
            )
            for item in items
        ]

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        if not item.details_ref:
            return item.title

        cache_key = f"pr:details:{item.details_ref}"
        html = self._http.post_form_text(
            PR_DETAILS_URL,
            data=parse_details_ref(item.details_ref),
            cache_key=cache_key,
            ttl_seconds=7 * 24 * 3600,
            force_refresh=force_refresh,
        )
        popup = parse_pr_programme_details_popup_html(html)
        lead = popup.lead
        description = popup.description

        if (not lead and not description) and popup.programme_href:
            programme_url = urljoin(PR_BASE, popup.programme_href)
            try:
                programme_html = self._http.get_text(
                    programme_url,
                    cache_key=f"pr:programme:{popup.programme_href}",
                    ttl_seconds=30 * 24 * 3600,
                    force_refresh=force_refresh,
                )
            except Exception:  # noqa: BLE001
                programme_html = ""

            if programme_html:
                programme = parse_pr_programme_page_html(programme_html)
                if programme.lead:
                    lead = programme.lead
                if programme.description:
                    description = programme.description

        return format_pr_programme_details(
            start_time=popup.start_time,
            title=popup.title,
            lead=lead,
            description=description,
        ) or item.title

    def _get_multischedule(self, day: date, *, force_refresh: bool) -> dict[str, list[_PrItem]]:
        day_s = day.isoformat()
        cache_key = f"pr:multischedule:{day_s}"
        html = self._http.post_form_text(
            PR_MULTISCHEDULE_URL,
            data={"selectedDate": day_s},
            cache_key=cache_key,
            ttl_seconds=60 * 30,
            force_refresh=force_refresh,
        )
        return parse_pr_multischedule_html(html, day, PR_CHANNELS)


@dataclass(frozen=True)
class _PrItem:
    start_time: time | None
    title: str
    details_ref: str | None


def parse_details_ref(details_ref: str) -> dict[str, str]:
    # details_ref format: "{scheduleId}|{programmeId}|{startTime}|{selectedDate}"
    schedule_id, programme_id, start_time, selected_date = details_ref.split("|", 3)
    return {
        "scheduleId": schedule_id,
        "programmeId": programme_id,
        "startTime": start_time,
        "selectedDate": selected_date,
    }


def parse_pr_multischedule_html(html: str, day: date, channel_order: list[str]) -> dict[str, list[_PrItem]]:
    soup = BeautifulSoup(html, "lxml")
    containers = soup.select("div.scheduleViewContainer")
    by_channel: dict[str, list[_PrItem]] = {}
    for idx, container in enumerate(containers):
        if idx >= len(channel_order):
            break
        channel = channel_order[idx]
        items: list[_PrItem] = []
        for li in container.select("li"):
            a = li.find("a", onclick=True)
            if not a:
                continue
            onclick = a.get("onclick") or ""
            details_ref = parse_onclick_details_ref(onclick)
            title = _extract_programme_title(a)
            start = None
            start_span = li.select_one("span.sTime") or li.select_one(".emitedNowProgrammeStartHour")
            if start_span:
                start = parse_time_hhmm(clean_text(start_span.get_text()))
            items.append(_PrItem(start_time=start, title=title, details_ref=details_ref))
        by_channel[channel] = items
    return by_channel


def parse_onclick_details_ref(onclick: str) -> str | None:
    # showProgrammeDetails('1307315','10548','00:00','2026-01-06')
    m = re.search(
        r"showProgrammeDetails\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*\)",
        onclick,
    )
    if not m:
        return None
    return "|".join([m.group(1), m.group(2), m.group(3), m.group(4)])


def _extract_programme_title(a) -> str:
    # Normal entries: <span class="desc">Title</span>
    # Currently-on-air entries: <span class="title">Title</span>
    title_el = a.select_one("span.desc") or a.select_one("span.title") or a.select_one(".desc")
    if title_el:
        t = clean_text(title_el.get_text(" "))
        if t:
            return t

    title_attr = clean_text(a.get("title") or "")
    if title_attr:
        return title_attr

    return clean_text(a.get_text(" "))


@dataclass(frozen=True)
class PrProgrammeDetailsPopup:
    start_time: str
    title: str
    lead: str
    description: str
    programme_href: str | None


@dataclass(frozen=True)
class PrProgrammePageDetails:
    lead: str
    description: str


def parse_pr_programme_details_popup_html(html: str) -> PrProgrammeDetailsPopup:
    soup = BeautifulSoup(html, "lxml")

    start_time_el = soup.select_one("#programmeDetails_lblProgrammeStartTime")
    programme_title_el = soup.select_one("#programmeDetails_lblProgrammeTitle")
    lead_el = soup.select_one("#programmeDetails_lblProgrammeLead")
    description_el = soup.select_one("#programmeDetails_lblProgrammeDescription")
    website_el = soup.select_one("#programmeDetails_hypProgrammeWebsite")

    start_time_s = clean_text(start_time_el.get_text(" ")) if start_time_el else ""
    title_s = clean_text(programme_title_el.get_text(" ")) if programme_title_el else ""
    lead_s = clean_multiline_text(lead_el.get_text("\n")) if lead_el else ""

    desc_s = ""
    if description_el:
        desc_s = clean_multiline_text(description_el.get_text("\n"))

    programme_href = None
    if website_el:
        href = website_el.get("href")
        if isinstance(href, str) and href.strip():
            programme_href = href.strip()

    return PrProgrammeDetailsPopup(
        start_time=start_time_s,
        title=title_s,
        lead=_normalize_pr_description(lead_s),
        description=_normalize_pr_description(desc_s),
        programme_href=programme_href,
    )


def parse_pr_programme_page_html(html: str) -> PrProgrammePageDetails:
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return PrProgrammePageDetails(lead="", description="")

    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return PrProgrammePageDetails(lead="", description="")

    details = (
        data.get("props", {})
        .get("pageProps", {})
        .get("details", {})
    )
    if not isinstance(details, dict):
        return PrProgrammePageDetails(lead="", description="")

    lead_raw = details.get("lead", "")
    desc_html = details.get("description", "")

    lead = clean_multiline_text(str(lead_raw)) if lead_raw else ""

    desc_text = ""
    if desc_html:
        desc_text = clean_multiline_text(BeautifulSoup(str(desc_html), "lxml").get_text("\n"))

    return PrProgrammePageDetails(
        lead=_normalize_pr_description(lead),
        description=_normalize_pr_description(desc_text),
    )


def format_pr_programme_details(*, start_time: str, title: str, lead: str, description: str) -> str:
    header = " ".join([p for p in (start_time, title) if p])
    parts = [p for p in (header, lead, description) if p]
    return "\n\n".join(parts)


def _normalize_pr_description(text: str) -> str:
    t = clean_multiline_text(text)
    if not t:
        return ""
    lowered = t.casefold()
    if lowered in {"s", ".", "-", "—", "–"}:
        return ""
    if len(t) <= 2:
        return ""
    return t
