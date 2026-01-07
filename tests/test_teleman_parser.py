from tvguide_app.core.providers.teleman import (
    parse_teleman_show_details,
    parse_teleman_station_schedule,
    parse_teleman_stations,
)


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

