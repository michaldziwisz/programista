from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import threading

import wx

from tvguide_app.core.hub_api import HubClient
from tvguide_app.core.models import ACCESSIBILITY_FEATURE_LABELS, AccessibilityFeature
from tvguide_app.core.prefetch import PrefetchStage, PrefetchUpdate
from tvguide_app.core.search_index import SearchIndex, SearchKind, SearchResult
from tvguide_app.core.settings import SearchKindFilters, SettingsStore


SEARCH_KIND_LABELS: dict[SearchKind, str] = {
    "tv": "Telewizja",
    "radio": "Radio",
    "tv_accessibility": "TV z udogodnieniami",
    "archive": "Archiwum",
}

PREFETCH_STAGE_LABELS: dict[PrefetchStage, str] = {
    "tv": "Telewizja",
    "tv_accessibility": "TV z udogodnieniami",
    "radio": "Radio",
    "archive": "Archiwum",
}


@dataclass(frozen=True)
class _UiFilters:
    tv: bool
    radio: bool
    tv_accessibility: bool
    archive: bool

    def selected(self) -> set[SearchKind]:
        selected: set[SearchKind] = set()
        if self.tv:
            selected.add("tv")
        if self.radio:
            selected.add("radio")
        if self.tv_accessibility:
            selected.add("tv_accessibility")
        if self.archive:
            selected.add("archive")
        return selected or {"tv", "radio", "tv_accessibility", "archive"}


