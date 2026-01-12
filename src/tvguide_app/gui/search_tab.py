from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

import wx

from tvguide_app.core.hub_api import HubClient
from tvguide_app.core.models import ACCESSIBILITY_FEATURE_LABELS, AccessibilityFeature, ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.search_index import SearchIndex, SearchKind, SearchResult
from tvguide_app.core.settings import SearchKindFilters, SettingsStore
from tvguide_app.gui.schedule_tabs import BaseScheduleTab


SEARCH_KIND_LABELS: dict[SearchKind, str] = {
    "tv": "Telewizja",
    "radio": "Radio",
    "tv_accessibility": "TV z udogodnieniami",
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


def _format_accessibility(features: tuple[AccessibilityFeature, ...]) -> str:
    if not features:
        return ""
    labels: list[str] = []
    for f in features:
        labels.append(ACCESSIBILITY_FEATURE_LABELS.get(f, str(f)))
    return ", ".join(labels)


def _parse_hhmm(value: str) -> time | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        if len(value) == 5:
            return time.fromisoformat(value)
        return time.fromisoformat(value[:8])
    except ValueError:
        return None


class _SearchResultsProvider(ScheduleProvider):
    def __init__(self, *, hub: HubClient | None) -> None:
        self._hub = hub
        self._sources: list[Source] = []
        self._days: list[date] = []
        self._items_by_pair: dict[tuple[str, str, date], list[ScheduleItem]] = {}
        self._days_by_source: dict[tuple[str, str], list[date]] = {}
        self._sources_by_day: dict[date, list[Source]] = {}
        self._kind_by_provider_id: dict[str, SearchKind] = {}

    @property
    def provider_id(self) -> str:
        return "search"

    @property
    def display_name(self) -> str:
        return "Wyszukiwanie"

    def set_results(self, results: list[SearchResult]) -> None:
        kinds_by_provider_id: dict[str, SearchKind] = {}
        source_names_by_key: dict[tuple[str, str], str] = {}
        for r in results:
            pid = str(r.provider_id)
            kinds_by_provider_id.setdefault(pid, r.kind)
            src_key = (pid, str(r.source_id))
            if src_key not in source_names_by_key:
                source_names_by_key[src_key] = str(r.source_name)

        name_counts: dict[str, int] = {}
        for name in source_names_by_key.values():
            key = str(name).casefold()
            name_counts[key] = name_counts.get(key, 0) + 1

        sources_by_key: dict[tuple[str, str], Source] = {}
        for (pid, sid), name in source_names_by_key.items():
            label = str(name)
            if name_counts.get(label.casefold(), 0) > 1:
                kind = kinds_by_provider_id.get(pid)
                kind_label = SEARCH_KIND_LABELS.get(kind, str(kind or "")).strip()
                if kind_label:
                    label = f"{label} ({kind_label})"
            sources_by_key[(pid, sid)] = Source(provider_id=ProviderId(pid), id=SourceId(sid), name=label)

        items_by_pair: dict[tuple[str, str, date], list[ScheduleItem]] = {}
        days_by_source: dict[tuple[str, str], set[date]] = {}
        sources_by_day: dict[date, set[Source]] = {}

        for r in results:
            pid = str(r.provider_id)
            sid = str(r.source_id)
            src = sources_by_key.get((pid, sid))
            if not src:
                continue

            start_time = _parse_hhmm(r.start)
            item = ScheduleItem(
                provider_id=ProviderId(pid),
                source=src,
                day=r.day,
                start_time=start_time,
                end_time=None,
                title=r.title,
                subtitle=r.subtitle,
                details_ref=r.details_ref,
                details_summary=r.details_summary,
                accessibility=r.accessibility,
            )

            items_by_pair.setdefault((pid, sid, r.day), []).append(item)
            days_by_source.setdefault((pid, sid), set()).add(r.day)
            sources_by_day.setdefault(r.day, set()).add(src)

        for key, items in items_by_pair.items():
            items.sort(key=lambda it: (it.start_time or time.min, it.title.casefold()))

        self._sources = sorted(sources_by_key.values(), key=lambda s: s.name.casefold())
        all_days: set[date] = set()
        for s in days_by_source.values():
            all_days.update(s)
        self._days = sorted(all_days)
        self._items_by_pair = items_by_pair
        self._days_by_source = {k: sorted(v) for k, v in days_by_source.items()}
        self._sources_by_day = {d: sorted(srcs, key=lambda s: s.name.casefold()) for d, srcs in sources_by_day.items()}
        self._kind_by_provider_id = kinds_by_provider_id

    def kind_for_provider_id(self, provider_id: str) -> SearchKind | None:
        return self._kind_by_provider_id.get((provider_id or "").strip())

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        return list(self._sources)

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        return list(self._days)

    def list_days_for_source(self, source: Source) -> list[date]:
        key = (str(source.provider_id), str(source.id))
        return list(self._days_by_source.get(key, []))

    def list_sources_for_day(self, day: date) -> list[Source]:
        return list(self._sources_by_day.get(day, []))

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        key = (str(source.provider_id), str(source.id), day)
        return list(self._items_by_pair.get(key, []))

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        if item.details_ref and self._hub:
            try:
                text = self._hub.get_details_text(str(item.provider_id), item.details_ref)
            except Exception:  # noqa: BLE001
                text = None
            if text:
                return text

        if item.details_summary:
            return item.details_summary

        parts: list[str] = []
        if item.subtitle:
            parts.append(item.subtitle)
        if item.accessibility:
            parts.append(f"Udogodnienia: {_format_accessibility(item.accessibility)}")
        return "\n".join(parts).strip() or "Brak szczegółów."


class SearchTab(BaseScheduleTab):
    def __init__(
        self,
        parent: wx.Window,
        status_bar: wx.StatusBar,
        *,
        settings_store: SettingsStore,
        search_index: SearchIndex,
        hub: HubClient | None = None,
    ) -> None:
        self._status_bar = status_bar
        self._settings_store = settings_store
        self._index = search_index
        self._hub = hub
        self._results: list[SearchResult] = []
        self._search_query: str = ""
        self._search_kinds: set[SearchKind] = set()
        self._search_mode: str | None = None
        self._hub_cursor: int | None = None
        self._has_more: bool = False
        self._is_loading: bool = False
        self._filters = self._load_persisted_filters()
        self._provider = _SearchResultsProvider(hub=hub)

        super().__init__(parent, self._provider, status_bar)

    def _load_persisted_filters(self) -> _UiFilters:
        persisted = self._settings_store.get_search_kind_filters()
        return _UiFilters(
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

    def refresh(self, *, force: bool) -> None:
        self.refresh_all(force=force)

    def refresh_all(self, *, force: bool) -> None:
        query = ""
        if hasattr(self, "_query"):
            query = self._query.GetValue()

        if (query or "").strip():
            self._run_search()
            return

        self._results = []
        self._provider.set_results([])
        self._sources = []
        self._days = []
        self._days_by_provider_id = {}
        self._expanded_by_source.clear()
        self._expanded_by_day.clear()
        self._list.DeleteAllItems()
        self._details.SetValue("")
        self._rebuild_nav()
        self._status_bar.SetStatusText("Wpisz frazę i naciśnij Szukaj.")
        self._search_mode = None
        self._hub_cursor = None
        self._has_more = False
        self._update_paging_controls()

    def _create_header_controls(self, header: wx.BoxSizer) -> None:
        panel = wx.BoxSizer(wx.VERTICAL)

        search_row = wx.BoxSizer(wx.HORIZONTAL)
        search_row.Add(wx.StaticText(self, label="Szukaj:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._query = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self._query.Bind(wx.EVT_TEXT_ENTER, lambda _evt: self._run_search())
        search_row.Add(self._query, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._search_btn = wx.Button(self, label="Szukaj")
        self._search_btn.Bind(wx.EVT_BUTTON, lambda _evt: self._run_search())
        search_row.Add(self._search_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        panel.Add(search_row, 0, wx.EXPAND | wx.BOTTOM, 6)

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

        panel.Add(filters_box, 0, wx.EXPAND | wx.BOTTOM, 6)

        hint = wx.StaticText(self, label="Wyszukiwanie online (jeśli dostępne) z fallbackiem na lokalny cache.")
        panel.Add(hint, 0, wx.BOTTOM, 6)

        results_row = wx.BoxSizer(wx.HORIZONTAL)
        self._results_label = wx.StaticText(self, label="Wyniki: 0")
        results_row.Add(self._results_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._more_btn = wx.Button(self, label="Pokaż więcej")
        self._more_btn.Disable()
        self._more_btn.Bind(wx.EVT_BUTTON, lambda _evt: self._load_more())
        results_row.Add(self._more_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        panel.Add(results_row, 0, wx.EXPAND)

        header.Add(panel, 1, wx.EXPAND)

    def _init_list_columns(self, list_ctrl: wx.Window) -> None:
        self._show_end_time = False
        self._add_list_column(list_ctrl, "Od", width=70)
        self._add_list_column(list_ctrl, "Tytuł", width=420)
        self._add_list_column(list_ctrl, "Typ", width=170)
        self._add_list_column(list_ctrl, "Udogodnienia", width=240)

    def _show_schedule(self, items: list[ScheduleItem]) -> None:
        self._items = items
        self._list.DeleteAllItems()
        for it in items:
            start = it.start_time.strftime("%H:%M") if it.start_time else ""
            kind = self._provider.kind_for_provider_id(str(it.provider_id))
            self._append_list_row(
                [
                    start,
                    it.title,
                    SEARCH_KIND_LABELS.get(kind, str(kind or "")),
                    _format_accessibility(it.accessibility),
                ]
            )

    def _nav_child_days_for_source(self, source: Source) -> list[date]:
        return self._provider.list_days_for_source(source)

    def _nav_child_sources_for_day(self, day: date) -> list[Source]:
        return self._provider.list_sources_for_day(day)

    def _on_filter_changed(self, _evt: wx.CommandEvent) -> None:
        self._filters = _UiFilters(
            tv=bool(self._tv_cb.IsChecked()),
            radio=bool(self._radio_cb.IsChecked()),
            tv_accessibility=bool(self._tv_a11y_cb.IsChecked()),
            archive=bool(self._archive_cb.IsChecked()),
        )
        self._persist_filters()

    def _run_search(self) -> None:
        query = (self._query.GetValue() or "").strip()
        kinds = self._filters.selected()

        if not query:
            self.refresh_all(force=False)
            return

        self._search_query = query
        self._search_kinds = set(kinds)
        self._search_mode = None
        self._hub_cursor = None
        self._has_more = False
        self._results = []
        self._provider.set_results([])
        self._sources = []
        self._days = []
        self._days_by_provider_id = {}
        self._expanded_by_source.clear()
        self._expanded_by_day.clear()
        self._list.DeleteAllItems()
        self._details.SetValue("")
        self._rebuild_nav()
        self._update_paging_controls()

        self._fetch_next_page(reset=True)

    def _page_size(self) -> int:
        # The archive is huge; keep the page smaller for speed and to avoid noisy results.
        return 100 if "archive" in self._search_kinds else 200

    def _update_paging_controls(self) -> None:
        if hasattr(self, "_results_label"):
            self._results_label.SetLabel(f"Wyniki: {len(self._results)}")
        if hasattr(self, "_more_btn"):
            self._more_btn.Enable(bool(self._search_mode == "online" and self._has_more and not self._is_loading))

    def _load_more(self) -> None:
        if self._is_loading:
            return
        if not self._has_more:
            return
        self._fetch_next_page(reset=False)

    def _fetch_next_page(self, *, reset: bool) -> None:
        if self._is_loading:
            return
        query = (self._search_query or "").strip()
        if not query:
            return

        page_size = self._page_size()
        cursor = None if reset else self._hub_cursor
        kinds = set(self._search_kinds)

        def work() -> tuple[str, list[SearchResult], str | None]:
            if self._hub:
                try:
                    results = self._hub.search(query, kinds=kinds, limit=page_size, cursor=cursor)
                    return ("online", results, None)
                except Exception as e:  # noqa: BLE001
                    if reset:
                        results = self._index.search(query, kinds=kinds)
                        return ("local", results, str(e) or "Błąd wyszukiwania online.")
                    raise

            results = self._index.search(query, kinds=kinds)
            return ("local", results, None)

        def on_success(result: tuple[str, list[SearchResult], str | None]) -> None:
            mode, results, warn = result
            self._search_mode = mode

            if mode == "online":
                ids = [r.item_id for r in results if isinstance(r.item_id, int)]
                next_cursor = min(ids) if ids else None
                has_more = len(results) >= page_size and next_cursor is not None
                if reset:
                    merged = list(results)
                else:
                    merged = list(self._results) + list(results)

                # Deduplicate just in case (shouldn't happen with cursor paging, but safe).
                seen: set[tuple[str, str, str, str, str, str]] = set()
                deduped: list[SearchResult] = []
                for r in merged:
                    key = (
                        str(r.kind),
                        str(r.provider_id),
                        str(r.source_id),
                        r.day.isoformat(),
                        str(r.start),
                        str(r.title).casefold(),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(r)

                self._results = deduped
                self._hub_cursor = next_cursor
                self._has_more = has_more
            else:
                # Local results are limited to what was already browsed/cached.
                self._results = results
                self._hub_cursor = None
                self._has_more = False

            self._provider.set_results(self._results)
            self._sources = self._provider.list_sources(force_refresh=False)
            self._days = self._provider.list_days(force_refresh=False)
            self._days_by_provider_id = {}
            if reset:
                self._expanded_by_source.clear()
                self._expanded_by_day.clear()

            self._list.DeleteAllItems()
            self._details.SetValue("")
            self._rebuild_nav()
            if not reset:
                self._refresh_schedule(force=False)

            if not self._results and query.strip():
                base = "Brak wyników"
                suffix = " (online)." if mode == "online" else " (w pobranych danych)."
                self._status_bar.SetStatusText(base + suffix)
            else:
                prefix = "Wyniki (online)" if mode == "online" else "Wyniki (lokalnie)"
                msg = f"{prefix}: {len(self._results)}"
                if mode == "online" and self._has_more:
                    msg += f" • następna strona"
                if warn and mode != "online":
                    msg += f" • {warn}"
                self._status_bar.SetStatusText(msg)

            self._search_btn.Enable(True)
            self._is_loading = False
            self._update_paging_controls()

        def on_error(exc: Exception) -> None:
            self._search_btn.Enable(True)
            self._is_loading = False
            msg = str(exc) or "Nieznany błąd."
            self._status_bar.SetStatusText(f"Błąd wyszukiwania: {msg}")
            self._update_paging_controls()

        self._search_btn.Enable(False)
        self._is_loading = True
        self._update_paging_controls()
        self._status_bar.SetStatusText("Wyszukiwanie…")
        self._run_in_thread(work, on_success=on_success, on_error=on_error)
