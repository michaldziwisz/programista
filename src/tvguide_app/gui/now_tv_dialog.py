from __future__ import annotations

import sys
import threading
import weakref
from typing import Callable, Protocol, TypeVar

import wx
import wx.dataview as dv

from tvguide_app.core.models import NowTvItem


class NowTvProvider(Protocol):
    def get_now_schedule(self, *, force_refresh: bool = False) -> list[NowTvItem]: ...

    def get_now_item_details(
        self,
        item: NowTvItem,
        *,
        force_refresh: bool = False,
    ) -> str: ...


_T = TypeVar("_T")

# Opoznienie (ms) po zmianie zaznaczenia, po ktorym dociagamy z sieci pelny opis.
# Przy szybkim przewijaniu strzalkami kolejne zmiany restartuja timer, wiec siec
# odpytujemy tylko dla pozycji, na ktorej uzytkownik faktycznie sie zatrzyma.
_DETAILS_DEBOUNCE_MS = 300


class NowTvDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, provider: NowTvProvider) -> None:
        super().__init__(
            parent,
            title="Teraz w TV",
            size=(950, 650),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self._provider = provider
        self._items: list[NowTvItem] = []
        self._request_token = 0
        self._details_token = 0
        self._closed = False
        self._first_load = True
        # Debounce pobierania pelnego opisu: przy przewijaniu strzalkami po liscie
        # podstawowe info pokazujemy od razu, a siec odpytujemy dopiero po chwili
        # bezruchu na danej pozycji (mniej zbednych zadan, plynne czytanie NVDA).
        self._pending_detail_index: int | None = None
        self._details_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_details_timer, self._details_timer)

        self._build_ui()
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy)
        self.CentreOnParent()

    def _build_ui(self) -> None:
        root = wx.BoxSizer(wx.VERTICAL)

        intro = wx.StaticText(
            self,
            label=(
                "Programy emitowane teraz oraz rozpoczynające się w ciągu najbliższych "
                "dwóch godzin."
            ),
        )
        root.Add(intro, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        self._status = wx.StaticText(self, label="Gotowe do pobrania danych.")
        self._status.SetName("Stan pobierania programów")
        root.Add(self._status, 0, wx.EXPAND | wx.ALL, 10)

        self._list_is_dataview = sys.platform == "darwin"
        if self._list_is_dataview:
            self._list = dv.DataViewListCtrl(
                self,
                style=dv.DV_SINGLE | dv.DV_ROW_LINES | dv.DV_VERT_RULES,
            )
        else:
            self._list = wx.ListCtrl(
                self,
                style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES,
            )
        self._list.SetName("Programy teraz i za chwilę w TV")
        columns = [
            ("Status", 90),
            ("Od", 65),
            ("Do", 65),
            ("Stacja", 150),
            ("Tytuł", 360),
            ("Rodzaj lub opis", 180),
        ]
        for index, (label, width) in enumerate(columns):
            if self._list_is_dataview:
                self._list.AppendTextColumn(  # type: ignore[attr-defined]
                    label,
                    width=width,
                    mode=dv.DATAVIEW_CELL_INERT,
                )
            else:
                self._list.InsertColumn(index, label, width=width)  # type: ignore[attr-defined]
        if self._list_is_dataview:
            self._list.Bind(dv.EVT_DATAVIEW_SELECTION_CHANGED, self._on_item_selected)
            self._list.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, self._on_item_activated)
        else:
            self._list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_item_selected)
            self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_item_activated)
        self._list.Bind(wx.EVT_KEY_DOWN, self._on_list_key_down)
        root.Add(self._list, 3, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        details_label = wx.StaticText(self, label="Szczegóły programu:")
        root.Add(details_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        self._details = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
        )
        self._details.SetName("Szczegóły programu")
        root.Add(self._details, 2, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self._refresh_button = wx.Button(self, label="&Odśwież")
        self._refresh_button.SetName("Odśwież dane Teraz w TV")
        self._refresh_button.SetToolTip("Pobierz ponownie aktualne dane z Teleman.pl")
        self._refresh_button.Bind(wx.EVT_BUTTON, self._on_refresh)
        buttons.Add(self._refresh_button, 0, wx.RIGHT, 8)

        close_button = wx.Button(self, wx.ID_CANCEL, "Zamknij")
        close_button.Bind(wx.EVT_BUTTON, lambda _evt: self.Close())
        buttons.Add(close_button, 0)
        root.Add(buttons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.SetEscapeId(wx.ID_CANCEL)
        self.SetMinSize((720, 480))
        self.SetSizer(root)

    def start_loading(self) -> None:
        self._load(force=False)

    def _on_refresh(self, _evt: wx.CommandEvent) -> None:
        self._load(force=True)

    def _load(self, *, force: bool) -> None:
        if self._closed:
            return
        self._request_token += 1
        token = self._request_token
        self._details_token += 1

        self._refresh_button.Disable()
        self._status.SetLabel("Pobieranie danych z Teleman.pl…")
        self._list.DeleteAllItems()
        self._details.SetValue("Pobieranie programów. To może potrwać kilka sekund.")

        self._run_in_thread(
            lambda: self._provider.get_now_schedule(force_refresh=force),
            on_success=lambda items: self._on_loaded(token, items),
            on_error=lambda exc: self._on_load_error(token, exc),
        )

    def _on_loaded(self, token: int, items: list[NowTvItem]) -> None:
        if self._closed or token != self._request_token:
            return
        self._refresh_button.Enable()
        self._items = list(items)
        self._list.Freeze()
        try:
            self._list.DeleteAllItems()
            for item in self._items:
                values = [
                    "Teraz" if item.is_current else "Za chwilę",
                    _format_time(item.start_time),
                    _format_time(item.end_time),
                    item.source.name,
                    item.title,
                    item.subtitle or "",
                ]
                if self._list_is_dataview:
                    self._list.AppendItem(values)  # type: ignore[attr-defined]
                else:
                    row = self._list.InsertItem(  # type: ignore[attr-defined]
                        self._list.GetItemCount(),  # type: ignore[attr-defined]
                        values[0],
                    )
                    for column, value in enumerate(values[1:], start=1):
                        self._list.SetItem(row, column, value)  # type: ignore[attr-defined]
        finally:
            self._list.Thaw()

        current_count = sum(1 for item in self._items if item.is_current)
        self._status.SetLabel(
            f"Załadowano programy: {len(self._items)}. Emitowane teraz: {current_count}."
        )

        if not self._items:
            self._details.SetValue("Teleman.pl nie zwrócił żadnych programów.")
            return

        self._select_item(0)
        if self._list_is_dataview:
            # SelectRow does not emit EVT_DATAVIEW_SELECTION_CHANGED on macOS.
            self._show_item_details(0)
        if self._first_load and self.IsActive():
            self._list.SetFocus()
        self._first_load = False

    def _on_load_error(self, token: int, exc: Exception) -> None:
        if self._closed or token != self._request_token:
            return
        self._refresh_button.Enable()
        self._items = []
        self._list.DeleteAllItems()
        message = _friendly_error_message(exc)
        self._status.SetLabel("Nie udało się pobrać danych.")
        self._details.SetValue(f"Błąd:\n{message}\n\nUżyj przycisku Odśwież, aby spróbować ponownie.")
        if self.IsActive():
            self._details.SetFocus()
        self._first_load = False

    def _on_item_selected(self, evt: wx.Event) -> None:
        index = self._event_index(evt)
        if index is not None:
            self._preview_item(index)

    def _on_item_activated(self, evt: wx.Event) -> None:
        index = self._event_index(evt)
        if index is not None:
            self._show_item_details(index)
            self._details.SetFocus()

    def _on_list_key_down(self, evt: wx.KeyEvent) -> None:
        if evt.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            index = self._selected_index()
            if index is not None:
                self._show_item_details(index)
                self._details.SetFocus()
                return
        evt.Skip()

    def _preview_item(self, index: int) -> None:
        # Zmiana zaznaczenia (np. strzalkami): pokaz podstawowe info natychmiast,
        # a pelny opis z sieci dociagnij dopiero po chwili bezruchu (debounce).
        if index < 0 or index >= len(self._items):
            return
        item = self._items[index]
        summary = format_now_tv_item(item)
        # Uniewaznij ewentualne pobranie w toku - wynik dla innej pozycji odrzucimy.
        self._details_token += 1
        self._details_timer.Stop()

        if not item.details_ref:
            self._pending_detail_index = None
            self._details.SetValue(summary)
            return

        self._pending_detail_index = index
        self._details.SetValue(summary)
        self._details_timer.StartOnce(_DETAILS_DEBOUNCE_MS)

    def _on_details_timer(self, _evt: wx.TimerEvent) -> None:
        index = self._pending_detail_index
        self._pending_detail_index = None
        if index is None or self._closed:
            return
        self._show_item_details(index)

    def _show_item_details(self, index: int) -> None:
        # Natychmiastowe pobranie pelnego opisu (aktywacja/Enter albo po debounce).
        if index < 0 or index >= len(self._items):
            return
        self._details_timer.Stop()
        self._pending_detail_index = None
        item = self._items[index]
        summary = format_now_tv_item(item)
        self._details_token += 1
        token = self._details_token

        if not item.details_ref:
            self._details.SetValue(summary)
            return

        self._details.SetValue(f"{summary}\n\nŁadowanie dodatkowego opisu…")
        self._run_in_thread(
            lambda: self._provider.get_now_item_details(item, force_refresh=False),
            on_success=lambda text: self._on_details_loaded(token, item, summary, text),
            on_error=lambda exc: self._on_details_error(token, summary, exc),
        )

    def _on_details_loaded(
        self,
        token: int,
        item: NowTvItem,
        summary: str,
        text: str,
    ) -> None:
        if self._closed or token != self._details_token:
            return
        details = str(text or "").strip()
        local_fallbacks = {item.title.strip(), (item.subtitle or "").strip()}
        if not details or details in local_fallbacks:
            self._details.SetValue(summary)
            return
        self._details.SetValue(f"{summary}\n\n{details}")

    def _on_details_error(self, token: int, summary: str, exc: Exception) -> None:
        if self._closed or token != self._details_token:
            return
        message = _friendly_error_message(exc)
        self._details.SetValue(f"{summary}\n\nNie udało się pobrać dodatkowego opisu: {message}")

    def _select_item(self, index: int) -> None:
        if index < 0 or index >= self._list.GetItemCount():
            return
        if self._list_is_dataview:
            self._list.SelectRow(index)  # type: ignore[attr-defined]
            try:
                item = self._list.RowToItem(index)  # type: ignore[attr-defined]
                self._list.EnsureVisible(item)
            except Exception:  # noqa: BLE001
                pass
            return
        self._list.SetItemState(
            index,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
            wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
        )
        self._list.EnsureVisible(index)

    def _selected_index(self) -> int | None:
        if self._list_is_dataview:
            index = int(self._list.GetSelectedRow())  # type: ignore[attr-defined]
            return index if index >= 0 else None
        index = self._list.GetNextItem(-1, wx.LIST_NEXT_ALL, wx.LIST_STATE_SELECTED)
        return int(index) if index != wx.NOT_FOUND else None

    def _event_index(self, evt: wx.Event) -> int | None:
        if hasattr(evt, "GetIndex"):
            index = int(evt.GetIndex())  # type: ignore[attr-defined]
            return index if index >= 0 else None
        return self._selected_index()

    def _run_in_thread(
        self,
        work: Callable[[], _T],
        *,
        on_success: Callable[[_T], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        dialog_ref = weakref.ref(self)

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                wx.CallAfter(_deliver_error, exc)
                return
            wx.CallAfter(_deliver_success, result)

        def _deliver_success(result: _T) -> None:
            dialog = dialog_ref()
            if dialog is None or dialog._closed or dialog.IsBeingDeleted():
                return
            on_success(result)

        def _deliver_error(exc: Exception) -> None:
            dialog = dialog_ref()
            if dialog is None or dialog._closed or dialog.IsBeingDeleted():
                return
            on_error(exc)

        threading.Thread(target=runner, daemon=True).start()

    def _on_close(self, _evt: wx.CloseEvent) -> None:
        if self._closed:
            return
        self._closed = True
        self._request_token += 1
        self._details_token += 1
        self._details_timer.Stop()
        self.Destroy()

    def _on_destroy(self, evt: wx.WindowDestroyEvent) -> None:
        if evt.GetEventObject() is self:
            self._closed = True
            self._request_token += 1
            self._details_token += 1
            self._details_timer.Stop()
        evt.Skip()


def format_now_tv_item(item: NowTvItem) -> str:
    status = "Emitowany teraz" if item.is_current else "Rozpocznie się za chwilę"
    start = _format_time(item.start_time) or "brak danych"
    end = _format_time(item.end_time) or "brak danych"
    lines = [
        f"Status: {status}",
        f"Stacja: {item.source.name}",
        f"Czas: {start}–{end}",
        f"Tytuł: {item.title}",
    ]
    if item.subtitle:
        lines.append(f"Rodzaj lub opis: {item.subtitle}")
    return "\n".join(lines)


def _format_time(value) -> str:
    return value.strftime("%H:%M") if value else ""


def _friendly_error_message(exc: Exception) -> str:
    message = " ".join(str(exc or "").split())
    if message:
        return message
    return "Nieznany błąd. Sprawdź połączenie z internetem i spróbuj ponownie."
