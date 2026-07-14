from __future__ import annotations

from datetime import date
import time as time_module

import pytest


wx = pytest.importorskip("wx", reason="test regresyjny wymaga wxPython")

from tvguide_app.core.models import ProviderId, Source, SourceId  # noqa: E402
from tvguide_app.gui.schedule_tabs import TvTab  # noqa: E402


TEST_DAY = date(2026, 7, 14)
TEST_SOURCE = Source(ProviderId("fake"), SourceId("channel"), "Kanał testowy")


class _ScheduleProvider:
    provider_id = ProviderId("fake")
    display_name = "Fake"

    def __init__(self) -> None:
        self.schedule_calls = 0

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        return [TEST_SOURCE]

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        return [TEST_DAY]

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list:
        self.schedule_calls += 1
        return []


class _NowTvProvider:
    def __init__(self) -> None:
        self.calls = 0

    def get_now_schedule(self, *, force_refresh: bool = False) -> list:
        self.calls += 1
        return []


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


def _select_source_and_day(tab: TvTab, provider: _ScheduleProvider) -> None:
    previous_calls = provider.schedule_calls
    source_key = tab._source_key(TEST_SOURCE)
    tab._set_nav_expanded(source_key, expanded=True)
    pair_key = tab._pair_key(TEST_SOURCE, TEST_DAY)
    tab._set_nav_selection(tab._nav_index_by_key[pair_key])
    _wait_until(lambda: provider.schedule_calls > previous_calls)


def _activate(button: wx.Button) -> None:
    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, button.GetId())
    event.SetEventObject(button)
    assert button.GetEventHandler().ProcessEvent(event)


def _close_if_alive(dialog) -> None:
    if dialog is None:
        return
    try:
        if not dialog.IsBeingDeleted():
            dialog.Close()
    except RuntimeError:
        pass


def test_now_tv_dialog_can_be_reopened_after_selecting_channel_and_day() -> None:
    app = wx.App.Get() or wx.App(False)
    frame = wx.Frame(None, title="Test Teraz w TV", size=(900, 600))
    tab = None

    try:
        status = frame.CreateStatusBar()
        schedule_provider = _ScheduleProvider()
        now_provider = _NowTvProvider()
        tab = TvTab(
            frame,
            schedule_provider,
            status,
            favorites_store=object(),
            on_favorites_changed=lambda: None,
            now_tv_provider=now_provider,
        )
        frame.Show()

        _wait_until(lambda: tab._nav.GetCount() > 0)
        _select_source_and_day(tab, schedule_provider)

        _activate(tab._now_tv_button)
        _wait_until(lambda: now_provider.calls == 1)
        first_dialog = tab._now_tv_dialog
        assert first_dialog is not None
        assert first_dialog.IsShown()

        first_dialog.Close()
        _wait_until(lambda: tab._now_tv_dialog is None)

        _select_source_and_day(tab, schedule_provider)
        _activate(tab._now_tv_button)
        _wait_until(lambda: now_provider.calls == 2)
        second_dialog = tab._now_tv_dialog
        assert second_dialog is not None
        assert second_dialog is not first_dialog
        assert second_dialog.IsShown()
    finally:
        if tab is not None:
            _close_if_alive(tab._now_tv_dialog)
        _pump_events()
        try:
            if not frame.IsBeingDeleted():
                frame.Destroy()
        except RuntimeError:
            pass
        _pump_events()
        del app
