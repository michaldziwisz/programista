from datetime import date

from tvguide_app.core.providers.fandom_archive import (
    date_to_fandom_page_title_candidates,
    extract_channel_schedule_from_wikitext,
    extract_channels_from_wikitext,
    parse_channel_from_category_title,
    parse_entry_start_and_rest,
    split_schedule_entries,
    split_title_subtitle,
)


def test_parse_channel_from_category_title() -> None:
    title = "Kategoria:Ramówki TVP 1 HD z 2013 roku"
    assert parse_channel_from_category_title(title, 2013) == "TVP 1 HD"


def test_extract_channel_schedule_from_wikitext() -> None:
    wikitext = """
====TVP 1 HD====
04:55 Wiadomości<br />05:20 TELEZAKUPY<br />06:00 Kawa czy herbata?
====TVP 2 HD====
05:15 TELEZAKUPY<br />06:30 Złotopolscy
"""
    block = extract_channel_schedule_from_wikitext(wikitext, "TVP 1 HD")
    assert "04:55" in block
    assert "Złotopolscy" not in block


def test_split_schedule_entries_and_parse_time() -> None:
    block = "04:55 Wiadomości<br />05:20 TELEZAKUPY"
    entries = split_schedule_entries(block)
    assert entries == ["04:55 Wiadomości", "05:20 TELEZAKUPY"]
    t, rest = parse_entry_start_and_rest(entries[0])
    assert t is not None and t.hour == 4 and t.minute == 55
    assert rest == "Wiadomości"


def test_split_title_subtitle() -> None:
    title, subtitle = split_title_subtitle("Kawa czy herbata? - Wiadomości: 6.00")
    assert title == "Kawa czy herbata?"
    assert subtitle == "Wiadomości: 6.00"


def test_date_to_fandom_page_title_candidates_handles_leading_zero_days() -> None:
    assert date_to_fandom_page_title_candidates(date(2013, 1, 1)) == [
        "01 Stycznia 2013",
        "1 Stycznia 2013",
        "01 stycznia 2013",
        "1 stycznia 2013",
    ]
    assert date_to_fandom_page_title_candidates(date(2013, 1, 10)) == ["10 Stycznia 2013", "10 stycznia 2013"]


def test_extract_channels_and_schedule_from_old_style_wikitext() -> None:
    wikitext = """
[[File:Tvp1 logo.gif|left|thumb|80x80px]]
<br />7.00 Program dnia<br />7.05 Jednego życia za mało
[[File:TVP2 1992-2000.png|left|thumb|80x80px]]
<br />8.00 Gwiazdy
[[Kategoria:Ramówki TVP 1 z 1997 roku]]
[[Kategoria:Ramówki TVP 2 z 1997 roku]]
"""
    channels = extract_channels_from_wikitext(wikitext)
    assert channels == ["TVP 1", "TVP 2"]
    tvp1 = extract_channel_schedule_from_wikitext(wikitext, "TVP 1")
    assert "Program dnia" in tvp1
    tvp2 = extract_channel_schedule_from_wikitext(wikitext, "TVP 2")
    assert "8.00" in tvp2


def test_category_tp1_tp2_channels_match_program_1_2_sections() -> None:
    wikitext = """
Program 1<br />8:50 Program dnia<br />9:00 A

Program 2<br />11:30 Powitanie<br />12:00 B

BBC1<br />7.00 Children's BBC
[[Kategoria:Ramówki TP1 z 1990 roku]]
[[Kategoria:Ramówki TP2 z 1990 roku]]
[[Kategoria:Ramówki BBC1 z 1990 roku]]
"""
    channels = extract_channels_from_wikitext(wikitext)
    assert channels == ["TP1", "TP2", "BBC1"]

    tp1 = extract_channel_schedule_from_wikitext(wikitext, "TP1")
    assert "8:50 Program dnia" in tp1
    tp2 = extract_channel_schedule_from_wikitext(wikitext, "TP2")
    assert "11:30 Powitanie" in tp2


def test_parse_entry_start_and_rest_accepts_time_ranges() -> None:
    t, rest = parse_entry_start_and_rest("5.45-9.00 Blok pr. dla dzieci")
    assert t is not None and t.hour == 5 and t.minute == 45
    assert rest == "Blok pr. dla dzieci"


def test_parse_entry_start_and_rest_accepts_time_with_space_separator() -> None:
    t, rest = parse_entry_start_and_rest("18 10 - Studio Sport")
    assert t is not None and t.hour == 18 and t.minute == 10
    assert rest == "Studio Sport"


def test_extract_channels_and_schedule_from_plain_text_sections() -> None:
    wikitext = """
TVP 1<br />7.00 Program dnia<br />7.05 Jednego życia za mało

TVP 2<br />8.00 Gwiazdy<br />9.00 Panorama
"""
    channels = extract_channels_from_wikitext(wikitext)
    assert channels == ["TVP 1", "TVP 2"]
    block = extract_channel_schedule_from_wikitext(wikitext, "TVP 2")
    entries = split_schedule_entries(block)
    assert entries[0].startswith("8.00 ")


def test_extract_channels_and_schedule_from_plain_sections_with_newlines() -> None:
    wikitext = """
'''CZWARTEK 01.01.2026 (NOWY ROK)'''

TVP 1

05:30 A

07:00 B

TVP 2
05:55 C
"""
    channels = extract_channels_from_wikitext(wikitext)
    assert channels == ["TVP 1", "TVP 2"]
    block = extract_channel_schedule_from_wikitext(wikitext, "TVP 1")
    entries = split_schedule_entries(block)
    assert entries == ["05:30 A", "07:00 B"]


def test_plain_section_parser_handles_editorial_time_with_space_without_splitting_channels() -> None:
    wikitext = """
Program 1<br />16.45 - Program dnia

Program 2<br />17.00 - Program dnia<br />18 10 - Studio Sport<br />19.10 - PANORAMA
"""
    channels = extract_channels_from_wikitext(wikitext)
    assert channels == ["Program 1", "Program 2"]
    block = extract_channel_schedule_from_wikitext(wikitext, "Program 2")
    entries = split_schedule_entries(block)
    assert "18 10 - Studio Sport" in entries


def test_plain_section_parser_ignores_non_channel_headings_that_look_like_titles() -> None:
    wikitext = """
Program 2<br />9.55 Program dnia<br />'''SPRAWDZONA SPÓŁKA - \"STUDIO-2\" i REDAKCJA MUZYCZNA'''<br />10.00 Spotkanie

XEW TV<br />13.00 BOTANICA
"""
    channels = extract_channels_from_wikitext(wikitext)
    assert channels == ["Program 2", "XEW TV"]
    block = extract_channel_schedule_from_wikitext(wikitext, "Program 2")
    entries = split_schedule_entries(block)
    assert entries[0].startswith("9.55 ")
    assert any(e.startswith("10.00 ") for e in entries)


def test_fallback_single_channel_page_defaults_to_tvp1() -> None:
    wikitext = """
CZWARTEK

19.25 Program dnia<br/>
19.30 Kronika filmowa dla dzieci i młodzieży<br/>
19.55 Dobranoc<br/>
20.00 Dziennik telewizyjny<br/>
"""
    channels = extract_channels_from_wikitext(wikitext)
    assert channels == ["TVP 1"]
    block = extract_channel_schedule_from_wikitext(wikitext, "TVP 1")
    entries = split_schedule_entries(block)
    assert entries[0].startswith("19.25 ")
