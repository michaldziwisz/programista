from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sys
import time
import threading
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal

import wx
import wx.dataview as dv

from tvguide_app.core.favorites import FavoriteKind, FavoriteRef, FavoritesStore, decode_favorite_source_id
from tvguide_app.core.models import ACCESSIBILITY_FEATURE_LABELS, AccessibilityFeature, ScheduleItem, Source
from tvguide_app.core.providers.archive_base import ArchiveProvider
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.search_index import SearchIndex, SearchKind
from tvguide_app.core.settings import SettingsStore, TvAccessibilityFilters
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
    def __init__(
        self,
        parent: wx.Window,
        provider: ScheduleProvider,
        status_bar: wx.StatusBar,
        *,
        search_index: SearchIndex | None = None,
        search_kind: SearchKind | None = None,
    ) -> None:
        super().__init__(parent, style=wx.TAB_TRAVERSAL)
        self._provider = provider
        self._status_bar = status_bar
        self._search_index = search_index
        self._search_kind = search_kind
        self._view_mode: ViewMode = "by_source"
        self._request_token = 0
        self._show_end_time = True

        self._sources: list[Source] = []
        self._days: list[date] = []
        self._days_by_provider_id: dict[str, set[date]] = {}
        self._nav_rows: list[NavRow] = []
        self._nav_index_by_key: dict[str, int] = {}
        self._expanded_by_source: set[str] = set()
        self._expanded_by_day: set[str] = set()
        self._suppress_nav_event = False
        self._nav_ignore_activate_until = 0.0

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
        self._nav = wx.ListBox(left, style=wx.LB_SINGLE | wx.LB_HSCROLL | wx.WANTS_CHARS)
        self._nav.Bind(wx.EVT_LISTBOX, self._on_nav_selection)
        self._nav.Bind(wx.EVT_LISTBOX_DCLICK, self._on_nav_activate)
        self._nav.Bind(wx.EVT_KEY_DOWN, self._on_nav_key_down)
        self._nav.Bind(wx.EVT_SET_FOCUS, self._on_nav_focus)
        self._nav.Bind(wx.EVT_CONTEXT_MENU, self._on_nav_context_menu)
        left_sizer.Add(self._nav, 1, wx.EXPAND)
        left.SetSizer(left_sizer)

        mid_splitter = wx.SplitterWindow(mid, style=wx.SP_LIVE_UPDATE | wx.TAB_TRAVERSAL)
        mid_splitter.SetMinimumPaneSize(140)

        list_panel = wx.Panel(mid_splitter, style=wx.TAB_TRAVERSAL)
        details_panel = wx.Panel(mid_splitter, style=wx.TAB_TRAVERSAL)
        mid_splitter.SplitHorizontally(list_panel, details_panel, 320)

        list_sizer = wx.BoxSizer(wx.VERTICAL)
        list_sizer.Add(wx.StaticText(list_panel, label="Programy:"), 0, wx.BOTTOM, 4)
        self._list_is_dataview = sys.platform == "darwin"
        if self._list_is_dataview:
            self._list = dv.DataViewListCtrl(
                list_panel,
                style=dv.DV_SINGLE | dv.DV_ROW_LINES | dv.DV_VERT_RULES,
            )
            self._list.Bind(dv.EVT_DATAVIEW_SELECTION_CHANGED, self._on_item_selected)
        else:
            self._list = wx.ListCtrl(list_panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
            self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_item_selected)
        self._list.Bind(wx.EVT_KEY_DOWN, self._on_list_key_down)
        self._list.SetName("Programy")
        self._init_list_columns(self._list)
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
        if evt.GetKeyCode() == wx.WXK_ESCAPE and not evt.HasAnyModifiers():
            if hasattr(self, "_list"):
                self._list.SetFocus()
                return
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

    def _on_list_key_down(self, evt: wx.KeyEvent) -> None:
        key = evt.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            idx = self._ensure_list_selection()
            if idx is None:
                return
            self._show_item_details(idx)
            self._details.SetFocus()
            return
        if key == wx.WXK_ESCAPE and not evt.HasAnyModifiers():
            self._nav.SetFocus()
            return
        evt.Skip()

    def _create_header_controls(self, _header: wx.BoxSizer) -> None:
        return

    def _add_list_column(self, list_ctrl: wx.Window, label: str, *, width: int) -> None:
        if getattr(self, "_list_is_dataview", False):
            list_ctrl.AppendTextColumn(label, width=width, mode=dv.DATAVIEW_CELL_INERT)  # type: ignore[attr-defined]
            return
        list_ctrl.InsertColumn(list_ctrl.GetColumnCount(), label, width=width)  # type: ignore[attr-defined]

    def _append_list_row(self, values: list[str]) -> None:
        if getattr(self, "_list_is_dataview", False):
            self._list.AppendItem(values)  # type: ignore[attr-defined]
            return
        row = self._list.InsertItem(self._list.GetItemCount(), values[0] if values else "")  # type: ignore[attr-defined]
        for col, value in enumerate(values[1:], start=1):
            self._list.SetItem(row, col, value)  # type: ignore[attr-defined]

    def _create_nav_context_menu(self) -> wx.Menu | None:
        return None

    def _on_nav_context_menu(self, evt: wx.ContextMenuEvent) -> None:
        if not hasattr(self, "_nav"):
            return

        pos = evt.GetPosition()
        if pos != wx.DefaultPosition:
            self._select_nav_item_at_screen_pos(pos)
            popup_pos = self._nav.ScreenToClient(pos)
        else:
            popup_pos = wx.Point(10, 10)

        menu = self._create_nav_context_menu()
        if not menu:
            return

        try:
            self._nav.PopupMenu(menu, popup_pos)
        finally:
            menu.Destroy()

    def _select_nav_item_at_screen_pos(self, screen_pos: wx.Point) -> None:
        if not hasattr(self, "_nav"):
            return

        client_pos = self._nav.ScreenToClient(screen_pos)
        hit = None
        if hasattr(self._nav, "HitTest"):
            try:
                hit = self._nav.HitTest(client_pos)
            except Exception:  # noqa: BLE001
                hit = None
        if isinstance(hit, tuple):
            idx = hit[0]
        else:
            idx = hit

        if not isinstance(idx, int) or idx == wx.NOT_FOUND:
            return

        if idx != self._nav.GetSelection():
            self._suppress_nav_event = True
            self._nav.SetSelection(idx)
            self._nav.EnsureVisible(idx)
            self._suppress_nav_event = False
            self._after_nav_update()

    def _init_list_columns(self, list_ctrl: wx.Window) -> None:
        self._show_end_time = True
        self._add_list_column(list_ctrl, "Od", width=70)
        self._add_list_column(list_ctrl, "Do", width=70)
        self._add_list_column(list_ctrl, "Tytuł", width=420)

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
        self._days_by_provider_id = self._build_days_by_provider_id()
        self._rebuild_nav()
        self._status_bar.SetStatusText("Gotowe.")

    def _build_days_by_provider_id(self) -> dict[str, set[date]]:
        method = getattr(self._provider, "list_days_for_provider", None)
        if not callable(method):
            return {}

        provider_ids = sorted({str(src.provider_id) for src in self._sources})
        out: dict[str, set[date]] = {}
        for pid in provider_ids:
            try:
                days = method(pid, force_refresh=False)
            except Exception:  # noqa: BLE001
                continue
            out[pid] = set(days)
        return out

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

    def _nav_child_days_for_source(self, source: Source) -> list[date]:
        pid = str(source.provider_id)
        if pid not in self._days_by_provider_id:
            return self._days

        allowed = self._days_by_provider_id[pid]
        return [d for d in self._days if d in allowed]

    def _nav_child_sources_for_day(self, day: date) -> list[Source]:
        if not self._days_by_provider_id:
            return self._sources

        out: list[Source] = []
        for src in self._sources:
            pid = str(src.provider_id)
            if pid not in self._days_by_provider_id:
                out.append(src)
                continue
            if day in self._days_by_provider_id[pid]:
                out.append(src)
        return out

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
        if time.monotonic() < self._nav_ignore_activate_until:
            self._nav_ignore_activate_until = 0.0
            return
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
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._nav_ignore_activate_until = time.monotonic() + 0.3
            self._advance_from_nav()
            return
        elif key == wx.WXK_ESCAPE and not evt.HasAnyModifiers():
            self._collapse_selected()
            return
        elif key == wx.WXK_SPACE and not evt.HasAnyModifiers():
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

    def _advance_from_nav(self) -> None:
        row = self._get_selected_nav_row()
        if not row:
            return

        if row.expandable:
            if not row.expanded:
                self._set_nav_expanded(row.key, expanded=True)
            child_idx = self._find_first_child_index(row.key)
            if child_idx is not None:
                self._set_nav_selection(child_idx)
            return

        if row.data.kind == "pair" and row.data.source and row.data.day:
            self._list.SetFocus()

    def _set_nav_selection(self, idx: int) -> None:
        if idx == wx.NOT_FOUND:
            return
        if idx < 0 or idx >= self._nav.GetCount():
            return

        self._suppress_nav_event = True
        self._nav.SetSelection(idx)
        self._nav.EnsureVisible(idx)
        self._suppress_nav_event = False
        self._on_nav_selection(wx.CommandEvent())
        self._after_nav_update()

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
            if self._search_index and self._search_kind:
                try:
                    self._search_index.add_items(self._search_kind, items)
                except Exception:  # noqa: BLE001
                    pass
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
        for it in items:
            start = it.start_time.strftime("%H:%M") if it.start_time else ""
            title = it.title
            values: list[str] = [start]
            if self._show_end_time:
                end = it.end_time.strftime("%H:%M") if it.end_time else ""
                values.append(end)
            values.append(title)
            self._append_list_row(values)

    def _on_item_selected(self, evt: wx.Event) -> None:
        if hasattr(evt, "GetIndex"):
            idx = int(evt.GetIndex())  # type: ignore[attr-defined]
        elif getattr(self, "_list_is_dataview", False):
            idx = int(self._list.GetSelectedRow())  # type: ignore[attr-defined]
        else:
            idx = wx.NOT_FOUND
        self._show_item_details(idx)

    def _show_item_details(self, idx: int) -> None:
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

    def _ensure_list_selection(self) -> int | None:
        idx = self._get_selected_list_index()
        if idx is not None and idx >= 0:
            return idx
        count = int(getattr(self._list, "GetItemCount")())  # type: ignore[misc]
        if count <= 0:
            return None
        self._select_list_index(0)
        return 0

    def _get_selected_list_index(self) -> int | None:
        if getattr(self, "_list_is_dataview", False):
            idx = int(self._list.GetSelectedRow())  # type: ignore[attr-defined]
            return idx if idx >= 0 else None

        idx = int(self._list.GetNextItem(-1, wx.LIST_NEXT_ALL, wx.LIST_STATE_SELECTED))  # type: ignore[attr-defined]
        return idx if idx != wx.NOT_FOUND else None

    def _select_list_index(self, idx: int) -> None:
        if idx < 0:
            return
        if getattr(self, "_list_is_dataview", False):
            self._list.SelectRow(idx)  # type: ignore[attr-defined]
            try:
                self._list.EnsureVisible(self._list.RowToItem(idx))  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            return

        self._list.SetItemState(  # type: ignore[attr-defined]
            idx,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
        )
        try:
            self._list.EnsureVisible(idx)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

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
        search_index: SearchIndex | None = None,
    ) -> None:
        self._favorites_store = favorites_store
        self._on_favorites_changed = on_favorites_changed
        self._favorite_kind: FavoriteKind = "tv"
        super().__init__(parent, provider, status_bar, search_index=search_index, search_kind="tv")

    def _init_list_columns(self, list_ctrl: wx.Window) -> None:
        self._show_end_time = False
        self._add_list_column(list_ctrl, "Od", width=70)
        self._add_list_column(list_ctrl, "Tytuł", width=520)

    def sync_favorites(self) -> None:
        # The tab used to expose a checkbox; favorites are now managed via context menu.
        return

    def _create_nav_context_menu(self) -> wx.Menu | None:
        src = self._get_selected_source()
        if not src:
            return None
        ref = FavoriteRef(kind=self._favorite_kind, provider_id=str(src.provider_id), source_id=str(src.id))
        is_fav = self._favorites_store.is_favorite(ref)

        menu = wx.Menu()
        label = "Usuń z ulubionych" if is_fav else "Dodaj do ulubionych"
        item_id = wx.NewIdRef()
        menu.Append(item_id, label)
        self.Bind(wx.EVT_MENU, lambda _evt, s=src, fav=is_fav: self._toggle_favorite(s, fav), id=item_id)
        return menu

    def _toggle_favorite(self, src: Source, is_fav: bool) -> None:
        ref = FavoriteRef(kind=self._favorite_kind, provider_id=str(src.provider_id), source_id=str(src.id))

        if is_fav:
            changed = self._favorites_store.remove(ref)
            if changed:
                self._status_bar.SetStatusText(f"Usunięto z ulubionych: {src.name}")
        else:
            changed = self._favorites_store.add_source(self._favorite_kind, src)
            if changed:
                self._status_bar.SetStatusText(f"Dodano do ulubionych: {src.name}")

        if changed:
            self._on_favorites_changed()


