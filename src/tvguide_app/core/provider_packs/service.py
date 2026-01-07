from __future__ import annotations

from dataclasses import dataclass

from tvguide_app.core.http import HttpClient
from tvguide_app.core.provider_packs.loader import PackLoader, PackStore
from tvguide_app.core.provider_packs.schema import ProviderKind
from tvguide_app.core.provider_packs.updater import ProviderPackUpdater, UpdateResult
from tvguide_app.core.provider_packs.wrappers import (
    CompositeArchiveProvider,
    CompositeScheduleProvider,
    EmptyArchiveProvider,
    EmptyScheduleProvider,
    ReloadableArchiveProvider,
    ReloadableScheduleProvider,
)
from tvguide_app.core.providers.archive_base import ArchiveProvider
from tvguide_app.core.providers.base import ScheduleProvider


@dataclass(frozen=True)
class ProviderRuntime:
    tv: ReloadableScheduleProvider
    radio: ReloadableScheduleProvider
    archive: ReloadableArchiveProvider


class ProviderPackService:
    def __init__(
        self,
        http: HttpClient,
        *,
        base_url: str,
        store: PackStore,
        app_version: str,
        fallback_tv: ScheduleProvider,
        fallback_radio: ScheduleProvider,
        fallback_archive: ArchiveProvider,
    ) -> None:
        self._http = http
        self._store = store
        self._loader = PackLoader(store, app_version=app_version)
        self._updater = ProviderPackUpdater(http, store, base_url=base_url)

        self.runtime = ProviderRuntime(
            tv=ReloadableScheduleProvider(fallback_tv),
            radio=ReloadableScheduleProvider(fallback_radio),
            archive=ReloadableArchiveProvider(fallback_archive),
        )

    def load_installed(self) -> None:
        tv = self._load_schedule_kind("tv")
        radio = self._load_schedule_kind("radio")
        archive = self._load_archive_kind()

        if tv:
            self.runtime.tv.set_delegate(tv)
        if radio:
            self.runtime.radio.set_delegate(radio)
        if archive:
            self.runtime.archive.set_delegate(archive)

    def update_and_reload(self, *, force_check: bool = False) -> UpdateResult:
        result = self._updater.update_if_needed(force_check=force_check)
        if result.updated:
            self.load_installed()
        return result

    def _load_schedule_kind(self, kind: ProviderKind) -> ScheduleProvider | None:
        loaded = self._loader.load_kind(kind, self._http)
        if not loaded:
            return None
        providers = loaded.providers
        if not isinstance(providers, list) or any(not isinstance(p, ScheduleProvider) for p in providers):
            return None
        return CompositeScheduleProvider(providers)

    def _load_archive_kind(self) -> ArchiveProvider | None:
        loaded = self._loader.load_kind("archive", self._http)
        if not loaded:
            return None
        providers = loaded.providers
        if not isinstance(providers, list) or any(not isinstance(p, ArchiveProvider) for p in providers):
            return None
        return CompositeArchiveProvider(providers)