class SearchTab(wx.Panel):
    def __init__(
        self,
        parent: wx.Window,
        status_bar: wx.StatusBar,
        *,
        settings_store: SettingsStore,
        search_index: SearchIndex,
        hub: HubClient | None = None,
        on_start_full_sync: Callable[[], None] | None = None,
        on_stop_full_sync: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, style=wx.TAB_TRAVERSAL)
        self._status_bar = status_bar
        self._settings_store = settings_store
        self._index = search_index
        self._hub = hub
        self._on_start_full_sync = on_start_full_sync
        self._on_stop_full_sync = on_stop_full_sync
        self._results: list[SearchResult] = []

        self._load_persisted_filters()
        self._build_ui()

    def _load_persisted_filters(self) -> None:
        persisted = self._settings_store.get_search_kind_filters()
        self._filters = _UiFilters(
            tv=bool(persisted.tv),
            radio=bool(persisted.radio),
            tv_accessibility=bool(persisted.tv_accessibility),
            archive=bool(persisted.archive),
        )

    def _persist_filters(self) -> None:
        try:
            self._settings_store.set_search_kind_filters(
                SearchKindFilters(
                    tv=self._filters.tv,
                    radio=self._filters.radio,
                    tv_accessibility=self._filters.tv_accessibility,
                    archive=self._filters.archive,
                )
            )
        except Exception:  # noqa: BLE001
            self._status_bar.SetStatusText("Nie udało się zapisać filtrów wyszukiwania.")

    def _build_ui(self) -> None:
        root = wx.BoxSizer(wx.VERTICAL)

        search_row = wx.BoxSizer(wx.HORIZONTAL)
        search_row.Add(wx.StaticText(self, label="Szukaj:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._query = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self._query.Bind(wx.EVT_TEXT_ENTER, lambda _evt: self._run_search())
        search_row.Add(self._query, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._search_btn = wx.Button(self, label="Szukaj")
        self._search_btn.Bind(wx.EVT_BUTTON, lambda _evt: self._run_search())
        search_row.Add(self._search_btn, 0, wx.ALIGN_CENTER_VERTICAL)

        root.Add(search_row, 0, wx.EXPAND | wx.ALL, 8)

        filters_box = wx.StaticBoxSizer(wx.StaticBox(self, label="Typy"), wx.HORIZONTAL)
        self._tv_cb = wx.CheckBox(self, label="Telewizja")
        self._radio_cb = wx.CheckBox(self, label="Radio")
        self._tv_a11y_cb = wx.CheckBox(self, label="TV z udogodnieniami")
        self._archive_cb = wx.CheckBox(self, label="Archiwum")

        self._tv_cb.SetValue(self._filters.tv)
        self._radio_cb.SetValue(self._filters.radio)
        self._tv_a11y_cb.SetValue(self._filters.tv_accessibility)
        self._archive_cb.SetValue(self._filters.archive)

        for cb in (self._tv_cb, self._radio_cb, self._tv_a11y_cb, self._archive_cb):
            cb.Bind(wx.EVT_CHECKBOX, self._on_filter_changed)
            filters_box.Add(cb, 0, wx.ALL, 4)

        root.Add(filters_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        hint = wx.StaticText(
            self,
            label="Wyszukiwanie online (jeśli dostępne) z fallbackiem na lokalny cache.",
        )
        root.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        sync_box = wx.StaticBoxSizer(wx.StaticBox(self, label="Pełna synchronizacja cache"), wx.VERTICAL)
        self._prefetch_status = wx.StaticText(self, label="Nie uruchomiono.")
        sync_box.Add(self._prefetch_status, 0, wx.ALL, 4)

        self._prefetch_gauge = wx.Gauge(self, range=100)
        sync_box.Add(self._prefetch_gauge, 0, wx.EXPAND | wx.ALL, 4)

        sync_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self._prefetch_start_btn = wx.Button(self, label="Pobierz wszystko")
        self._prefetch_stop_btn = wx.Button(self, label="Zatrzymaj")
        self._prefetch_stop_btn.Disable()
        self._prefetch_start_btn.Bind(wx.EVT_BUTTON, self._on_prefetch_start)
        self._prefetch_stop_btn.Bind(wx.EVT_BUTTON, self._on_prefetch_stop)
        if not callable(self._on_start_full_sync):
            self._prefetch_start_btn.Disable()
        sync_buttons.Add(self._prefetch_start_btn, 0, wx.RIGHT, 8)
        sync_buttons.Add(self._prefetch_stop_btn, 0)
        sync_box.Add(sync_buttons, 0, wx.ALL, 4)

        root.Add(sync_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self._list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.TAB_TRAVERSAL)
        self._list.InsertColumn(0, "Data", width=110)
        self._list.InsertColumn(1, "Od", width=70)
        self._list.InsertColumn(2, "Stacja", width=180)
        self._list.InsertColumn(3, "Tytuł", width=520)
        self._list.InsertColumn(4, "Typ", width=160)
        self._list.InsertColumn(5, "Udogodnienia", width=180)

        root.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.SetSizer(root)

    def update_prefetch(self, update: PrefetchUpdate, *, running: bool) -> None:
        stage = PREFETCH_STAGE_LABELS.get(update.stage, update.stage)
        if update.total:
            pct = int((update.done / update.total) * 100) if update.total else 0
            pct = max(0, min(100, pct))
            self._prefetch_gauge.SetValue(pct)
            self._prefetch_status.SetLabel(
                f"{stage}: {update.done}/{update.total} • błędy: {update.errors} • {update.message}"
            )
        else:
            if running:
                self._prefetch_gauge.Pulse()
            else:
                self._prefetch_gauge.SetValue(0)
            self._prefetch_status.SetLabel(f"{stage}: błędy: {update.errors} • {update.message}")

        self._prefetch_start_btn.Enable(not running)
        self._prefetch_stop_btn.Enable(running)

    def set_prefetch_running(self, running: bool) -> None:
        if not callable(self._on_start_full_sync):
            self._prefetch_start_btn.Enable(False)
            self._prefetch_stop_btn.Enable(False)
            return
        self._prefetch_start_btn.Enable(not running)
        self._prefetch_stop_btn.Enable(running)

    def _on_prefetch_start(self, _evt: wx.CommandEvent) -> None:
        if callable(self._on_start_full_sync):
            self._on_start_full_sync()

    def _on_prefetch_stop(self, _evt: wx.CommandEvent) -> None:
        if callable(self._on_stop_full_sync):
            self._on_stop_full_sync()

    def _on_filter_changed(self, _evt: wx.CommandEvent) -> None:
        self._filters = _UiFilters(
            tv=bool(self._tv_cb.IsChecked()),
            radio=bool(self._radio_cb.IsChecked()),
            tv_accessibility=bool(self._tv_a11y_cb.IsChecked()),
            archive=bool(self._archive_cb.IsChecked()),
        )
        self._persist_filters()

    def _run_search(self) -> None:
        query = self._query.GetValue()
        kinds = self._filters.selected()

        def work() -> tuple[str, list[SearchResult], str | None]:
            if self._hub:
                try:
                    results = self._hub.search(query, kinds=kinds, limit=200)
                    return ("online", results, None)
                except Exception as e:  # noqa: BLE001
                    # Fallback to local cache if hub is unavailable or auth fails.
                    results = self._index.search(query, kinds=kinds)
                    return ("local", results, str(e) or "Błąd wyszukiwania online.")

            results = self._index.search(query, kinds=kinds)
            return ("local", results, None)

        def on_success(result: tuple[str, list[SearchResult], str | None]) -> None:
            mode, results, warn = result
            self._results = results
            self._list.DeleteAllItems()
            for idx, r in enumerate(self._results):
                row = self._list.InsertItem(idx, r.day.isoformat())
                self._list.SetItem(row, 1, r.start)
                self._list.SetItem(row, 2, r.source_name)
                self._list.SetItem(row, 3, r.title)
                self._list.SetItem(row, 4, SEARCH_KIND_LABELS.get(r.kind, r.kind))
                self._list.SetItem(row, 5, _format_accessibility(r.accessibility))

            if not self._results and query.strip():
                base = "Brak wyników"
                suffix = " (online)." if mode == "online" else " (w pobranych danych)."
                self._status_bar.SetStatusText(base + suffix)
            else:
                prefix = "Wyniki (online)" if mode == "online" else "Wyniki (lokalnie)"
                self._status_bar.SetStatusText(f"{prefix}: {len(self._results)}")
                if warn and mode != "online":
                    self._status_bar.SetStatusText(f"{prefix}: {len(self._results)} • {warn}")

            self._search_btn.Enable(True)

        def on_error(exc: Exception) -> None:
            self._search_btn.Enable(True)
            msg = str(exc) or "Nieznany błąd."
            self._status_bar.SetStatusText(f"Błąd wyszukiwania: {msg}")

        self._search_btn.Enable(False)
        self._status_bar.SetStatusText("Wyszukiwanie…")
        self._run_in_thread(work, on_success=on_success, on_error=on_error)

    def _run_in_thread(
        self,
        work,
        *,
        on_success,
        on_error,
    ) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                wx.CallAfter(on_error, e)
                return
            wx.CallAfter(on_success, result)

        threading.Thread(target=runner, daemon=True).start()


def _format_accessibility(features: tuple[AccessibilityFeature, ...]) -> str:
    if not features:
        return ""
    labels: list[str] = []
    for f in features:
        labels.append(ACCESSIBILITY_FEATURE_LABELS.get(f, str(f)))
    return ", ".join(labels)