class TvAccessibilityTab(BaseScheduleTab):
    def __init__(
        self,
        parent: wx.Window,
        provider: ScheduleProvider,
        status_bar: wx.StatusBar,
        *,
        settings_store: SettingsStore,
        search_index: SearchIndex | None = None,
    ) -> None:
        self._settings_store = settings_store
        persisted = self._settings_store.get_tv_accessibility_filters()
        self._filter_ad = bool(persisted.ad)
        self._filter_jm = bool(persisted.jm)
        self._filter_n = bool(persisted.n)
        self._all_items: list[ScheduleItem] = []
        self._a11y_index_token = 0
        self._a11y_index_ready = False
        self._a11y_pair_features: dict[str, frozenset[AccessibilityFeature] | None] = {}
        super().__init__(parent, provider, status_bar, search_index=search_index, search_kind="tv_accessibility")

    def _create_header_controls(self, header: wx.BoxSizer) -> None:
        header.AddStretchSpacer(1)
        header.Add(wx.StaticText(self, label="Udogodnienia:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self._ad_cb = wx.CheckBox(self, label="Audiodeskrypcja")
        self._ad_cb.SetValue(self._filter_ad)
        self._ad_cb.Bind(wx.EVT_CHECKBOX, self._on_filter_changed)
        header.Add(self._ad_cb, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._jm_cb = wx.CheckBox(self, label="Język migowy")
        self._jm_cb.SetValue(self._filter_jm)
        self._jm_cb.Bind(wx.EVT_CHECKBOX, self._on_filter_changed)
        header.Add(self._jm_cb, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self._n_cb = wx.CheckBox(self, label="Napisy")
        self._n_cb.SetValue(self._filter_n)
        self._n_cb.Bind(wx.EVT_CHECKBOX, self._on_filter_changed)
        header.Add(self._n_cb, 0, wx.ALIGN_CENTER_VERTICAL)

    def _init_list_columns(self, list_ctrl: wx.Window) -> None:
        self._show_end_time = False
        self._add_list_column(list_ctrl, "Od", width=70)
        self._add_list_column(list_ctrl, "Tytuł", width=420)
        self._add_list_column(list_ctrl, "Udogodnienia", width=240)

    def _on_loaded_sources_days(self, result: tuple[list[Source], list[date]]) -> None:
        sources, days = result
        self._sources = sources
        self._days = days
        self._start_a11y_index_build()

    def _on_filter_changed(self, _evt: wx.CommandEvent) -> None:
        if hasattr(self, "_ad_cb"):
            self._filter_ad = bool(self._ad_cb.IsChecked())
        if hasattr(self, "_jm_cb"):
            self._filter_jm = bool(self._jm_cb.IsChecked())
        if hasattr(self, "_n_cb"):
            self._filter_n = bool(self._n_cb.IsChecked())

        try:
            self._settings_store.set_tv_accessibility_filters(
                TvAccessibilityFilters(ad=self._filter_ad, jm=self._filter_jm, n=self._filter_n)
            )
        except Exception:  # noqa: BLE001
            self._status_bar.SetStatusText("Nie udało się zapisać filtrów.")
        self._clear_schedule_view()
        self._expanded_by_source.clear()
        self._expanded_by_day.clear()
        if self._a11y_index_ready:
            self._rebuild_nav()
            self._status_bar.SetStatusText("Gotowe.")

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
        for it in filtered:
            start = it.start_time.strftime("%H:%M") if it.start_time else ""
            self._append_list_row([start, it.title, _format_accessibility(it.accessibility)])

        if not filtered and self._all_items:
            self._details.SetValue("Brak programów spełniających wybrane udogodnienia.")

    def _start_a11y_index_build(self) -> None:
        self._a11y_index_token += 1
        token = self._a11y_index_token

        self._a11y_index_ready = False
        self._a11y_pair_features = {}
        self._expanded_by_source.clear()
        self._expanded_by_day.clear()

        if hasattr(self, "_nav"):
            self._nav.Disable()
            self._nav.Clear()
        if hasattr(self, "_view_choice"):
            self._view_choice.Disable()

        self._status_bar.SetStatusText("Analizowanie udogodnień…")

        sources = list(self._sources)
        days = list(self._days)

        def work() -> dict[str, frozenset[AccessibilityFeature] | None]:
            return self._compute_a11y_pair_features(sources, days)

        def on_success(pair_features: dict[str, frozenset[AccessibilityFeature] | None]) -> None:
            if token != self._a11y_index_token:
                return
            self._a11y_pair_features = pair_features
            self._a11y_index_ready = True
            self._nav.Enable()
            self._view_choice.Enable()
            self._rebuild_nav()
            self._status_bar.SetStatusText("Gotowe.")

        def on_error(exc: Exception) -> None:
            if token != self._a11y_index_token:
                return
            self._nav.Enable()
            self._view_choice.Enable()
            self._on_error(exc)

        self._run_in_thread(work, on_success=on_success, on_error=on_error)

    def _compute_a11y_pair_features(
        self,
        sources: list[Source],
        days: list[date],
    ) -> dict[str, frozenset[AccessibilityFeature] | None]:
        sources_by_provider: dict[str, list[Source]] = {}
        for src in sources:
            sources_by_provider.setdefault(str(src.provider_id), []).append(src)

        list_days_for_provider = getattr(self._provider, "list_days_for_provider", None)
        allowed_days_by_provider: dict[str, set[date] | None] = {}
        for pid in sources_by_provider:
            if callable(list_days_for_provider):
                try:
                    allowed_days_by_provider[pid] = set(list_days_for_provider(pid, force_refresh=False))
                except Exception:  # noqa: BLE001
                    allowed_days_by_provider[pid] = None
            else:
                allowed_days_by_provider[pid] = None

        # Warm up provider caches concurrently (the expensive part is usually fetching/parsing per day).
        def warm_up(sample_source: Source, day: date) -> None:
            try:
                self._provider.get_schedule(sample_source, day, force_refresh=False)
            except Exception:  # noqa: BLE001
                return

        warm_tasks: list[tuple[Source, date]] = []
        for pid, srcs in sources_by_provider.items():
            if not srcs:
                continue
            sample = srcs[0]
            allowed = allowed_days_by_provider.get(pid)
            warm_days = sorted(allowed) if allowed else list(days)
            for d in warm_days:
                warm_tasks.append((sample, d))

        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(warm_up, src, d) for src, d in warm_tasks]
            for f in futures:
                f.result()

        out: dict[str, frozenset[AccessibilityFeature] | None] = {}
        for src in sources:
            pid = str(src.provider_id)
            allowed = allowed_days_by_provider.get(pid)
            for d in days:
                key = self._pair_key(src, d)
                if allowed is not None and d not in allowed:
                    out[key] = frozenset()
                    continue
                try:
                    items = self._provider.get_schedule(src, d, force_refresh=False)
                except Exception:  # noqa: BLE001
                    out[key] = None
                    continue
                features: set[AccessibilityFeature] = set()
                for it in items:
                    if it.accessibility:
                        features.update(it.accessibility)
                out[key] = frozenset(features)
        return out

    def _current_a11y_features(self) -> set[AccessibilityFeature]:
        selected: set[AccessibilityFeature] = set()
        if self._filter_ad:
            selected.add("AD")
        if self._filter_jm:
            selected.add("JM")
        if self._filter_n:
            selected.add("N")
        if not selected:
            selected = {"AD", "JM", "N"}
        return selected

    def _pair_matches(self, source: Source, day: date) -> bool:
        feats = self._a11y_pair_features.get(self._pair_key(source, day))
        if feats is None:
            return True
        selected = self._current_a11y_features()
        return bool(feats.intersection(selected))

    def _nav_root_sources(self) -> list[Source]:
        if not self._a11y_index_ready:
            return []
        out: list[Source] = []
        for src in self._sources:
            if any(self._pair_matches(src, d) for d in self._days):
                out.append(src)
        return out

    def _nav_root_days(self) -> list[date]:
        if not self._a11y_index_ready:
            return []
        out: list[date] = []
        for d in self._days:
            if any(self._pair_matches(src, d) for src in self._sources):
                out.append(d)
        return out

    def _nav_child_days_for_source(self, source: Source) -> list[date]:
        if not self._a11y_index_ready:
            return []
        return [d for d in self._days if self._pair_matches(source, d)]

    def _nav_child_sources_for_day(self, day: date) -> list[Source]:
        if not self._a11y_index_ready:
            return []
        return [src for src in self._sources if self._pair_matches(src, day)]


def _format_accessibility(features: tuple[AccessibilityFeature, ...]) -> str:
    labels: list[str] = []
    for f in features:
        labels.append(ACCESSIBILITY_FEATURE_LABELS.get(f, str(f)))
    return ", ".join(labels)


def _format_archive_item_details(item: ScheduleItem) -> str:
    parts: list[str] = []
    if item.start_time:
        parts.append(item.start_time.strftime("%H:%M"))
    title = item.title
    if item.subtitle:
        title = f"{title} — {item.subtitle}"
    if title:
        parts.append(title)
    if item.details_summary:
        summary = item.details_summary.strip()
        if summary and summary != (item.subtitle or "").strip():
            parts.append(summary)
    return "\n".join([p for p in parts if p]).strip() or "Brak szczegółów."


class RadioTab(BaseScheduleTab):
    def __init__(
        self,
        parent: wx.Window,
        provider: ScheduleProvider,
        status_bar: wx.StatusBar,
        *,
        favorites_store: FavoritesStore,
        on_favorites_changed: Callable[[], None],
        search_index: SearchIndex | None = None,
    ) -> None:
        self._favorites_store = favorites_store
        self._on_favorites_changed = on_favorites_changed
        self._favorite_kind: FavoriteKind = "radio"
        super().__init__(parent, provider, status_bar, search_index=search_index, search_kind="radio")

    def _init_list_columns(self, list_ctrl: wx.Window) -> None:
        self._show_end_time = False
        self._add_list_column(list_ctrl, "Od", width=70)
        self._add_list_column(list_ctrl, "Tytuł", width=520)

    def _on_loaded_sources_days(self, result: tuple[list[Source], list[date]]) -> None:
        sources, days = result
        today = date.today()
        filtered = [d for d in days if d >= today]
        super()._on_loaded_sources_days((sources, filtered or [today]))

    def sync_favorites(self) -> None:
        # The tab used to expose a checkbox; favorites are now managed via context menu.
        return

    def _create_nav_context_menu(self) -> wx.Menu | None:
        src = self._get_selected_source()
        if not src:
            return None

        ref = FavoriteRef(kind=self._favorite_kind, provider_id=str(src.provider_id), source_id=str(src.id))
        is_fav = self._favorites_store.is_favorite(ref)

        menu = wx.Menu()
        label = "Usuń z ulubionych" if is_fav else "Dodaj do ulubionych"
        item_id = wx.NewIdRef()
        menu.Append(item_id, label)
        self.Bind(wx.EVT_MENU, lambda _evt, s=src, fav=is_fav: self._toggle_favorite(s, fav), id=item_id)
        return menu

    def _toggle_favorite(self, src: Source, is_fav: bool) -> None:
        ref = FavoriteRef(kind=self._favorite_kind, provider_id=str(src.provider_id), source_id=str(src.id))

        if is_fav:
            changed = self._favorites_store.remove(ref)
            if changed:
                self._status_bar.SetStatusText(f"Usunięto z ulubionych: {src.name}")
        else:
            changed = self._favorites_store.add_source(self._favorite_kind, src)
            if changed:
                self._status_bar.SetStatusText(f"Dodano do ulubionych: {src.name}")

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
        search_index: SearchIndex | None = None,
    ) -> None:
        self._favorites_store = favorites_store
        self._on_favorites_changed = on_favorites_changed
        super().__init__(parent, provider, status_bar, search_index=search_index, search_kind=None)

    def _create_header_controls(self, header: wx.BoxSizer) -> None:
        header.AddStretchSpacer(1)
        self._remove_button = wx.Button(self, label="Usuń z ulubionych")
        self._remove_button.Disable()
        self._remove_button.Bind(wx.EVT_BUTTON, self._on_remove_button)
        header.Add(self._remove_button, 0, wx.ALIGN_CENTER_VERTICAL)

    def _init_list_columns(self, list_ctrl: wx.Window) -> None:
        self._show_end_time = False
        self._add_list_column(list_ctrl, "Od", width=70)
        self._add_list_column(list_ctrl, "Tytuł", width=520)

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
        *,
        search_index: SearchIndex | None = None,
    ) -> None:
        super().__init__(parent, style=wx.TAB_TRAVERSAL)
        self._provider = provider
        self._status_bar = status_bar
        self._search_index = search_index
        self._request_token = 0
        self._nav_rows: list[ArchiveNavRow] = []
        self._nav_index_by_key: dict[str, int] = {}
        self._expanded: set[str] = set()
        self._month_days: dict[str, list[date]] = {}
        self._day_sources: dict[str, list[Source]] = {}
        self._loading: set[str] = set()
        self._items: list[ScheduleItem] = []
        self._suppress_nav_event = False
        self._nav_ignore_activate_until = 0.0

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
        self._nav = wx.ListBox(left, style=wx.LB_SINGLE | wx.LB_HSCROLL | wx.WANTS_CHARS)
        self._nav.Bind(wx.EVT_LISTBOX, self._on_nav_selection)
        self._nav.Bind(wx.EVT_LISTBOX_DCLICK, self._on_nav_activate)
        self._nav.Bind(wx.EVT_KEY_DOWN, self._on_nav_key_down)
        self._nav.Bind(wx.EVT_SET_FOCUS, self._on_nav_focus)
        left_sizer.Add(self._nav, 1, wx.EXPAND)
        left.SetSizer(left_sizer)

        right_splitter = wx.SplitterWindow(right, style=wx.SP_LIVE_UPDATE | wx.TAB_TRAVERSAL)
        right_splitter.SetMinimumPaneSize(140)

        list_panel = wx.Panel(right_splitter, style=wx.TAB_TRAVERSAL)
        details_panel = wx.Panel(right_splitter, style=wx.TAB_TRAVERSAL)
        right_splitter.SplitHorizontally(list_panel, details_panel, 360)

        list_sizer = wx.BoxSizer(wx.VERTICAL)
        list_sizer.Add(wx.StaticText(list_panel, label="Programy:"), 0, wx.BOTTOM, 4)
        self._list_is_dataview = sys.platform == "darwin"
        if self._list_is_dataview:
            self._list = dv.DataViewListCtrl(
                list_panel,
                style=dv.DV_SINGLE | dv.DV_ROW_LINES | dv.DV_VERT_RULES,
            )
            self._list.AppendTextColumn("Od", width=70, mode=dv.DATAVIEW_CELL_INERT)
            self._list.AppendTextColumn("Tytuł", width=740, mode=dv.DATAVIEW_CELL_INERT)
            self._list.Bind(dv.EVT_DATAVIEW_SELECTION_CHANGED, self._on_item_selected)
        else:
            self._list = wx.ListCtrl(list_panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
            self._list.InsertColumn(0, "Od", width=70)
            self._list.InsertColumn(1, "Tytuł", width=740)
            self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_item_selected)
        self._list.SetName("Programy archiwalne")
        self._list.Bind(wx.EVT_KEY_DOWN, self._on_list_key_down)
        list_sizer.Add(self._list, 1, wx.EXPAND)
        list_panel.SetSizer(list_sizer)

        details_sizer = wx.BoxSizer(wx.VERTICAL)
        details_sizer.Add(wx.StaticText(details_panel, label="Opis:"), 0, wx.BOTTOM, 4)
        self._details = wx.TextCtrl(details_panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self._details.Bind(wx.EVT_KEY_DOWN, self._on_last_control_key_down)
        details_sizer.Add(self._details, 1, wx.EXPAND)
        details_panel.SetSizer(details_sizer)

        right_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer.Add(right_splitter, 1, wx.EXPAND)
        right.SetSizer(right_sizer)

        root.Add(splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(root)

    def _on_last_control_key_down(self, evt: wx.KeyEvent) -> None:
        if evt.GetKeyCode() == wx.WXK_ESCAPE and not evt.HasAnyModifiers():
            if hasattr(self, "_list"):
                self._list.SetFocus()
                return
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

    def _on_list_key_down(self, evt: wx.KeyEvent) -> None:
        key = evt.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            idx = self._ensure_list_selection()
            if idx is None:
                return
            self._show_item_details(idx)
            self._details.SetFocus()
            return
        if key == wx.WXK_ESCAPE and not evt.HasAnyModifiers():
            self._nav.SetFocus()
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
        if hasattr(self, "_details"):
            self._details.SetValue("")
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
        if time.monotonic() < self._nav_ignore_activate_until:
            self._nav_ignore_activate_until = 0.0
            return
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
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._nav_ignore_activate_until = time.monotonic() + 0.3
            self._advance_from_nav()
            return
        elif key == wx.WXK_ESCAPE and not evt.HasAnyModifiers():
            self._collapse_selected()
            return
        elif key == wx.WXK_SPACE and not evt.HasAnyModifiers():
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

    def _advance_from_nav(self) -> None:
        row = self._get_selected_nav_row()
        if not row:
            return

        if row.expandable:
            if not row.expanded:
                self._set_expanded(row.key, expanded=True)
                if row.data:
                    self._maybe_start_loading(row.data)
            child_idx = self._find_first_child_index(row.key, skip_placeholders=True)
            if child_idx is not None:
                self._set_nav_selection(child_idx)
            return

        if row.data and row.data.kind == "station" and row.data.source and row.data.day:
            self._list.SetFocus()
            return

        if row.is_placeholder:
            self._show_list_message(row.label)

    def _set_nav_selection(self, idx: int) -> None:
        if idx == wx.NOT_FOUND:
            return
        if idx < 0 or idx >= self._nav.GetCount():
            return

        self._suppress_nav_event = True
        self._nav.SetSelection(idx)
        self._nav.EnsureVisible(idx)
        self._suppress_nav_event = False
        self._on_nav_selection(wx.CommandEvent())

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

    def _append_list_row(self, values: list[str]) -> None:
        if getattr(self, "_list_is_dataview", False):
            self._list.AppendItem(values)  # type: ignore[attr-defined]
            return
        row = self._list.InsertItem(self._list.GetItemCount(), values[0] if values else "")  # type: ignore[attr-defined]
        for col, value in enumerate(values[1:], start=1):
            self._list.SetItem(row, col, value)  # type: ignore[attr-defined]

    def _show_list_message(self, message: str) -> None:
        self._list.DeleteAllItems()
        if hasattr(self, "_details"):
            self._details.SetValue("")
        self._append_list_row(["", message])

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

    def _find_first_child_index(self, parent_key: str, *, skip_placeholders: bool = False) -> int | None:
        parent_idx = self._nav_index_by_key.get(parent_key)
        if parent_idx is None:
            return None
        parent_level = self._nav_rows[parent_idx].level
        for idx in range(parent_idx + 1, len(self._nav_rows)):
            row = self._nav_rows[idx]
            if row.level <= parent_level:
                break
            if row.parent_key == parent_key:
                if skip_placeholders and row.is_placeholder:
                    continue
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
        if hasattr(self, "_details"):
            self._details.SetValue("")
        self._status_bar.SetStatusText(f"Pobieranie: {source.name} {day.isoformat()}…")

        def work() -> list[ScheduleItem]:
            return self._provider.get_schedule(source, day, force_refresh=force)

        def on_success(items: list[ScheduleItem]) -> None:
            if token != self._request_token:
                return
            self._show_schedule(items)
            if self._search_index:
                try:
                    self._search_index.add_items("archive", items)
                except Exception:  # noqa: BLE001
                    pass
            self._status_bar.SetStatusText("Gotowe.")

        self._run_in_thread(work, on_success=on_success, on_error=self._on_error)

    def _show_schedule(self, items: list[ScheduleItem]) -> None:
        self._items = list(items)
        self._list.DeleteAllItems()
        for it in items:
            start = it.start_time.strftime("%H:%M") if it.start_time else ""
            title = it.title
            if it.subtitle:
                title = f"{title} — {it.subtitle}"
            self._append_list_row([start, title])

    def _on_item_selected(self, evt: wx.Event) -> None:
        if hasattr(evt, "GetIndex"):
            idx = int(evt.GetIndex())  # type: ignore[attr-defined]
        elif getattr(self, "_list_is_dataview", False):
            idx = int(self._list.GetSelectedRow())  # type: ignore[attr-defined]
        else:
            idx = wx.NOT_FOUND
        self._show_item_details(idx)

    def _show_item_details(self, idx: int) -> None:
        if idx < 0:
            return
        if not hasattr(self, "_items") or idx >= len(self._items):
            return
        item = self._items[idx]
        self._details.SetValue(_format_archive_item_details(item))

    def _ensure_list_selection(self) -> int | None:
        idx = self._get_selected_list_index()
        if idx is not None and idx >= 0:
            return idx
        count = int(getattr(self._list, "GetItemCount")())  # type: ignore[misc]
        if count <= 0:
            return None
        self._select_list_index(0)
        return 0

    def _get_selected_list_index(self) -> int | None:
        if getattr(self, "_list_is_dataview", False):
            idx = int(self._list.GetSelectedRow())  # type: ignore[attr-defined]
            return idx if idx >= 0 else None

        idx = int(self._list.GetNextItem(-1, wx.LIST_NEXT_ALL, wx.LIST_STATE_SELECTED))  # type: ignore[attr-defined]
        return idx if idx != wx.NOT_FOUND else None

    def _select_list_index(self, idx: int) -> None:
        if idx < 0:
            return
        if getattr(self, "_list_is_dataview", False):
            self._list.SelectRow(idx)  # type: ignore[attr-defined]
            try:
                self._list.EnsureVisible(self._list.RowToItem(idx))  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            return

        self._list.SetItemState(  # type: ignore[attr-defined]
            idx,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
        )
        try:
            self._list.EnsureVisible(idx)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

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
        self._show_list_message(f"Błąd: {msg}")
