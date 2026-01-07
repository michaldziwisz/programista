from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal

import wx

from tvguide_app.core.favorites import FavoriteKind, FavoriteRef, FavoritesStore, decode_favorite_source_id
from tvguide_app.core.models import ACCESSIBILITY_FEATURE_LABELS, AccessibilityFeature, ScheduleItem, Source
from tvguide_app.core.providers.archive_base import ArchiveProvider
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.util import POLISH_MONTHS_NOMINATIVE


ViewMode = Literal["by_source", "by_day"]


@dataclass(frozen=True)
class NodeData:
    kind: Literal["pair", "source", "day"]
    source: Source | None = None
    day: date | None = None


@dataclass(frozen=True)
class NavRow:
    key: str
    parent_key: str | None
    level: int
    label: str
    data: NodeData
    expandable: bool
    expanded: bool


class BaseScheduleTab(wx.Panel):
    def __init__(self, parent: wx.Window, provider: ScheduleProvider, status_bar: wx.StatusBar) -> None:
        super().__init__(parent, style=wx.TAB_TRAVERSAL)
        self._provider = provider
        self._status_bar = status_bar
        self._view_mode: ViewMode = "by_source"
        self._request_token = 0
        self._show_end_time = True

        self._sources: list[Source] = []
        self._days: list[date] = []
        self._nav_rows: list[NavRow] = []
        self._nav_index_by_key: dict[str, int] = {}
        self._expanded_by_source: set[str] = set()
        self._expanded_by_day: set[str] = set()
        self._suppress_nav_event = False

        self._build_ui()
        self.refresh_all(force=False)

    def _after_nav_update(self) -> None:
        return

    def _build_ui(self) -> None:
        root = wx.BoxSizer(wx.VERTICAL)

        header = wx.BoxSizer(wx.HORIZONTAL)
        header.Add(wx.StaticText(self, label="Widok:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self._view_choice = wx.Choice(self, choices=["wg kanałów", "wg dni"])
        self._view_choice.SetSelection(0)
        self._view_choice.Bind(wx.EVT_CHOICE, self._on_view_choice)
        header.Add(self._view_choice, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        self._create_header_controls(header)

        root.Add(header, 0, wx.EXPAND | wx.ALL, 8)

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE | wx.TAB_TRAVERSAL)
        splitter.SetMinimumPaneSize(200)

        left = wx.Panel(splitter, style=wx.TAB_TRAVERSAL)
        mid = wx.Panel(splitter, style=wx.TAB_TRAVERSAL)
        splitter.SplitVertically(left, mid, 320)

        left_sizer = wx.BoxSizer(wx.VERTICAL)
        left_sizer.Add(wx.StaticText(left, label="Nawigacja:"), 0, wx.BOTTOM, 4)
        self._nav = wx.ListBox(left, style=wx.LB_SINGLE | wx.LB_HSCROLL)
        self._nav.Bind(wx.EVT_LISTBOX, self._on_nav_selection)
        self._nav.Bind(wx.EVT_LISTBOX_DCLICK, self._on_nav_activate)
        self._nav.Bind(wx.EVT_KEY_DOWN, self._on_nav_key_down)
        self._nav.Bind(wx.EVT_SET_FOCUS, self._on_nav_focus)
        left_sizer.Add(self._nav, 1, wx.EXPAND)
        left.SetSizer(left_sizer)

        mid_splitter = wx.SplitterWindow(mid, style=wx.SP_LIVE_UPDATE | wx.TAB_TRAVERSAL)
        mid_splitter.SetMinimumPaneSize(140)

        list_panel = wx.Panel(mid_splitter, style=wx.TAB_TRAVERSAL)
        details_panel = wx.Panel(mid_splitter, style=wx.TAB_TRAVERSAL)
        mid_splitter.SplitHorizontally(list_panel, details_panel, 320)

        list_sizer = wx.BoxSizer(wx.VERTICAL)
        self._list = wx.ListCtrl(list_panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self._init_list_columns(self._list)
        self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_item_selected)
        list_sizer.Add(self._list, 1, wx.EXPAND)
        list_panel.SetSizer(list_sizer)

        details_sizer = wx.BoxSizer(wx.VERTICAL)
        details_sizer.Add(wx.StaticText(details_panel, label="Szczegóły:"), 0, wx.BOTTOM, 4)
        self._details = wx.TextCtrl(
            details_panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
        )
        self._details.Bind(wx.EVT_KEY_DOWN, self._on_last_control_key_down)
        details_sizer.Add(self._details, 1, wx.EXPAND)
        details_panel.SetSizer(details_sizer)

        mid_sizer = wx.BoxSizer(wx.VERTICAL)
        mid_sizer.Add(mid_splitter, 1, wx.EXPAND)
        mid.SetSizer(mid_sizer)

        root.Add(splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(root)

    def _on_last_control_key_down(self, evt: wx.KeyEvent) -> None:
        if (
            evt.GetKeyCode() == wx.WXK_TAB
            and not evt.ShiftDown()
            and not evt.ControlDown()
            and not evt.AltDown()
        ):
            parent = self.GetParent()
            if isinstance(parent, wx.Notebook):
                parent.SetFocus()
                return
        evt.Skip()

    def _create_header_controls(self, _header: wx.BoxSizer) -> None:
        return

    def _init_list_columns(self, list_ctrl: wx.ListCtrl) -> None:
        self._show_end_time = True
        list_ctrl.InsertColumn(0, "Od", width=70)
        list_ctrl.InsertColumn(1, "Do", width=70)
        list_ctrl.InsertColumn(2, "Tytuł", width=420)

    def refresh(self, *, force: bool) -> None:
        self._refresh_schedule(force=force)

    def refresh_all(self, *, force: bool) -> None:
        self._status_bar.SetStatusText("Ładowanie listy kanałów/dni…")
        self._run_in_thread(
            lambda: (self._provider.list_sources(force_refresh=force), self._provider.list_days(force_refresh=force)),
            on_success=self._on_loaded_sources_days,
            on_error=self._on_error,
        )

    def _on_loaded_sources_days(self, result: tuple[list[Source], list[date]]) -> None:
        sources, days = result
        self._sources = sources
        self._days = days
        self._rebuild_nav()
        self._status_bar.SetStatusText("Gotowe.")

    def _source_key(self, source: Source) -> str:
        return f"src:{source.provider_id}:{source.id}"

    @staticmethod
    def _day_key(day: date) -> str:
        return f"day:{day.isoformat()}"

    def _pair_key(self, source: Source, day: date) -> str:
        return f"pair:{source.provider_id}:{source.id}:{day.isoformat()}"

    def _on_view_choice(self, _evt: wx.CommandEvent) -> None:
        self._view_mode = "by_source" if self._view_choice.GetSelection() == 0 else "by_day"
        self._rebuild_nav()

    def _nav_root_sources(self) -> list[Source]:
        return self._sources

    def _nav_root_days(self) -> list[date]:
        return self._days

    def _nav_child_days_for_source(self, _source: Source) -> list[date]:
        return self._days

    def _nav_child_sources_for_day(self, _day: date) -> list[Source]:
        return self._sources

    def _rebuild_nav(self) -> None:
        selected_key = self._get_selected_nav_key()

        rows: list[NavRow] = []
        if self._view_mode == "by_source":
            for src in self._nav_root_sources():
                src_key = self._source_key(src)
                expanded = src_key in self._expanded_by_source
                rows.append(
                    NavRow(
                        key=src_key,
                        parent_key=None,
                        level=0,
                        label=src.name,
                        data=NodeData(kind="source", source=src),
                        expandable=True,
                        expanded=expanded,
                    )
                )
                if expanded:
                    for d in self._nav_child_days_for_source(src):
                        rows.append(
                            NavRow(
                                key=self._pair_key(src, d),
                                parent_key=src_key,
                                level=1,
                                label=d.isoformat(),
                                data=NodeData(kind="pair", source=src, day=d),
                                expandable=False,
                                expanded=False,
                            )
                        )
        else:
            for d in self._nav_root_days():
                day_key = self._day_key(d)
                expanded = day_key in self._expanded_by_day
                rows.append(
                    NavRow(
                        key=day_key,
                        parent_key=None,
                        level=0,
                        label=d.isoformat(),
                        data=NodeData(kind="day", day=d),
                        expandable=True,
                        expanded=expanded,
                    )
                )
                if expanded:
                    for src in self._nav_child_sources_for_day(d):
                        rows.append(
                            NavRow(
                                key=self._pair_key(src, d),
                                parent_key=day_key,
                                level=1,
                                label=src.name,
                                data=NodeData(kind="pair", source=src, day=d),
                                expandable=False,
                                expanded=False,
                            )
                        )

        self._nav_rows = rows
        self._nav_index_by_key = {row.key: idx for idx, row in enumerate(rows)}

        def format_row(row: NavRow) -> str:
            text = ("  " * row.level) + row.label
            if row.expandable:
                text += " (rozwinięte)" if row.expanded else " (zwinięte)"
            return text

        self._suppress_nav_event = True
        self._nav.Freeze()
        self._nav.Clear()
        for row in rows:
            self._nav.Append(format_row(row), row.key)
        self._nav.Thaw()

        if rows:
            idx = self._nav_index_by_key.get(selected_key, 0) if selected_key else 0
            idx = max(0, min(idx, len(rows) - 1))
            self._nav.SetSelection(idx)
            self._nav.EnsureVisible(idx)
        self._suppress_nav_event = False
        self._after_nav_update()

    def _select_first_nav_item(self) -> None:
        if self._nav.GetCount() == 0:
            return
        self._nav.SetSelection(0)
        self._nav.EnsureVisible(0)

    def _get_selected_source(self) -> Source | None:
        row = self._get_selected_nav_row()
        data = row.data if row else None
        if not data or data.kind not in ("source", "pair") or not data.source:
            return None
        return data.source

    def _get_selected_nav_key(self) -> str | None:
        idx = self._nav.GetSelection()
        if idx == wx.NOT_FOUND:
            return None
        key = self._nav.GetClientData(idx)
        return key if isinstance(key, str) else None

    def _get_selected_nav_row(self) -> NavRow | None:
        key = self._get_selected_nav_key()
        if not key:
            return None
        idx = self._nav_index_by_key.get(key)
        if idx is None:
            return None
        return self._nav_rows[idx]

    def _on_nav_focus(self, evt: wx.FocusEvent) -> None:
        if self._nav.GetSelection() == wx.NOT_FOUND and self._nav.GetCount() > 0:
            self._select_first_nav_item()
            self._after_nav_update()
        evt.Skip()

    def _on_nav_selection(self, _evt: wx.CommandEvent) -> None:
        if self._suppress_nav_event:
            return
        row = self._get_selected_nav_row()
        if not row or row.data.kind != "pair" or not row.data.source or not row.data.day:
            return
        self._load_schedule(row.data.source, row.data.day, force=False)

    def _on_nav_activate(self, _evt: wx.CommandEvent) -> None:
        self._toggle_or_load_selected()

    def _on_nav_key_down(self, evt: wx.KeyEvent) -> None:
        key = evt.GetKeyCode()
        if key == wx.WXK_RIGHT:
            if self._expand_selected():
                return
            # Tree semantics: if nothing to expand, do nothing (don't move selection).
            return
        elif key == wx.WXK_LEFT:
            if self._collapse_selected():
                return
            # Tree semantics: if nothing to collapse/go up to, do nothing (don't move selection).
            return
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER, wx.WXK_SPACE):
            self._toggle_or_load_selected()
            return
        elif key == wx.WXK_HOME:
            self._select_first_nav_item()
            self._after_nav_update()
            return
        elif key == wx.WXK_END:
            count = self._nav.GetCount()
            if count:
                self._nav.SetSelection(count - 1)
                self._nav.EnsureVisible(count - 1)
                self._after_nav_update()
            return
        evt.Skip()

    def _toggle_or_load_selected(self) -> None:
        row = self._get_selected_nav_row()
        if not row:
            return
        if row.expandable:
            self._set_nav_expanded(row.key, expanded=not row.expanded)
            return
        if row.data.kind == "pair" and row.data.source and row.data.day:
            self._load_schedule(row.data.source, row.data.day, force=False)

    def _expand_selected(self) -> bool:
        row = self._get_selected_nav_row()
        if not row or not row.expandable:
            return False
        if not row.expanded:
            self._set_nav_expanded(row.key, expanded=True)
            return True
        child_idx = self._find_first_child_index(row.key)
        if child_idx is None:
            return False
        self._nav.SetSelection(child_idx)
        self._nav.EnsureVisible(child_idx)
        self._after_nav_update()
        return True

    def _collapse_selected(self) -> bool:
        row = self._get_selected_nav_row()
        if not row:
            return False
        if row.expandable and row.expanded:
            self._set_nav_expanded(row.key, expanded=False)
            return True
        if row.parent_key:
            parent_idx = self._nav_index_by_key.get(row.parent_key)
            if parent_idx is None:
                return False
            self._nav.SetSelection(parent_idx)
            self._nav.EnsureVisible(parent_idx)
            self._after_nav_update()
            return True
        return False

    def _set_nav_expanded(self, key: str, *, expanded: bool) -> None:
        if key.startswith("src:"):
            if expanded:
                self._expanded_by_source.add(key)
            else:
                self._expanded_by_source.discard(key)
        elif key.startswith("day:"):
            if expanded:
                self._expanded_by_day.add(key)
            else:
                self._expanded_by_day.discard(key)
        self._rebuild_nav()

    def _find_first_child_index(self, parent_key: str) -> int | None:
        parent_idx = self._nav_index_by_key.get(parent_key)
        if parent_idx is None:
            return None
        parent_level = self._nav_rows[parent_idx].level
        for idx in range(parent_idx + 1, len(self._nav_rows)):
            row = self._nav_rows[idx]
            if row.level <= parent_level:
                break
            if row.parent_key == parent_key:
                return idx
        return None

    def _load_schedule(self, source: Source, day: date, *, force: bool) -> None:
        self._request_token += 1
        token = self._request_token

        self._list.DeleteAllItems()
        self._details.SetValue("")
        self._status_bar.SetStatusText(f"Pobieranie: {source.name} {day.isoformat()}…")

        def work() -> list[ScheduleItem]:
            return self._provider.get_schedule(source, day, force_refresh=force)

        def on_success(items: list[ScheduleItem]) -> None:
            if token != self._request_token:
                return
            self._show_schedule(items)
            self._status_bar.SetStatusText("Gotowe.")

        self._run_in_thread(work, on_success=on_success, on_error=self._on_error)

    def _refresh_schedule(self, *, force: bool) -> None:
        row = self._get_selected_nav_row()
        data = row.data if row else None
        if not isinstance(data, NodeData) or data.kind != "pair" or not data.source or not data.day:
            return
        self._load_schedule(data.source, data.day, force=force)

    def _show_schedule(self, items: list[ScheduleItem]) -> None:
        self._items = items
        self._list.DeleteAllItems()
        for idx, it in enumerate(items):
            start = it.start_time.strftime("%H:%M") if it.start_time else ""
            title = it.title
            row = self._list.InsertItem(idx, start)
            col = 1
            if self._show_end_time:
                end = it.end_time.strftime("%H:%M") if it.end_time else ""
                self._list.SetItem(row, col, end)
                col += 1
            self._list.SetItem(row, col, title)

    def _on_item_selected(self, evt: wx.ListEvent) -> None:
        idx = evt.GetIndex()
        if idx < 0:
            return
        if not hasattr(self, "_items") or idx >= len(self._items):
            return
        item = self._items[idx]
        self._details.SetValue(item.details_summary or "Ładowanie szczegółów…")
        self._run_in_thread(
            lambda: self._provider.get_item_details(item, force_refresh=False),
            on_success=lambda text: self._details.SetValue(text),
            on_error=self._on_error,
        )

    def _run_in_thread(
        self,
        work: Callable[[], Any],
        *,
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                wx.CallAfter(on_error, e)
                return
            wx.CallAfter(on_success, result)

        threading.Thread(target=runner, daemon=True).start()

    def _on_error(self, exc: Exception) -> None:
        msg = str(exc) or "Nieznany błąd."
        self._status_bar.SetStatusText("Błąd.")
        # For accessibility, avoid modal dialogs; show the error in the read-only details pane.
        self._details.SetValue(f"Błąd:\n{msg}")


class TvTab(BaseScheduleTab):
    def __init__(
        self,
        parent: wx.Window,
        provider: ScheduleProvider,
        status_bar: wx.StatusBar,
        *,
        favorites_store: FavoritesStore,
        on_favorites_changed: Callable[[], None],
    ) -> None:
        self._favorites_store = favorites_store
        self._on_favorites_changed = on_favorites_changed
        self._favorite_kind: FavoriteKind = "tv"
        self._suppress_favorite_event = False
        super().__init__(parent, provider, status_bar)

    def _create_header_controls(self, header: wx.BoxSizer) -> None:
        header.AddStretchSpacer(1)
        self._favorite_checkbox = wx.CheckBox(self, label="Ulubione")
        self._favorite_checkbox.Disable()
        self._favorite_checkbox.Bind(wx.EVT_CHECKBOX, self._on_favorite_checkbox)
        header.Add(self._favorite_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)

    def _init_list_columns(self, list_ctrl: wx.ListCtrl) -> None:
        self._show_end_time = False
        list_ctrl.InsertColumn(0, "Od", width=70)
        list_ctrl.InsertColumn(1, "Tytuł", width=520)

    def _after_nav_update(self) -> None:
        self.sync_favorites()

    def _on_nav_selection(self, evt: wx.CommandEvent) -> None:
        super()._on_nav_selection(evt)
        self.sync_favorites()

    def sync_favorites(self) -> None:
        if not hasattr(self, "_favorite_checkbox"):
            return
        src = self._get_selected_source()
        self._suppress_favorite_event = True
        try:
            if not src:
                self._favorite_checkbox.SetValue(False)
                self._favorite_checkbox.Disable()
                return

            self._favorite_checkbox.Enable()
            ref = FavoriteRef(kind=self._favorite_kind, provider_id=str(src.provider_id), source_id=str(src.id))
            self._favorite_checkbox.SetValue(self._favorites_store.is_favorite(ref))
        finally:
            self._suppress_favorite_event = False

    def _on_favorite_checkbox(self, evt: wx.CommandEvent) -> None:
        if self._suppress_favorite_event:
            return
        src = self._get_selected_source()
        if not src:
            return

        ref = FavoriteRef(kind=self._favorite_kind, provider_id=str(src.provider_id), source_id=str(src.id))
        checked = bool(evt.IsChecked())

        if checked:
            changed = self._favorites_store.add_source(self._favorite_kind, src)
            if changed:
                self._status_bar.SetStatusText(f"Dodano do ulubionych: {src.name}")
        else:
            changed = self._favorites_store.remove(ref)
            if changed:
                self._status_bar.SetStatusText(f"Usunięto z ulubionych: {src.name}")

        if changed:
            self._on_favorites_changed()


class TvAccessibilityTab(BaseScheduleTab):
    def __init__(self, parent: wx.Window, provider: ScheduleProvider, status_bar: wx.StatusBar) -> None:
        self._filter_ad = True
        self._filter_jm = True
        self._filter_n = True
        self._all_items: list[ScheduleItem] = []
        self._a11y_nav_token = 0
        self._a11y_signature: tuple[AccessibilityFeature, ...] = ("AD", "JM", "N")
        self._a11y_pair_status: dict[str, bool | None] = {}
        self._a11y_available_sources: set[str] = set()
        self._a11y_available_days: set[str] = set()
        self._a11y_days_for_source: dict[str, list[date]] = {}
        self._a11y_sources_for_day: dict[str, list[Source]] = {}
        super().__init__(parent, provider, status_bar)

    def _create_header_controls(self, header: wx.BoxSizer) -> None:
        header.AddStretchSpacer(1)
        header.Add(wx.StaticText(self, label="Udogodnienia:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self._ad_cb = wx.CheckBox(self, label="Audiodeskrypcja")
        self._ad_cb.SetValue(True)
        self._ad_cb.Bind(wx.EVT_CHECKBOX, self._on_filter_changed)
        header.Add(self._ad_cb, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._jm_cb = wx.CheckBox(self, label="Język migowy")
        self._jm_cb.SetValue(True)
        self._jm_cb.Bind(wx.EVT_CHECKBOX, self._on_filter_changed)
        header.Add(self._jm_cb, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._n_cb = wx.CheckBox(self, label="Napisy")
        self._n_cb.SetValue(True)
        self._n_cb.Bind(wx.EVT_CHECKBOX, self._on_filter_changed)
        header.Add(self._n_cb, 0, wx.ALIGN_CENTER_VERTICAL)

    def _init_list_columns(self, list_ctrl: wx.ListCtrl) -> None:
        self._show_end_time = False
        list_ctrl.InsertColumn(0, "Od", width=70)
        list_ctrl.InsertColumn(1, "Tytuł", width=420)
        list_ctrl.InsertColumn(2, "Udogodnienia", width=240)

    def _on_loaded_sources_days(self, result: tuple[list[Source], list[date]]) -> None:
        sources, days = result
        self._sources = sources
        self._days = days
        self._start_a11y_nav_refresh()

    def _on_filter_changed(self, _evt: wx.CommandEvent) -> None:
        if hasattr(self, "_ad_cb"):
            self._filter_ad = bool(self._ad_cb.IsChecked())
        if hasattr(self, "_jm_cb"):
            self._filter_jm = bool(self._jm_cb.IsChecked())
        if hasattr(self, "_n_cb"):
            self._filter_n = bool(self._n_cb.IsChecked())
        self._clear_schedule_view()
        self._start_a11y_nav_refresh()

    def _show_schedule(self, items: list[ScheduleItem]) -> None:
        self._all_items = list(items)
        self._apply_filters()

    def _clear_schedule_view(self) -> None:
        self._all_items = []
        self._items = []
        if hasattr(self, "_list"):
            self._list.DeleteAllItems()
        if hasattr(self, "_details"):
            self._details.SetValue("")

    def _apply_filters(self) -> None:
        selected: set[AccessibilityFeature] = set()
        if self._filter_ad:
            selected.add("AD")
        if self._filter_jm:
            selected.add("JM")
        if self._filter_n:
            selected.add("N")

        if not selected:
            selected = {"AD", "JM", "N"}

        filtered: list[ScheduleItem] = []
        for it in self._all_items:
            if not it.accessibility:
                continue
            if any(f in selected for f in it.accessibility):
                filtered.append(it)

        self._items = filtered
        self._list.DeleteAllItems()
        for idx, it in enumerate(filtered):
            start = it.start_time.strftime("%H:%M") if it.start_time else ""
            row = self._list.InsertItem(idx, start)
            self._list.SetItem(row, 1, it.title)
            self._list.SetItem(row, 2, _format_accessibility(it.accessibility))

        if not filtered and self._all_items:
            self._details.SetValue("Brak programów spełniających wybrane udogodnienia.")

    def _start_a11y_nav_refresh(self) -> None:
        self._a11y_nav_token += 1
        token = self._a11y_nav_token

        self._a11y_signature = self._current_a11y_signature()
        self._a11y_pair_status = {}
        self._a11y_available_sources = set()
        self._a11y_available_days = set()
        self._a11y_days_for_source = {}
        self._a11y_sources_for_day = {}
        self._expanded_by_source.clear()
        self._expanded_by_day.clear()

        if hasattr(self, "_nav"):
            self._nav.Disable()
            self._nav.Clear()
        if hasattr(self, "_view_choice"):
            self._view_choice.Disable()

        self._status_bar.SetStatusText("Analizowanie udogodnień…")

        signature = tuple(self._a11y_signature)
        sources = list(self._sources)
        days = list(self._days)

        def work() -> tuple[set[str], set[str], dict[str, bool | None]]:
            return self._compute_a11y_availability(sources, days, signature)

        def on_success(result: tuple[set[str], set[str], dict[str, bool | None]]) -> None:
            if token != self._a11y_nav_token:
                return

            available_sources, available_days, pair_status = result
            self._a11y_available_sources = available_sources
            self._a11y_available_days = available_days
            self._a11y_pair_status = pair_status

            self._nav.Enable()
            self._view_choice.Enable()
            self._rebuild_nav()
            self._status_bar.SetStatusText("Gotowe.")

        def on_error(exc: Exception) -> None:
            if token != self._a11y_nav_token:
                return
            self._nav.Enable()
            self._view_choice.Enable()
            self._on_error(exc)

        self._run_in_thread(work, on_success=on_success, on_error=on_error)

    def _compute_a11y_availability(
        self,
        sources: list[Source],
        days: list[date],
        signature: tuple[AccessibilityFeature, ...],
    ) -> tuple[set[str], set[str], dict[str, bool | None]]:
        selected = set(signature) if signature else {"AD", "JM", "N"}
        pair_status: dict[str, bool | None] = {}

        def status_for(src: Source, d: date) -> bool | None:
            key = self._pair_key(src, d)
            if key in pair_status:
                return pair_status[key]
            try:
                items = self._provider.get_schedule(src, d, force_refresh=False)
            except Exception:  # noqa: BLE001
                pair_status[key] = None
                return None
            ok = False
            for it in items:
                if not it.accessibility:
                    continue
                if any(f in selected for f in it.accessibility):
                    ok = True
                    break
            pair_status[key] = ok
            return ok

        available_sources: set[str] = set()
        for src in sources:
            unknown = False
            for d in days:
                st = status_for(src, d)
                if st is True:
                    available_sources.add(self._source_key(src))
                    break
                if st is None:
                    unknown = True
            else:
                if unknown:
                    # Be conservative: keep the node visible if we couldn't verify.
                    available_sources.add(self._source_key(src))

        available_days: set[str] = set()
        for d in days:
            unknown = False
            for src in sources:
                st = status_for(src, d)
                if st is True:
                    available_days.add(self._day_key(d))
                    break
                if st is None:
                    unknown = True
            else:
                if unknown:
                    available_days.add(self._day_key(d))

        return available_sources, available_days, pair_status

    def _current_a11y_signature(self) -> tuple[AccessibilityFeature, ...]:
        selected: list[AccessibilityFeature] = []
        if self._filter_ad:
            selected.append("AD")
        if self._filter_jm:
            selected.append("JM")
        if self._filter_n:
            selected.append("N")
        if not selected:
            selected = ["AD", "JM", "N"]
        return tuple(selected)

    def _nav_root_sources(self) -> list[Source]:
        if not self._a11y_available_sources:
            return []
        return [s for s in self._sources if self._source_key(s) in self._a11y_available_sources]

    def _nav_root_days(self) -> list[date]:
        if not self._a11y_available_days:
            return []
        return [d for d in self._days if self._day_key(d) in self._a11y_available_days]

    def _nav_child_days_for_source(self, source: Source) -> list[date]:
        return self._a11y_days_for_source.get(self._source_key(source), [])

    def _nav_child_sources_for_day(self, day: date) -> list[Source]:
        return self._a11y_sources_for_day.get(self._day_key(day), [])

    def _set_nav_expanded(self, key: str, *, expanded: bool) -> None:
        if not expanded:
            super()._set_nav_expanded(key, expanded=False)
            return

        token = self._a11y_nav_token

        if key.startswith("src:") and key not in self._a11y_days_for_source:
            src = self._source_from_key(key)
            if not src:
                super()._set_nav_expanded(key, expanded=True)
                return
            self._nav.Disable()
            self._status_bar.SetStatusText(f"Sprawdzanie: {src.name}…")
            signature = tuple(self._a11y_signature)
            pair_cache = self._a11y_pair_status

            def work() -> list[date]:
                return self._compute_days_for_source(src, signature, pair_cache)

            def on_success(days: list[date]) -> None:
                if token != self._a11y_nav_token:
                    return
                self._nav.Enable()
                self._a11y_days_for_source[key] = days
                if not days:
                    self._a11y_available_sources.discard(key)
                    self._rebuild_nav()
                    self._status_bar.SetStatusText("Brak programów spełniających filtry.")
                    return
                BaseScheduleTab._set_nav_expanded(self, key, expanded=True)
                self._status_bar.SetStatusText("Gotowe.")

            def on_error(exc: Exception) -> None:
                if token != self._a11y_nav_token:
                    return
                self._nav.Enable()
                self._on_error(exc)

            self._run_in_thread(work, on_success=on_success, on_error=on_error)
            return

        if key.startswith("day:") and key not in self._a11y_sources_for_day:
            d = self._day_from_key(key)
            if not d:
                super()._set_nav_expanded(key, expanded=True)
                return
            self._nav.Disable()
            self._status_bar.SetStatusText(f"Sprawdzanie: {d.isoformat()}…")
            signature = tuple(self._a11y_signature)
            pair_cache = self._a11y_pair_status

            def work() -> list[Source]:
                return self._compute_sources_for_day(d, signature, pair_cache)

            def on_success(sources: list[Source]) -> None:
                if token != self._a11y_nav_token:
                    return
                self._nav.Enable()
                self._a11y_sources_for_day[key] = sources
                if not sources:
                    self._a11y_available_days.discard(key)
                    self._rebuild_nav()
                    self._status_bar.SetStatusText("Brak programów spełniających filtry.")
                    return
                BaseScheduleTab._set_nav_expanded(self, key, expanded=True)
                self._status_bar.SetStatusText("Gotowe.")

            def on_error(exc: Exception) -> None:
                if token != self._a11y_nav_token:
                    return
                self._nav.Enable()
                self._on_error(exc)

            self._run_in_thread(work, on_success=on_success, on_error=on_error)
            return

        super()._set_nav_expanded(key, expanded=True)

    def _source_from_key(self, key: str) -> Source | None:
        if not key.startswith("src:"):
            return None
        try:
            _, provider_id, source_id = key.split(":", 2)
        except ValueError:
            return None
        for src in self._sources:
            if str(src.provider_id) == provider_id and str(src.id) == source_id:
                return src
        return None

    @staticmethod
    def _day_from_key(key: str) -> date | None:
        if not key.startswith("day:"):
            return None
        value = key.split(":", 1)[1]
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def _pair_status_for(
        self,
        src: Source,
        d: date,
        signature: tuple[AccessibilityFeature, ...],
        pair_cache: dict[str, bool | None],
    ) -> bool | None:
        selected = set(signature) if signature else {"AD", "JM", "N"}
        key = self._pair_key(src, d)
        if key in pair_cache:
            return pair_cache[key]
        try:
            items = self._provider.get_schedule(src, d, force_refresh=False)
        except Exception:  # noqa: BLE001
            pair_cache[key] = None
            return None
        ok = False
        for it in items:
            if not it.accessibility:
                continue
            if any(f in selected for f in it.accessibility):
                ok = True
                break
        pair_cache[key] = ok
        return ok

    def _compute_days_for_source(
        self,
        src: Source,
        signature: tuple[AccessibilityFeature, ...],
        pair_cache: dict[str, bool | None],
    ) -> list[date]:
        allowed_days = [d for d in self._days if self._day_key(d) in self._a11y_available_days]
        out: list[date] = []
        for d in allowed_days:
            st = self._pair_status_for(src, d, signature, pair_cache)
            if st is True or st is None:
                out.append(d)
        return out

    def _compute_sources_for_day(
        self,
        d: date,
        signature: tuple[AccessibilityFeature, ...],
        pair_cache: dict[str, bool | None],
    ) -> list[Source]:
        allowed_sources = [s for s in self._sources if self._source_key(s) in self._a11y_available_sources]
        out: list[Source] = []
        for src in allowed_sources:
            st = self._pair_status_for(src, d, signature, pair_cache)
            if st is True or st is None:
                out.append(src)
        return out


def _format_accessibility(features: tuple[AccessibilityFeature, ...]) -> str:
    labels: list[str] = []
    for f in features:
        labels.append(ACCESSIBILITY_FEATURE_LABELS.get(f, str(f)))
    return ", ".join(labels)


class RadioTab(BaseScheduleTab):
    def __init__(
        self,
        parent: wx.Window,
        provider: ScheduleProvider,
        status_bar: wx.StatusBar,
        *,
        favorites_store: FavoritesStore,
        on_favorites_changed: Callable[[], None],
    ) -> None:
        self._favorites_store = favorites_store
        self._on_favorites_changed = on_favorites_changed
        self._favorite_kind: FavoriteKind = "radio"
        self._suppress_favorite_event = False
        super().__init__(parent, provider, status_bar)

    def _create_header_controls(self, header: wx.BoxSizer) -> None:
        header.AddStretchSpacer(1)
        self._favorite_checkbox = wx.CheckBox(self, label="Ulubione")
        self._favorite_checkbox.Disable()
        self._favorite_checkbox.Bind(wx.EVT_CHECKBOX, self._on_favorite_checkbox)
        header.Add(self._favorite_checkbox, 0, wx.ALIGN_CENTER_VERTICAL)

    def _init_list_columns(self, list_ctrl: wx.ListCtrl) -> None:
        self._show_end_time = False
        list_ctrl.InsertColumn(0, "Od", width=70)
        list_ctrl.InsertColumn(1, "Tytuł", width=520)

    def _on_loaded_sources_days(self, result: tuple[list[Source], list[date]]) -> None:
        sources, days = result
        today = date.today()
        filtered = [d for d in days if d >= today]
        super()._on_loaded_sources_days((sources, filtered or [today]))

    def _after_nav_update(self) -> None:
        self.sync_favorites()

    def _on_nav_selection(self, evt: wx.CommandEvent) -> None:
        super()._on_nav_selection(evt)
        self.sync_favorites()

    def sync_favorites(self) -> None:
        if not hasattr(self, "_favorite_checkbox"):
            return
        src = self._get_selected_source()
        self._suppress_favorite_event = True
        try:
            if not src:
                self._favorite_checkbox.SetValue(False)
                self._favorite_checkbox.Disable()
                return

            self._favorite_checkbox.Enable()
            ref = FavoriteRef(kind=self._favorite_kind, provider_id=str(src.provider_id), source_id=str(src.id))
            self._favorite_checkbox.SetValue(self._favorites_store.is_favorite(ref))
        finally:
            self._suppress_favorite_event = False

    def _on_favorite_checkbox(self, evt: wx.CommandEvent) -> None:
        if self._suppress_favorite_event:
            return
        src = self._get_selected_source()
        if not src:
            return

        ref = FavoriteRef(kind=self._favorite_kind, provider_id=str(src.provider_id), source_id=str(src.id))
        checked = bool(evt.IsChecked())

        if checked:
            changed = self._favorites_store.add_source(self._favorite_kind, src)
            if changed:
                self._status_bar.SetStatusText(f"Dodano do ulubionych: {src.name}")
        else:
            changed = self._favorites_store.remove(ref)
            if changed:
                self._status_bar.SetStatusText(f"Usunięto z ulubionych: {src.name}")

        if changed:
            self._on_favorites_changed()


class FavoritesTab(BaseScheduleTab):
    def __init__(
        self,
        parent: wx.Window,
        provider: ScheduleProvider,
        status_bar: wx.StatusBar,
        *,
        favorites_store: FavoritesStore,
        on_favorites_changed: Callable[[], None],
    ) -> None:
        self._favorites_store = favorites_store
        self._on_favorites_changed = on_favorites_changed
        super().__init__(parent, provider, status_bar)

    def _create_header_controls(self, header: wx.BoxSizer) -> None:
        header.AddStretchSpacer(1)
        self._remove_button = wx.Button(self, label="Usuń z ulubionych")
        self._remove_button.Disable()
        self._remove_button.Bind(wx.EVT_BUTTON, self._on_remove_button)
        header.Add(self._remove_button, 0, wx.ALIGN_CENTER_VERTICAL)

    def _init_list_columns(self, list_ctrl: wx.ListCtrl) -> None:
        self._show_end_time = False
        list_ctrl.InsertColumn(0, "Od", width=70)
        list_ctrl.InsertColumn(1, "Tytuł", width=520)

    def _after_nav_update(self) -> None:
        self._sync_remove_button()

    def _on_nav_selection(self, evt: wx.CommandEvent) -> None:
        super()._on_nav_selection(evt)
        self._sync_remove_button()

    def _on_nav_key_down(self, evt: wx.KeyEvent) -> None:
        if evt.GetKeyCode() == wx.WXK_DELETE:
            self._remove_selected_favorite()
            return
        super()._on_nav_key_down(evt)

    def _sync_remove_button(self) -> None:
        if not hasattr(self, "_remove_button"):
            return
        self._remove_button.Enable(bool(self._selected_favorite_ref()))

    def _selected_favorite_ref(self) -> FavoriteRef | None:
        src = self._get_selected_source()
        if not src:
            return None
        return decode_favorite_source_id(str(src.id))

    def _on_remove_button(self, _evt: wx.CommandEvent) -> None:
        self._remove_selected_favorite()

    def _remove_selected_favorite(self) -> None:
        ref = self._selected_favorite_ref()
        if not ref:
            return
        if not self._favorites_store.remove(ref):
            return

        src = self._get_selected_source()
        if src:
            self._status_bar.SetStatusText(f"Usunięto z ulubionych: {src.name}")

        self._list.DeleteAllItems()
        self._details.SetValue("")
        self._on_favorites_changed()


@dataclass(frozen=True)
class ArchiveNodeData:
    kind: Literal["year", "month", "day", "station"]
    year: int | None = None
    month: int | None = None
    day: date | None = None
    source: Source | None = None


@dataclass(frozen=True)
class ArchiveNavRow:
    key: str
    parent_key: str | None
    level: int
    label: str
    data: ArchiveNodeData | None
    expandable: bool
    expanded: bool
    is_placeholder: bool = False


class ArchiveTab(wx.Panel):
    def __init__(
        self,
        parent: wx.Window,
        provider: ArchiveProvider,
        status_bar: wx.StatusBar,
    ) -> None:
        super().__init__(parent, style=wx.TAB_TRAVERSAL)
        self._provider = provider
        self._status_bar = status_bar
        self._request_token = 0
        self._nav_rows: list[ArchiveNavRow] = []
        self._nav_index_by_key: dict[str, int] = {}
        self._expanded: set[str] = set()
        self._month_days: dict[str, list[date]] = {}
        self._day_sources: dict[str, list[Source]] = {}
        self._loading: set[str] = set()
        self._suppress_nav_event = False

        self._build_ui()
        self.refresh_all(force=False)

    def _build_ui(self) -> None:
        root = wx.BoxSizer(wx.VERTICAL)

        header = wx.BoxSizer(wx.HORIZONTAL)
        header.Add(wx.StaticText(self, label="Wybierz: Rok → Miesiąc → Dzień → Stacja"), 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(header, 0, wx.EXPAND | wx.ALL, 8)

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE | wx.TAB_TRAVERSAL)
        splitter.SetMinimumPaneSize(240)

        left = wx.Panel(splitter, style=wx.TAB_TRAVERSAL)
        right = wx.Panel(splitter, style=wx.TAB_TRAVERSAL)
        splitter.SplitVertically(left, right, 320)

        left_sizer = wx.BoxSizer(wx.VERTICAL)
        left_sizer.Add(wx.StaticText(left, label="Nawigacja:"), 0, wx.BOTTOM, 4)
        self._nav = wx.ListBox(left, style=wx.LB_SINGLE | wx.LB_HSCROLL)
        self._nav.Bind(wx.EVT_LISTBOX, self._on_nav_selection)
        self._nav.Bind(wx.EVT_LISTBOX_DCLICK, self._on_nav_activate)
        self._nav.Bind(wx.EVT_KEY_DOWN, self._on_nav_key_down)
        self._nav.Bind(wx.EVT_SET_FOCUS, self._on_nav_focus)
        left_sizer.Add(self._nav, 1, wx.EXPAND)
        left.SetSizer(left_sizer)

        right_sizer = wx.BoxSizer(wx.VERTICAL)
        self._list = wx.ListCtrl(right, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self._list.InsertColumn(0, "Od", width=70)
        self._list.InsertColumn(1, "Tytuł", width=740)
        self._list.Bind(wx.EVT_KEY_DOWN, self._on_last_control_key_down)
        right_sizer.Add(self._list, 1, wx.EXPAND)
        right.SetSizer(right_sizer)

        root.Add(splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(root)

    def _on_last_control_key_down(self, evt: wx.KeyEvent) -> None:
        if (
            evt.GetKeyCode() == wx.WXK_TAB
            and not evt.ShiftDown()
            and not evt.ControlDown()
            and not evt.AltDown()
        ):
            parent = self.GetParent()
            if isinstance(parent, wx.Notebook):
                parent.SetFocus()
                return
        evt.Skip()

    def refresh(self, *, force: bool) -> None:
        selected = self._get_selected_station()
        if not selected:
            return
        src, day = selected
        self._load_schedule(src, day, force=force)

    def refresh_all(self, *, force: bool) -> None:
        self._list.DeleteAllItems()
        if force:
            self._expanded.clear()
            self._month_days.clear()
            self._day_sources.clear()
            self._loading.clear()
        self._rebuild_nav()
        self._status_bar.SetStatusText("Gotowe.")

    @staticmethod
    def _year_key(year: int) -> str:
        return f"year:{year}"

    @staticmethod
    def _month_key(year: int, month: int) -> str:
        return f"month:{year}:{month:02d}"

    @staticmethod
    def _day_key(day: date) -> str:
        return f"day:{day.isoformat()}"

    @staticmethod
    def _station_key(day: date, source: Source) -> str:
        return f"station:{day.isoformat()}:{source.provider_id}:{source.id}"

    def _rebuild_nav(self, *, preserve_key: str | None = None) -> None:
        selected_key = preserve_key or self._get_selected_nav_key()

        years = self._provider.list_years()
        rows: list[ArchiveNavRow] = []
        for y in reversed(years):
            year_key = self._year_key(y)
            year_expanded = year_key in self._expanded
            rows.append(
                ArchiveNavRow(
                    key=year_key,
                    parent_key=None,
                    level=0,
                    label=str(y),
                    data=ArchiveNodeData(kind="year", year=y),
                    expandable=True,
                    expanded=year_expanded,
                )
            )
            if not year_expanded:
                continue

            for month in range(1, 13):
                month_key = self._month_key(y, month)
                month_expanded = month_key in self._expanded
                rows.append(
                    ArchiveNavRow(
                        key=month_key,
                        parent_key=year_key,
                        level=1,
                        label=POLISH_MONTHS_NOMINATIVE[month],
                        data=ArchiveNodeData(kind="month", year=y, month=month),
                        expandable=True,
                        expanded=month_expanded,
                    )
                )
                if not month_expanded:
                    continue

                days = self._month_days.get(month_key)
                if days is None:
                    label = "Ładowanie…" if month_key in self._loading else "Brak danych."
                    rows.append(
                        ArchiveNavRow(
                            key=f"{month_key}:placeholder",
                            parent_key=month_key,
                            level=2,
                            label=label,
                            data=None,
                            expandable=False,
                            expanded=False,
                            is_placeholder=True,
                        )
                    )
                    continue

                for d in days:
                    day_key = self._day_key(d)
                    day_expanded = day_key in self._expanded
                    rows.append(
                        ArchiveNavRow(
                            key=day_key,
                            parent_key=month_key,
                            level=2,
                            label=f"{d.day:02d}",
                            data=ArchiveNodeData(kind="day", day=d),
                            expandable=True,
                            expanded=day_expanded,
                        )
                    )
                    if not day_expanded:
                        continue

                    sources = self._day_sources.get(day_key)
                    if sources is None:
                        label = "Ładowanie…" if day_key in self._loading else "Brak danych."
                        rows.append(
                            ArchiveNavRow(
                                key=f"{day_key}:placeholder",
                                parent_key=day_key,
                                level=3,
                                label=label,
                                data=None,
                                expandable=False,
                                expanded=False,
                                is_placeholder=True,
                            )
                        )
                        continue

                    for src in sources:
                        rows.append(
                            ArchiveNavRow(
                                key=self._station_key(d, src),
                                parent_key=day_key,
                                level=3,
                                label=src.name,
                                data=ArchiveNodeData(kind="station", day=d, source=src),
                                expandable=False,
                                expanded=False,
                            )
                        )

        self._nav_rows = rows
        self._nav_index_by_key = {row.key: idx for idx, row in enumerate(rows)}

        def format_row(row: ArchiveNavRow) -> str:
            text = ("  " * row.level) + row.label
            if row.expandable:
                text += " (rozwinięte)" if row.expanded else " (zwinięte)"
            return text

        self._suppress_nav_event = True
        self._nav.Freeze()
        self._nav.Clear()
        for row in rows:
            self._nav.Append(format_row(row), row.key)
        self._nav.Thaw()

        if rows:
            idx = self._nav_index_by_key.get(selected_key, 0) if selected_key else 0
            idx = max(0, min(idx, len(rows) - 1))
            self._nav.SetSelection(idx)
            self._nav.EnsureVisible(idx)
        self._suppress_nav_event = False

    def _select_first_nav_item(self) -> None:
        if self._nav.GetCount() == 0:
            return
        self._nav.SetSelection(0)
        self._nav.EnsureVisible(0)

    def _get_selected_nav_key(self) -> str | None:
        idx = self._nav.GetSelection()
        if idx == wx.NOT_FOUND:
            return None
        key = self._nav.GetClientData(idx)
        return key if isinstance(key, str) else None

    def _get_selected_nav_row(self) -> ArchiveNavRow | None:
        key = self._get_selected_nav_key()
        if not key:
            return None
        idx = self._nav_index_by_key.get(key)
        if idx is None:
            return None
        return self._nav_rows[idx]

    def _on_nav_focus(self, evt: wx.FocusEvent) -> None:
        if self._nav.GetSelection() == wx.NOT_FOUND and self._nav.GetCount() > 0:
            self._select_first_nav_item()
        evt.Skip()

    def _on_nav_selection(self, _evt: wx.CommandEvent) -> None:
        if self._suppress_nav_event:
            return
        row = self._get_selected_nav_row()
        if not row:
            return
        if not row.data:
            if row.is_placeholder:
                self._show_list_message(row.label)
            return
        if row.data.kind != "station" or not row.data.source or not row.data.day:
            if row.data.kind == "day":
                self._show_list_message("Rozwiń dzień (→) i wybierz stację.")
            else:
                self._show_list_message("Rozwiń (→), aby zobaczyć kolejne poziomy.")
            return
        self._load_schedule(row.data.source, row.data.day, force=False)

    def _on_nav_activate(self, _evt: wx.CommandEvent) -> None:
        self._toggle_or_load_selected()

    def _on_nav_key_down(self, evt: wx.KeyEvent) -> None:
        key = evt.GetKeyCode()
        if key == wx.WXK_RIGHT:
            if self._expand_selected():
                return
            return
        elif key == wx.WXK_LEFT:
            if self._collapse_selected():
                return
            return
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER, wx.WXK_SPACE):
            self._toggle_or_load_selected()
            return
        elif key == wx.WXK_HOME:
            self._select_first_nav_item()
            return
        elif key == wx.WXK_END:
            count = self._nav.GetCount()
            if count:
                self._nav.SetSelection(count - 1)
                self._nav.EnsureVisible(count - 1)
            return
        evt.Skip()

    def _toggle_or_load_selected(self) -> None:
        row = self._get_selected_nav_row()
        if not row:
            return
        if row.expandable:
            new_state = not row.expanded
            self._set_expanded(row.key, expanded=new_state)
            if new_state and row.data:
                self._maybe_start_loading(row.data)
            return
        if row.data and row.data.kind == "station" and row.data.source and row.data.day:
            self._load_schedule(row.data.source, row.data.day, force=False)

    def _show_list_message(self, message: str) -> None:
        self._list.DeleteAllItems()
        row = self._list.InsertItem(0, "")
        self._list.SetItem(row, 1, message)

    def _expand_selected(self) -> bool:
        row = self._get_selected_nav_row()
        if not row or not row.expandable:
            return False
        if not row.expanded:
            self._set_expanded(row.key, expanded=True)
            if row.data:
                self._maybe_start_loading(row.data)
            return True
        child_idx = self._find_first_child_index(row.key)
        if child_idx is None:
            return False
        self._nav.SetSelection(child_idx)
        self._nav.EnsureVisible(child_idx)
        return True

    def _collapse_selected(self) -> bool:
        row = self._get_selected_nav_row()
        if not row:
            return False
        if row.expandable and row.expanded:
            self._set_expanded(row.key, expanded=False)
            return True
        if row.parent_key:
            parent_idx = self._nav_index_by_key.get(row.parent_key)
            if parent_idx is None:
                return False
            self._nav.SetSelection(parent_idx)
            self._nav.EnsureVisible(parent_idx)
            return True
        return False

    def _set_expanded(self, key: str, *, expanded: bool) -> None:
        if expanded:
            self._expanded.add(key)
        else:
            self._expanded.discard(key)
        self._rebuild_nav(preserve_key=key)

    def _find_first_child_index(self, parent_key: str) -> int | None:
        parent_idx = self._nav_index_by_key.get(parent_key)
        if parent_idx is None:
            return None
        parent_level = self._nav_rows[parent_idx].level
        for idx in range(parent_idx + 1, len(self._nav_rows)):
            row = self._nav_rows[idx]
            if row.level <= parent_level:
                break
            if row.parent_key == parent_key:
                return idx
        return None

    def _maybe_start_loading(self, data: ArchiveNodeData) -> None:
        if data.kind == "month" and data.year and data.month:
            month_key = self._month_key(data.year, data.month)
            if month_key in self._month_days or month_key in self._loading:
                return
            self._loading.add(month_key)
            self._rebuild_nav(preserve_key=month_key)
            self._status_bar.SetStatusText(f"Ładowanie dni: {data.year}-{data.month:02d}…")

            def work() -> list[date]:
                return self._provider.list_days_in_month(data.year, data.month, force_refresh=False)

            def on_success(days: list[date]) -> None:
                self._loading.discard(month_key)
                self._month_days[month_key] = days
                self._rebuild_nav(preserve_key=month_key)
                self._status_bar.SetStatusText("Gotowe.")

            def on_error(exc: Exception) -> None:
                self._loading.discard(month_key)
                self._rebuild_nav(preserve_key=month_key)
                self._on_error(exc)

            self._run_in_thread(work, on_success=on_success, on_error=on_error)
            return

        if data.kind == "day" and data.day:
            day_key = self._day_key(data.day)
            if day_key in self._day_sources or day_key in self._loading:
                return
            self._loading.add(day_key)
            self._rebuild_nav(preserve_key=day_key)
            self._status_bar.SetStatusText(f"Ładowanie stacji: {data.day.isoformat()}…")

            def work() -> list[Source]:
                return self._provider.list_sources_for_day(data.day, force_refresh=False)

            def on_success(sources: list[Source]) -> None:
                self._loading.discard(day_key)
                self._day_sources[day_key] = sources
                self._rebuild_nav(preserve_key=day_key)
                self._status_bar.SetStatusText("Gotowe.")

            def on_error(exc: Exception) -> None:
                self._loading.discard(day_key)
                self._rebuild_nav(preserve_key=day_key)
                self._on_error(exc)

            self._run_in_thread(work, on_success=on_success, on_error=on_error)

    def _get_selected_station(self) -> tuple[Source, date] | None:
        row = self._get_selected_nav_row()
        data = row.data if row else None
        if not data or data.kind != "station" or not data.source or not data.day:
            return None
        return data.source, data.day

    def _load_schedule(self, source: Source, day: date, *, force: bool) -> None:
        self._request_token += 1
        token = self._request_token

        self._list.DeleteAllItems()
        self._status_bar.SetStatusText(f"Pobieranie: {source.name} {day.isoformat()}…")

        def work() -> list[ScheduleItem]:
            return self._provider.get_schedule(source, day, force_refresh=force)

        def on_success(items: list[ScheduleItem]) -> None:
            if token != self._request_token:
                return
            self._show_schedule(items)
            self._status_bar.SetStatusText("Gotowe.")

        self._run_in_thread(work, on_success=on_success, on_error=self._on_error)

    def _show_schedule(self, items: list[ScheduleItem]) -> None:
        self._list.DeleteAllItems()
        for idx, it in enumerate(items):
            start = it.start_time.strftime("%H:%M") if it.start_time else ""
            title = it.title
            if it.subtitle:
                title = f"{title} — {it.subtitle}"
            row = self._list.InsertItem(idx, start)
            self._list.SetItem(row, 1, title)

    def _run_in_thread(
        self,
        work: Callable[[], Any],
        *,
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                wx.CallAfter(on_error, e)
                return
            wx.CallAfter(on_success, result)

        threading.Thread(target=runner, daemon=True).start()

    def _on_error(self, exc: Exception) -> None:
        msg = str(exc) or "Nieznany błąd."
        self._status_bar.SetStatusText(f"Błąd: {msg}")
        self._list.DeleteAllItems()
        row = self._list.InsertItem(0, "")
        self._list.SetItem(row, 1, f"Błąd: {msg}")
