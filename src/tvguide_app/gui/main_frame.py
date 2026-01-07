from __future__ import annotations

from datetime import date
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import threading

import wx
from platformdirs import user_cache_dir, user_data_dir

from tvguide_app.core.cache import SqliteCache
from tvguide_app.core.http import HttpClient
from tvguide_app.core.provider_packs.loader import PackStore
from tvguide_app.core.provider_packs.service import ProviderPackService
from tvguide_app.core.providers.fandom_archive import FandomArchiveProvider
from tvguide_app.core.providers.polskieradio import PolskieRadioProvider
from tvguide_app.core.providers.teleman import TelemanProvider
from tvguide_app.gui.schedule_tabs import ArchiveTab, RadioTab, TvTab


class MainFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="Programista", size=(1100, 700))
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        cache_path = self._default_cache_path()
        self._cache = SqliteCache(cache_path)
        self._cache.prune_expired()

        self._http = HttpClient(self._cache, user_agent="programista/0.1 (+desktop)")

        self._providers = ProviderPackService(
            self._http,
            base_url="https://github.com/michaldziwisz/programista-providers/releases/latest/download/",
            store=PackStore(self._default_providers_path()),
            app_version=self._app_version(),
            fallback_tv=TelemanProvider(self._http),
            fallback_radio=PolskieRadioProvider(self._http),
            fallback_archive=FandomArchiveProvider(self._http, year=date.today().year),
        )
        self._providers.load_installed()

        self._status_bar = self.CreateStatusBar()

        self._build_menu()
        self._build_ui()
        self._auto_update_providers()

    @staticmethod
    def _default_cache_path() -> Path:
        path = Path(user_cache_dir("Programista", "Programista"))
        path.mkdir(parents=True, exist_ok=True)
        return path / "cache.sqlite3"

    @staticmethod
    def _default_providers_path() -> Path:
        path = Path(user_data_dir("Programista", "Programista")) / "providers"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _app_version() -> str:
        try:
            from tvguide_app import __version__

            return __version__
        except Exception:  # noqa: BLE001
            pass

        try:
            return version("programista")
        except PackageNotFoundError:
            return "0.0.0"

    def _build_menu(self) -> None:
        menubar = wx.MenuBar()

        file_menu = wx.Menu()
        exit_item = file_menu.Append(wx.ID_EXIT, "Zakończ\tAlt+F4")
        self.Bind(wx.EVT_MENU, lambda _evt: self.Close(True), exit_item)

        data_menu = wx.Menu()
        refresh_item = data_menu.Append(wx.ID_REFRESH, "Odśwież\tF5")
        force_item = data_menu.Append(wx.ID_ANY, "Wymuś odświeżenie\tCtrl+R")
        update_providers_item = data_menu.Append(wx.ID_ANY, "Aktualizuj dostawców\tCtrl+U")
        clear_cache_item = data_menu.Append(wx.ID_ANY, "Wyczyść cache")

        self.Bind(wx.EVT_MENU, self._on_refresh, refresh_item)
        self.Bind(wx.EVT_MENU, self._on_force_refresh, force_item)
        self.Bind(wx.EVT_MENU, self._on_update_providers, update_providers_item)
        self.Bind(wx.EVT_MENU, self._on_clear_cache, clear_cache_item)

        menubar.Append(file_menu, "Plik")
        menubar.Append(data_menu, "Dane")
        self.SetMenuBar(menubar)

    def _build_ui(self) -> None:
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self._notebook = wx.Notebook(panel)

        self._tv_tab = TvTab(self._notebook, self._providers.runtime.tv, self._status_bar)
        self._radio_tab = RadioTab(self._notebook, self._providers.runtime.radio, self._status_bar)
        self._archive_tab = ArchiveTab(self._notebook, self._providers.runtime.archive, self._status_bar)

        self._notebook.AddPage(self._tv_tab, "Telewizja")
        self._notebook.AddPage(self._radio_tab, "Radio")
        self._notebook.AddPage(self._archive_tab, "Programy archiwalne")

        sizer.Add(self._notebook, 1, wx.EXPAND)
        panel.SetSizer(sizer)

    def _on_char_hook(self, evt: wx.KeyEvent) -> None:
        if (
            evt.GetKeyCode() == wx.WXK_TAB
            and evt.ShiftDown()
            and not evt.ControlDown()
            and not evt.AltDown()
        ):
            focused = wx.Window.FindFocus()
            win: wx.Window | None = focused
            while win is not None:
                if win.Navigate(flags=wx.NavigationKeyEvent.IsBackward):
                    return
                win = win.GetParent()
        evt.Skip()

    def _active_tab(self):
        idx = self._notebook.GetSelection()
        return self._notebook.GetPage(idx)

    def _on_refresh(self, _evt: wx.CommandEvent) -> None:
        tab = self._active_tab()
        if hasattr(tab, "refresh"):
            tab.refresh(force=False)

    def _on_force_refresh(self, _evt: wx.CommandEvent) -> None:
        tab = self._active_tab()
        if hasattr(tab, "refresh"):
            tab.refresh(force=True)

    def _on_clear_cache(self, _evt: wx.CommandEvent) -> None:
        self._cache.clear()
        self._status_bar.SetStatusText("Wyczyszczono cache.")
        tab = self._active_tab()
        if hasattr(tab, "refresh_all"):
            tab.refresh_all(force=True)

    def _refresh_all_tabs(self) -> None:
        self._tv_tab.refresh_all(force=False)
        self._radio_tab.refresh_all(force=False)
        self._archive_tab.refresh_all(force=False)

    def _auto_update_providers(self) -> None:
        self._status_bar.SetStatusText("Sprawdzanie aktualizacji dostawców…")
        self._run_in_thread(
            lambda: self._providers.update_and_reload(force_check=False),
            on_success=self._on_providers_updated,
            on_error=self._on_providers_update_error,
        )

    def _on_update_providers(self, _evt: wx.CommandEvent) -> None:
        self._status_bar.SetStatusText("Aktualizowanie dostawców…")
        self._run_in_thread(
            lambda: self._providers.update_and_reload(force_check=True),
            on_success=self._on_providers_updated,
            on_error=self._on_providers_update_error,
        )

    def _on_providers_updated(self, result) -> None:
        self._status_bar.SetStatusText(getattr(result, "message", "Gotowe."))
        if getattr(result, "updated", None):
            self._refresh_all_tabs()

    def _on_providers_update_error(self, exc: Exception) -> None:
        msg = str(exc) or "Nieznany błąd."
        self._status_bar.SetStatusText(f"Błąd aktualizacji dostawców: {msg}")

    def _run_in_thread(self, work, *, on_success, on_error) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                wx.CallAfter(on_error, e)
                return
            wx.CallAfter(on_success, result)

        threading.Thread(target=runner, daemon=True).start()
