from datetime import date

from tvguide_app.core.providers.polskieradio import (
    parse_onclick_details_ref,
    parse_pr_multischedule_html,
    parse_pr_programme_details_popup_html,
    parse_pr_programme_page_html,
)


def test_parse_onclick_details_ref() -> None:
    onclick = "showProgrammeDetails('1307315','10548','00:00','2026-01-06')"
    assert parse_onclick_details_ref(onclick) == "1307315|10548|00:00|2026-01-06"


def test_parse_pr_multischedule_html_basic() -> None:
    html = """
    <div class="scheduleViewContainer"><ul class="scheduleView">
      <li class='programmeLi'><a onclick="showProgrammeDetails('1','2','00:00','2026-01-06')"><span class="sTime">00:00</span><span class="desc">A</span></a></li>
      <li class='programmeLi'><a onclick="showProgrammeDetails('1','3','01:00','2026-01-06')"><span class="sTime">01:00</span><span class="desc">B</span></a></li>
    </ul></div>
    <div class="scheduleViewContainer"><ul class="scheduleView">
      <li class='programmeLi'><a onclick="showProgrammeDetails('9','8','00:00','2026-01-06')"><span class="sTime">00:00</span><span class="desc">C</span></a></li>
    </ul></div>
    """
    by_channel = parse_pr_multischedule_html(html, date(2026, 1, 6), ["Jedynka", "Dwójka"])
    assert len(by_channel["Jedynka"]) == 2
    assert by_channel["Jedynka"][0].title == "A"
    assert by_channel["Dwójka"][0].details_ref == "9|8|00:00|2026-01-06"


def test_parse_pr_programme_details_popup_html_filters_placeholder_description() -> None:
    html = """
    <div class="popupScheduleContent">
        <span id="programmeDetails_lblProgrammeStartTime">04:05</span>
        <span id="programmeDetails_lblProgrammeTitle">Tak to bywało</span>
        <span id="programmeDetails_lblProgrammeLead"></span>
        <span id="programmeDetails_lblProgrammeDescription"><p>s</p></span>
        <a id="programmeDetails_hypProgrammeWebsite" href="/7/3727"><span>Strona audycji</span></a>
    </div>
    """
    popup = parse_pr_programme_details_popup_html(html)
    assert popup.start_time == "04:05"
    assert popup.title == "Tak to bywało"
    assert popup.description == ""
    assert popup.programme_href == "/7/3727"


def test_parse_pr_programme_page_html_reads_next_data() -> None:
    html = """
    <html><body>
      <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"details":{"lead":"Lead text","description":"<p>.</p>"}}}}
      </script>
    </body></html>
    """
    page = parse_pr_programme_page_html(html)
    assert page.lead == "Lead text"
    assert page.description == ""
