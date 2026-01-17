from __future__ import annotations

from datetime import date
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import threading

import wx
from platformdirs import user_cache_dir, user_data_dir

from tvguide_app.core.cache import SqliteCache
from tvguide_app.core.favorites import FavoritesStore
from tvguide_app.core.hub_api import HubClient
from tvguide_app.core.http import HttpClient
from tvguide_app.core.provider_packs.loader import PackStore
from tvguide_app.core.provider_packs.service import ProviderPackService
from tvguide_app.core.provider_packs.wrappers import EmptyScheduleProvider
from tvguide_app.core.providers.favorites import FavoritesProvider
from tvguide_app.core.providers.fandom_archive import FandomArchiveProvider
from tvguide_app.core.providers.polskieradio import PolskieRadioProvider
from tvguide_app.core.providers.teleman import TelemanProvider
from tvguide_app.core.schedule_cache import CachedArchiveProvider, CachedScheduleProvider
from tvguide_app.core.search_index import SearchIndex
from tvguide_app.core.settings import SettingsStore
from tvguide_app.gui.accessibility import install_notebook_accessible
from tvguide_app.gui.feedback_dialog import FeedbackDialog
from tvguide_app.gui.search_tab import SearchTab
from tvguide_app.gui.schedule_tabs import ArchiveTab, FavoritesTab, RadioTab, TvAccessibilityTab, TvTab


