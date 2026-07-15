from __future__ import annotations

from datetime import date, time
import threading
import time as time_module

import pytest


wx = pytest.importorskip("wx", reason="test widoku kategorii wymaga wxPython")

from tvguide_app.core.models import (  # noqa: E402
    ProviderId,
    ScheduleItem,
    Source,
    SourceId,
    TvCategory,
)
from tvguide_app.gui.schedule_tabs import TvTab  # noqa: E402


TEST_DAY = date(2026, 7, 14)
PACK_SOURCE = Source(ProviderId("pack"), SourceId("main"), "Kanał pakietu")
FILM_SOURCE = Source(ProviderId("teleman"), SourceId("Film-TV"), "Film TV")
SPORT_SOURCE = Source(ProviderId("teleman"), SourceId("Sport-Net"), "Sport Net")
LIVE_GENERAL_SOURCE = Source(ProviderId("teleman"), SourceId("TVP-1"), "TVP 1")


class _ScheduleProvider:
    provider_id = "pack"
    display_name = "Pakiet testowy"

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        return [PACK_SOURCE]

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        return [TEST_DAY]

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        return []

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        return item.title


class _CategoryProvider:
    def __init__(self) -> None:
        self.live_calls = 0
        self.live_thread_id: int | None = None
        self.live_error: Exception | None = None
        self.live_gate: threading.Event | None = None

    def list_tv_categories(self, *, force_refresh: bool = False) -> list[TvCategory]:
        return [
            TvCategory(id="film", name="Filmowe", sources=(FILM_SOURCE,)),
            TvCategory(id="sport", name="Sportowe", sources=(SPORT_SOURCE,)),
        ]

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        return [TEST_DAY]

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        return [
            ScheduleItem(
                provider_id=source.provider_id,
                source=source,
                day=day,
                start_time=time(10, 0),
                end_time=time(11, 0),
                title="Program kategorii",
                subtitle=None,
                details_ref=None,
                details_summary=None,
            )
        ]

    def get_live_sport_schedule(self, *, force_refresh: bool = False) -> list[ScheduleItem]:
        self.live_calls += 1
        self.live_thread_id = threading.get_ident()
        if self.live_gate is not None and not self.live_gate.wait(timeout=2):
            raise TimeoutError("Test nie zwolnił pobierania transmisji")
        if self.live_error is not None:
            raise self.live_error
        return [
            ScheduleItem(
                provider_id=LIVE_GENERAL_SOURCE.provider_id,
                source=LIVE_GENERAL_SOURCE,
                day=TEST_DAY,
                start_time=time(20, 10),
                end_time=time(20, 15),
                title="Sport na żywo",
                subtitle="magazyn sportowy",
                details_ref="/tv/Sport-1",
                details_summary="magazyn sportowy",
            )
        ]

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        return item.details_summary or item.title


class _FavoritesStore:
    def is_favorite(self, _ref) -> bool:
        return False

    def add_source(self, *_args) -> bool:
        return True

    def remove(self, *_args) -> bool:
        return True


def _pump_events() -> None:
    wx.YieldIfNeeded()
    time_module.sleep(0.01)


def _wait_until(condition, *, timeout: float = 2.0) -> None:
    deadline = time_module.monotonic() + timeout
    while time_module.monotonic() < deadline:
        _pump_events()
        if condition():
            return
    raise AssertionError("Warunek GUI nie został spełniony przed upływem limitu czasu")


