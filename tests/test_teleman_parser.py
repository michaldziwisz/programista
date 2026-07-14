from dataclasses import replace
from datetime import time
from pathlib import Path

import pytest
import requests

from tvguide_app.core.providers.teleman import (
    TELEMAN_BASE,
    TelemanNowScheduleParseError,
    TelemanProvider,
    parse_teleman_now_schedule,
    parse_teleman_show_details,
    parse_teleman_station_schedule,
    parse_teleman_stations,
)


NOW_HTML = """
<table id="now">
  <tr>
    <th>od 27 minut <span>08:25</span></th>
    <td>
      <div class="cat-ser">
        <span class="bar bar-50" title="trwa"></span>
        <a class="prog-title" href="/tv/Ostry-Dyzur-8-2254851"
           data-time="8:25-9:25">Ostry dyżur &amp; przyjaciele</a>
        | <span class="genre"> serial obyczajowy </span>
        | <a href="/program-tv/stacje/AXN">AXN</a>
      </div>
    </td>
  </tr>
  <tr>
    <th>za 8 minut <span>09:30</span></th>
    <td>
      <div class="cat-fil">
        <a class="prog-title" href="/tv/Nadzieja-627766"
           data-time="9:30-11:05">Nadzieja</a>
        | <span class="genre">dramat sensacyjny</span>
        | <a href="/program-tv/stacje/CanalPlus">CANAL+ Premium</a>
      </div>
    </td>
  </tr>
</table>
"""


class _FakeHttp:
    def __init__(self, html: str = "", error: Exception | None = None) -> None:
        self.html = html
        self.error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get_text(self, url: str, **kwargs: object) -> str:
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.html


def _http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(response=response)


def test_parse_teleman_now_schedule_current_and_upcoming() -> None:
    items = parse_teleman_now_schedule(NOW_HTML)

    assert [item.title for item in items] == ["Ostry dyżur & przyjaciele", "Nadzieja"]
    current, upcoming = items
    assert current.provider_id == "teleman"
    assert current.source.provider_id == "teleman"
    assert current.source.id == "AXN"
    assert current.source.name == "AXN"
    assert current.start_time == time(8, 25)
    assert current.end_time == time(9, 25)
    assert current.subtitle == "serial obyczajowy"
    assert current.details_ref == "/tv/Ostry-Dyzur-8-2254851"
    assert current.is_current is True

    assert upcoming.source.id == "CanalPlus"
    assert upcoming.start_time == time(9, 30)
    assert upcoming.end_time == time(11, 5)
    assert upcoming.is_current is False


def test_parse_teleman_now_schedule_handles_changed_wrappers_and_midnight() -> None:
    html = """
    <table class="renamed-now-table">
      <tr><th>od 10 minut <span>23.55</span></th><td>
        <article><section class="renamed-item">
          <a data-time="23.55 – 1:15"
             href="https://www.teleman.pl/tv/Nocny-Film-123">Nocny film</a>
          <span class="genre"><strong>film</strong> nocny</span>
          <a href="https://teleman.pl/program-tv/stacje/Kino%20TV/?source=now">
            FILMBOX+ One
          </a>
        </section></article>
      </td></tr>
    </table>
    """

    [item] = parse_teleman_now_schedule(html)

    assert item.start_time == time(23, 55)
    assert item.end_time == time(1, 15)
    assert item.source.id == "Kino%20TV"
    assert item.source.name == "FILMBOX+ One"
    assert item.subtitle == "film nocny"
    assert item.is_current is True


def test_parse_teleman_now_schedule_falls_back_to_header_time() -> None:
    html = """
    <table id="now"><tr><th>za chwilę <span>10:05</span></th><td>
      <div>
        <a class="prog-title" data-time="brak" href="/tv/Test-1">Test</a>
        <a href="/program-tv/stacje/TVP-1">TVP 1</a>
      </div>
    </td></tr></table>
    """

    [item] = parse_teleman_now_schedule(html)

    assert item.start_time == time(10, 5)
    assert item.end_time is None
    assert item.subtitle is None
    assert item.is_current is False


def test_parse_teleman_now_schedule_skips_broken_item_and_keeps_duplicates() -> None:
    html = """
    <table id="now"><tr><th>za 5 minut <span>10:00</span></th><td>
      <div><a class="prog-title" href="/tv/Bez-Stacji">Uszkodzony</a></div>
      <div>
        <a class="prog-title" data-time="10:00-11:00" href="/tv/Test-1">Test</a>
        <a href="/program-tv/stacje/TVP-1">TVP 1</a>
      </div>
      <div>
        <a class="prog-title" data-time="10:00-11:00" href="/tv/Test-1">Test</a>
        <a href="/program-tv/stacje/TVP-2">TVP 2</a>
      </div>
    </td></tr></table>
    """

    items = parse_teleman_now_schedule(html)

    assert [item.title for item in items] == ["Test", "Test"]
    assert [item.source.name for item in items] == ["TVP 1", "TVP 2"]