class MainFrame(wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title="Programista", size=(1100, 700))

        cache_path = self._default_cache_path()
        self._cache = SqliteCache(cache_path)
        self._cache.prune_expired()

        self._http = HttpClient(self._cache, user_agent="programista/0.1 (+desktop)")
        self._search_index = SearchIndex(cache_path.with_name("search.sqlite3"))
        self._search_index.prune()

        self._providers = ProviderPackService(
            self._http,
            base_url="https://github.com/michaldziwisz/programista-providers/releases/latest/download/",
            store=PackStore(self._default_providers_path()),
            app_version=self._app_version(),
            fallback_tv=TelemanProvider(self._http),
            fallback_tv_accessibility=EmptyScheduleProvider(),
            fallback_radio=PolskieRadioProvider(self._http),
            fallback_archive=FandomArchiveProvider(self._http, year=date.today().year),
        )
        self._providers.load_installed()

        self._tv_provider = CachedScheduleProvider(
            self._providers.runtime.tv,
            self._cache,
            kind="tv",
            ttl_seconds=6 * 3600,
        )
        self._tv_accessibility_provider = CachedScheduleProvider(
            self._providers.runtime.tv_accessibility,
            self._cache,
            kind="tv_accessibility",
            ttl_seconds=24 * 3600,
        )
        self._radio_provider = CachedScheduleProvider(
            self._providers.runtime.radio,
            self._cache,
            kind="radio",
            ttl_seconds=24 * 3600,
        )
        self._archive_provider = CachedArchiveProvider(
            self._providers.runtime.archive,
            self._cache,
            ttl_seconds=365 * 24 * 3600,
        )

        self._favorites_store = FavoritesStore(self._default_favorites_path())
        self._favorites_provider = FavoritesProvider(
            self._favorites_store,
            tv=self._tv_provider,
            radio=self._radio_provider,
        )

        self._settings_store = SettingsStore(self._default_settings_path())

        self._status_bar = self.CreateStatusBar()

        self._hub = HubClient(
            self._settings_store,
            app_version=self._app_version(),
            user_agent=f"programista/{self._app_version()} (+desktop)",
        )

        self._build_menu()
        self._build_ui()
        self._install_tab_shortcuts()
        self._auto_update_providers()
        self._ensure_hub_api_key()
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _on_close(self, evt: wx.CloseEvent) -> None:
        try:
            self._cache.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._search_index.close()
        except Exception:  # noqa: BLE001
            pass
        evt.Skip()

    def _install_tab_shortcuts(self) -> None:
        if not hasattr(self, "_notebook"):
            return

        entries: list[tuple[int, int, int]] = []
        max_tabs = min(self._notebook.GetPageCount(), 9)
        for idx in range(max_tabs):
            digit = str(idx + 1)
            cmd_id = wx.NewIdRef()
            self.Bind(wx.EVT_MENU, lambda _evt, i=idx: self._select_tab(i), id=cmd_id)

            entries.append((wx.ACCEL_CTRL, ord(digit), cmd_id))
            entries.append((wx.ACCEL_CMD, ord(digit), cmd_id))

            numpad = getattr(wx, f"WXK_NUMPAD{idx + 1}", None)
            if isinstance(numpad, int):
                entries.append((wx.ACCEL_CTRL, numpad, cmd_id))
                entries.append((wx.ACCEL_CMD, numpad, cmd_id))

        if entries:
            self.SetAcceleratorTable(wx.AcceleratorTable(entries))

    def _select_tab(self, index: int) -> None:
        if not hasattr(self, "_notebook"):
            return
        count = self._notebook.GetPageCount()
        if index < 0 or index >= count:
            return
        self._notebook.SetSelection(index)
        self._notebook.SetFocus()

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
    def _default_favorites_path() -> Path:
        path = Path(user_data_dir("Programista", "Programista"))
        path.mkdir(parents=True, exist_ok=True)
        return path / "favorites.json"

    @staticmethod
    def _default_settings_path() -> Path:
        path = Path(user_data_dir("Programista", "Programista"))
        path.mkdir(parents=True, exist_ok=True)
        return path / "settings.json"

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

        help_menu = wx.Menu()
        feedback_item = help_menu.Append(wx.ID_ANY, "Zgłoś błąd / sugestię…")
        self.Bind(wx.EVT_MENU, self._on_send_feedback, feedback_item)
        menubar.Append(help_menu, "Pomoc")

        self.SetMenuBar(menubar)

    def _on_send_feedback(self, _evt: wx.CommandEvent) -> None:
        dlg = FeedbackDialog(self, app_version=self._app_version())
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def _build_ui(self) -> None:
        panel = wx.Panel(self, style=wx.TAB_TRAVERSAL)
        sizer = wx.BoxSizer(wx.VERTICAL)

        notebook_style = wx.NB_TOP
        # Avoid tab scrolling buttons in the native control; some ATs miscount
        # tabs when the notebook overflows.
        if wx.Platform == "__WXMSW__":
            notebook_style |= wx.NB_MULTILINE
        self._notebook = wx.Notebook(panel, style=notebook_style)
        self._notebook.Bind(wx.EVT_NAVIGATION_KEY, self._on_notebook_navigation_key)

        self._tv_tab = TvTab(
            self._notebook,
            self._tv_provider,
            self._status_bar,
            favorites_store=self._favorites_store,
            on_favorites_changed=self._on_favorites_changed,
            search_index=self._search_index,
        )
        self._tv_accessibility_tab = TvAccessibilityTab(
            self._notebook,
            self._tv_accessibility_provider,
            self._status_bar,
            settings_store=self._settings_store,
            search_index=self._search_index,
        )
        self._radio_tab = RadioTab(
            self._notebook,
            self._radio_provider,
            self._status_bar,
            favorites_store=self._favorites_store,
            on_favorites_changed=self._on_favorites_changed,
            search_index=self._search_index,
        )
        self._favorites_tab = FavoritesTab(
            self._notebook,
            self._favorites_provider,
            self._status_bar,
            favorites_store=self._favorites_store,
            on_favorites_changed=self._on_favorites_changed,
            search_index=self._search_index,
        )
        self._search_tab = SearchTab(
            self._notebook,
            self._status_bar,
            settings_store=self._settings_store,
            search_index=self._search_index,
            hub=self._hub,
        )
        self._archive_tab = ArchiveTab(
            self._notebook,
            self._archive_provider,
            self._status_bar,
            search_index=self._search_index,
        )

        self._notebook.AddPage(self._tv_tab, "Telewizja")
        self._notebook.AddPage(self._tv_accessibility_tab, "Programy TV z udogodnieniami")
        self._notebook.AddPage(self._radio_tab, "Radio")
        self._notebook.AddPage(self._favorites_tab, "Ulubione")
        self._notebook.AddPage(self._search_tab, "Wyszukiwanie")
        self._notebook.AddPage(self._archive_tab, "Programy archiwalne")

        install_notebook_accessible(self._notebook)

        sizer.Add(self._notebook, 1, wx.EXPAND)
        panel.SetSizer(sizer)

    def _on_notebook_navigation_key(self, evt: wx.NavigationKeyEvent) -> None:
        if not evt.IsFromTab():
            evt.Skip()
            return

        page = self._notebook.GetCurrentPage()
        if not page:
            evt.Skip()
            return

        direction = wx.NavigationKeyEvent.IsForward if evt.GetDirection() else wx.NavigationKeyEvent.IsBackward
        if page.NavigateIn(flags=direction):
            return

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
        self._search_index.clear()
        self._status_bar.SetStatusText("Wyczyszczono cache.")
        tab = self._active_tab()
        if hasattr(tab, "refresh_all"):
            tab.refresh_all(force=True)

    def _on_favorites_changed(self) -> None:
        self._favorites_tab.refresh_all(force=False)
        self._tv_tab.sync_favorites()
        self._radio_tab.sync_favorites()

    def _refresh_all_tabs(self) -> None:
        self._tv_tab.refresh_all(force=False)
        self._tv_accessibility_tab.refresh_all(force=False)
        self._radio_tab.refresh_all(force=False)
        self._favorites_tab.refresh_all(force=False)
        self._archive_tab.refresh_all(force=False)

    def _auto_update_providers(self) -> None:
        self._status_bar.SetStatusText("Sprawdzanie aktualizacji dostawców…")
        self._run_in_thread(
            # On startup, always check upstream (not just the local cache),
            # so provider fixes can land without requiring a manual Ctrl+U.
            lambda: self._providers.update_and_reload(force_check=True),
            on_success=self._on_providers_updated,
            on_error=self._on_providers_update_error,
        )

    def _ensure_hub_api_key(self) -> None:
        if self._settings_store.get_hub_api_key():
            return

        def work():
            try:
                return self._hub.ensure_api_key()
            except Exception:  # noqa: BLE001
                return None

        self._run_in_thread(work, on_success=lambda _result: None, on_error=lambda _exc: None)

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