def test_tv_category_tree_and_live_sport_filter() -> None:
    main_thread_id = threading.get_ident()
    app = wx.App.Get() or wx.App(False)
    frame = wx.Frame(None, title="Test kategorii TV", size=(1000, 700))
    tab = None

    try:
        status = frame.CreateStatusBar()
        category_provider = _CategoryProvider()
        tab = TvTab(
            frame,
            _ScheduleProvider(),
            status,
            favorites_store=_FavoritesStore(),
            on_favorites_changed=lambda: None,
            category_provider=category_provider,
        )
        frame.Show()

        _wait_until(lambda: tab._category_metadata_loaded)
        assert [tab._view_choice.GetString(i) for i in range(tab._view_choice.GetCount())] == [
            "wg kanałów",
            "wg dni",
            "wg kategorii",
        ]

        tab._view_choice.SetSelection(2)
        tab._on_view_choice(wx.CommandEvent())
        sport_key = tab._category_key("sport")
        tab._nav.SetSelection(tab._nav_index_by_key[sport_key])
        tab._on_nav_selection(wx.CommandEvent())
        assert tab._live_sport_cb.IsShown()
        assert tab._live_sport_cb.GetName() == "Pokaż tylko programy na żywo"

        tab._set_nav_expanded(sport_key, expanded=True)
        static_source_key = tab._category_source_key("sport", SPORT_SOURCE)
        assert static_source_key in tab._nav_index_by_key

        tab._live_sport_cb.SetValue(True)
        tab._on_live_sport_changed(wx.CommandEvent())
        _wait_until(lambda: tab._live_sport_enabled and not tab._live_sport_loading)
        assert category_provider.live_calls == 1
        assert category_provider.live_thread_id != main_thread_id

        live_source_key = tab._category_source_key("sport", LIVE_GENERAL_SOURCE)
        assert live_source_key in tab._nav_index_by_key
        assert static_source_key not in tab._nav_index_by_key

        tab._set_nav_expanded(live_source_key, expanded=True)
        live_pair_key = tab._category_pair_key("sport", LIVE_GENERAL_SOURCE, TEST_DAY)
        tab._set_nav_selection(tab._nav_index_by_key[live_pair_key])
        assert tab._list.GetItemCount() == 1
        assert tab._items[0].title == "Sport na żywo"

        film_key = tab._category_key("film")
        tab._nav.SetSelection(tab._nav_index_by_key[film_key])
        tab._on_nav_selection(wx.CommandEvent())
        assert not tab._live_sport_cb.IsShown()

        tab._nav.SetSelection(tab._nav_index_by_key[sport_key])
        tab._on_nav_selection(wx.CommandEvent())
        tab._live_sport_cb.SetValue(False)
        tab._on_live_sport_changed(wx.CommandEvent())
        category_provider.live_error = RuntimeError("Nie udało się połączyć z Teleman.pl.")
        tab._live_sport_cb.SetValue(True)
        tab._on_live_sport_changed(wx.CommandEvent())
        _wait_until(lambda: not tab._live_sport_loading)
        assert not tab._live_sport_enabled
        assert not tab._live_sport_cb.IsChecked()
        assert static_source_key in tab._nav_index_by_key
        assert "Nie udało się połączyć" in tab._details.GetValue()

        category_provider.live_error = None
        category_provider.live_gate = threading.Event()
        tab._live_sport_cb.SetValue(True)
        tab._on_live_sport_changed(wx.CommandEvent())
        _wait_until(lambda: tab._live_sport_loading and category_provider.live_calls == 3)
        tab._nav.SetSelection(tab._nav_index_by_key[film_key])
        tab._on_nav_selection(wx.CommandEvent())
        film_details = tab._details.GetValue()
        category_provider.live_gate.set()
        _wait_until(lambda: not tab._live_sport_loading)
        assert tab._get_selected_nav_key() == film_key
        assert tab._details.GetValue() == film_details
        assert not tab._live_sport_cb.IsShown()
    finally:
        if tab is not None:
            dialog = tab._now_tv_dialog
            if dialog is not None:
                try:
                    if not dialog.IsBeingDeleted():
                        dialog.Close()
                except RuntimeError:
                    pass
        _pump_events()
        try:
            if not frame.IsBeingDeleted():
                frame.Destroy()
        except RuntimeError:
            pass
        _pump_events()
        del app
