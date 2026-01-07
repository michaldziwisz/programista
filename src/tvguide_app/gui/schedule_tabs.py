from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal

import wx

from tvguide_app.core.models import ScheduleItem, Source
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
        super().__init__(parent)
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

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        splitter.SetMinimumPaneSize(200)

        left = wx.Panel(splitter)
        mid = wx.Panel(splitter)
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

        mid_splitter = wx.SplitterWindow(mid, style=wx.SP_LIVE_UPDATE)
        mid_splitter.SetMinimumPaneSize(140)

        list_panel = wx.Panel(mid_splitter)
        details_panel = wx.Panel(mid_splitter)
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
        details_sizer.Add(self._details, 1, wx.EXPAND)
        details_panel.SetSizer(details_sizer)

        mid_sizer = wx.BoxSizer(wx.VERTICAL)
        mid_sizer.Add(mid_splitter, 1, wx.EXPAND)
        mid.SetSizer(mid_sizer)

        root.Add(splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(root)

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

    def _rebuild_nav(self) -> None:
        selected_key = self._get_selected_nav_key()

        rows: list[NavRow] = []
        if self._view_mode == "by_source":
            for src in self._sources:
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
                    for d in self._days:
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
            for d in self._days:
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
                    for src in self._sources:
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
    def _init_list_columns(self, list_ctrl: wx.ListCtrl) -> None:
        self._show_end_time = False
        list_ctrl.InsertColumn(0, "Od", width=70)
        list_ctrl.InsertColumn(1, "Tytuł", width=520)


class RadioTab(BaseScheduleTab):
    def _init_list_columns(self, list_ctrl: wx.ListCtrl) -> None:
        self._show_end_time = False
        list_ctrl.InsertColumn(0, "Od", width=70)
        list_ctrl.InsertColumn(1, "Tytuł", width=520)

    def _on_loaded_sources_days(self, result: tuple[list[Source], list[date]]) -> None:
        sources, days = result
        today = date.today()
        filtered = [d for d in days if d >= today]
        super()._on_loaded_sources_days((sources, filtered or [today]))


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
        super().__init__(parent)
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

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        splitter.SetMinimumPaneSize(240)

        left = wx.Panel(splitter)
        right = wx.Panel(splitter)
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
        right_sizer.Add(self._list, 1, wx.EXPAND)
        right.SetSizer(right_sizer)

        root.Add(splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(root)

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