@pytest.mark.parametrize(
    "html",
    [
        "<html><h1>Strona logowania</h1></html>",
        '<table id="now"><tr><td><a class="prog-title" href="/tv/Test">Test</a></td></tr></table>',
    ],
)
def test_parse_teleman_now_schedule_reports_changed_html(html: str) -> None:
    with pytest.raises(TelemanNowScheduleParseError, match="Układ strony mógł się zmienić"):
        parse_teleman_now_schedule(html)


def test_teleman_provider_get_now_schedule_uses_http_cache_conventions() -> None:
    http = _FakeHttp(NOW_HTML)
    provider = TelemanProvider(http)  # type: ignore[arg-type]

    items = provider.get_now_schedule(force_refresh=True)

    assert len(items) == 2
    assert http.calls == [
        (
            TELEMAN_BASE + "/teraz",
            {
                "cache_key": "teleman:now",
                "ttl_seconds": 60,
                "force_refresh": True,
            },
        )
    ]


def test_teleman_provider_get_now_item_details_reuses_show_parser_and_cache() -> None:
    [item, _upcoming] = parse_teleman_now_schedule(NOW_HTML)
    http = _FakeHttp('<div class="section"><h2>Opis</h2><p>Pełny opis.</p></div>')
    provider = TelemanProvider(http)  # type: ignore[arg-type]

    details = provider.get_now_item_details(item, force_refresh=True)

    assert details == "Opis:\nPełny opis."
    assert http.calls == [
        (
            TELEMAN_BASE + item.details_ref,
            {
                "cache_key": f"teleman:show:{item.details_ref}",
                "ttl_seconds": 30 * 24 * 3600,
                "force_refresh": True,
            },
        )
    ]


def test_teleman_provider_get_now_item_details_without_link_uses_local_text() -> None:
    [item, _upcoming] = parse_teleman_now_schedule(NOW_HTML)
    item = replace(item, details_ref=None)
    http = _FakeHttp()
    provider = TelemanProvider(http)  # type: ignore[arg-type]

    assert provider.get_now_item_details(item) == "serial obyczajowy"
    assert http.calls == []


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (requests.Timeout(), "Przekroczono czas oczekiwania"),
        (requests.ConnectionError(), "Nie udało się połączyć"),
        (_http_error(503), "błąd HTTP 503"),
        (requests.RequestException(), "Nie udało się pobrać danych"),
    ],
)
def test_teleman_provider_get_now_schedule_translates_network_errors(
    error: Exception,
    message: str,
) -> None:
    provider = TelemanProvider(_FakeHttp(error=error))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match=message):
        provider.get_now_schedule()


_REAL_NOW_FIXTURE = Path(__file__).parent / "fixtures" / "teleman_teraz.html"


def test_parse_teleman_now_schedule_on_real_page_snapshot() -> None:
    # Regresja na realnym snapshocie sekcji "Teraz w TV" z teleman.pl/teraz.
    # Chroni przed zmiana ukladu strony, ktorej testy syntetyczne moga nie wychwycic.
    html = _REAL_NOW_FIXTURE.read_text(encoding="utf-8")

    items = parse_teleman_now_schedule(html)

    # Snapshot zawiera pelna ramowke "teraz i za chwile" - dziesiatki pozycji.
    assert len(items) >= 20
    # Kazda pozycja musi miec sensowne, niepuste dane - inaczej parser cos gubi.
    assert all(item.title for item in items)
    assert all(item.source.name for item in items)
    assert all(item.source.id for item in items)
    assert all(item.start_time is not None for item in items)
    # Linki do szczegolow to bezpieczne sciezki Teleman.pl (/tv/...).
    assert all(item.details_ref and item.details_ref.startswith("/tv/") for item in items)
    # Na stronie "teraz" zawsze sa programy aktualnie emitowane.
    assert any(item.is_current for item in items)
    # ...oraz rozpoczynajace sie za chwile.
    assert any(not item.is_current for item in items)


def test_parse_teleman_stations() -> None:
    html = """
    <nav id="stations-index">
      <a href="/program-tv/stacje/TVP-1">TVP 1</a>
      <a href="/program-tv/stacje/TVP-2">TVP 2</a>
    </nav>
    """
    stations = parse_teleman_stations(html)
    assert ("TVP-1", "TVP 1") in stations


def test_parse_teleman_station_schedule() -> None:
    html = """
    <ul class="stationItems">
      <li id="prog1"><em>15:05</em><div class="detail">
        <a href="/tv/Test-1-123">Test 1</a>
        <p class="genre">serial</p>
        <p>Opis 1</p>
      </div></li>
      <li id="prog2"><em>16:05</em><div class="detail">
        <a href="/tv/Test-2-456">Test 2</a>
        <p class="genre">film</p>
      </div></li>
    </ul>
    """
    items = parse_teleman_station_schedule(html)
    assert len(items) == 2
    assert items[0].title == "Test 1"
    assert items[0].subtitle == "serial"


def test_parse_teleman_show_details() -> None:
    html = """
    <div class="section"><h2>Opis</h2><p>To jest opis.</p></div>
    <div class="section"><h2>W tym odcinku</h2><p>To jest odcinek.</p></div>
    """
    text = parse_teleman_show_details(html)
    assert "Opis:" in text
    assert "To jest opis." in text
