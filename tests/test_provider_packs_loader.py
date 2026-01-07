import sys
from datetime import date
from pathlib import Path

from tvguide_app.core.cache import SqliteCache
from tvguide_app.core.http import HttpClient
from tvguide_app.core.models import ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.provider_packs.loader import PackLoader, PackStore
from tvguide_app.core.providers.archive_base import ArchiveProvider
from tvguide_app.core.providers.base import ScheduleProvider


def _http(tmp_path: Path) -> HttpClient:
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    return HttpClient(cache, user_agent="tvguide-app-tests/0.0")


def test_pack_loader_loads_tv_pack(tmp_path: Path) -> None:
    store = PackStore(tmp_path / "providers")
    pack_dir = store.pack_dir("tv", "1.0.0")
    pack_dir.mkdir(parents=True, exist_ok=True)

    (pack_dir / "pack.json").write_text(
        """
        {
          "schema": 1,
          "kind": "tv",
          "version": "1.0.0",
          "package": "programista_providers_tv_test",
          "entrypoint": "programista_providers_tv_test:load",
          "provider_api_version": 1
        }
        """.strip(),
        encoding="utf-8",
    )

    pkg = pack_dir / "programista_providers_tv_test"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        """
from datetime import date

from tvguide_app.core.models import ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.base import ScheduleProvider


class DummyTv(ScheduleProvider):
    @property
    def provider_id(self) -> str:
        return "dummy-tv"

    @property
    def display_name(self) -> str:
        return "Dummy TV"

    def list_sources(self, *, force_refresh: bool = False):
        return [Source(provider_id=ProviderId(self.provider_id), id=SourceId("x"), name="X")]

    def list_days(self, *, force_refresh: bool = False):
        return [date(2026, 1, 1)]

    def get_schedule(self, source, day, *, force_refresh: bool = False):
        return [
            ScheduleItem(
                provider_id=ProviderId(self.provider_id),
                source=source,
                day=day,
                start_time=None,
                end_time=None,
                title="Test",
                subtitle=None,
                details_ref=None,
                details_summary=None,
            )
        ]

    def get_item_details(self, item, *, force_refresh: bool = False) -> str:
        return "OK"


def load(http):
    return [DummyTv()]
        """.lstrip(),
        encoding="utf-8",
    )

    store.set_active_version("tv", "1.0.0")

    loader = PackLoader(store, app_version="0.1.0")
    orig_sys_path = list(sys.path)
    try:
        loaded = loader.load_kind("tv", _http(tmp_path))
        assert loaded is not None
        assert loaded.manifest.kind == "tv"
        assert len(loaded.providers) == 1
        provider = loaded.providers[0]
        assert isinstance(provider, ScheduleProvider)
        sources = provider.list_sources()
        assert sources[0].name == "X"
    finally:
        sys.path[:] = orig_sys_path
        for name in list(sys.modules):
            if name.startswith("programista_providers_tv_test"):
                sys.modules.pop(name, None)


def test_pack_loader_loads_tv_accessibility_pack(tmp_path: Path) -> None:
    store = PackStore(tmp_path / "providers")
    pack_dir = store.pack_dir("tv_accessibility", "1.0.0")
    pack_dir.mkdir(parents=True, exist_ok=True)

    (pack_dir / "pack.json").write_text(
        """
        {
          "schema": 1,
          "kind": "tv_accessibility",
          "version": "1.0.0",
          "package": "programista_providers_tv_accessibility_test",
          "entrypoint": "programista_providers_tv_accessibility_test:load",
          "provider_api_version": 1
        }
        """.strip(),
        encoding="utf-8",
    )

    pkg = pack_dir / "programista_providers_tv_accessibility_test"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        """
from datetime import date

from tvguide_app.core.models import ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.base import ScheduleProvider


class DummyA11yTv(ScheduleProvider):
    @property
    def provider_id(self) -> str:
        return "dummy-tv-a11y"

    @property
    def display_name(self) -> str:
        return "Dummy TV A11y"

    def list_sources(self, *, force_refresh: bool = False):
        return [Source(provider_id=ProviderId(self.provider_id), id=SourceId("x"), name="X")]

    def list_days(self, *, force_refresh: bool = False):
        return [date(2026, 1, 1)]

    def get_schedule(self, source, day, *, force_refresh: bool = False):
        return [
            ScheduleItem(
                provider_id=ProviderId(self.provider_id),
                source=source,
                day=day,
                start_time=None,
                end_time=None,
                title="Test",
                subtitle=None,
                details_ref=None,
                details_summary=None,
            )
        ]

    def get_item_details(self, item, *, force_refresh: bool = False) -> str:
        return "OK"


def load(http):
    return [DummyA11yTv()]
        """.lstrip(),
        encoding="utf-8",
    )

    store.set_active_version("tv_accessibility", "1.0.0")

    loader = PackLoader(store, app_version="0.1.0")
    orig_sys_path = list(sys.path)
    try:
        loaded = loader.load_kind("tv_accessibility", _http(tmp_path))
        assert loaded is not None
        assert loaded.manifest.kind == "tv_accessibility"
        assert len(loaded.providers) == 1
        provider = loaded.providers[0]
        assert isinstance(provider, ScheduleProvider)
        sources = provider.list_sources()
        assert sources[0].name == "X"
    finally:
        sys.path[:] = orig_sys_path
        for name in list(sys.modules):
            if name.startswith("programista_providers_tv_accessibility_test"):
                sys.modules.pop(name, None)


def test_pack_loader_loads_archive_pack(tmp_path: Path) -> None:
    store = PackStore(tmp_path / "providers")
    pack_dir = store.pack_dir("archive", "1.0.0")
    pack_dir.mkdir(parents=True, exist_ok=True)

    (pack_dir / "pack.json").write_text(
        """
        {
          "schema": 1,
          "kind": "archive",
          "version": "1.0.0",
          "package": "programista_providers_archive_test",
          "entrypoint": "programista_providers_archive_test:load",
          "provider_api_version": 1
        }
        """.strip(),
        encoding="utf-8",
    )

    pkg = pack_dir / "programista_providers_archive_test"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        """
from datetime import date

from tvguide_app.core.models import ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.archive_base import ArchiveProvider


class DummyArchive(ArchiveProvider):
    @property
    def provider_id(self) -> str:
        return "dummy-archive"

    @property
    def display_name(self) -> str:
        return "Dummy Archive"

    def list_years(self) -> list[int]:
        return [2026]

    def list_days_in_month(self, year: int, month: int, *, force_refresh: bool = False):
        return [date(2026, 1, 1)]

    def list_sources_for_day(self, day: date, *, force_refresh: bool = False):
        return [Source(provider_id=ProviderId(self.provider_id), id=SourceId("x"), name="X")]

    def get_schedule(self, source: Source, day: date, *, force_refresh: bool = False):
        return [
            ScheduleItem(
                provider_id=ProviderId(self.provider_id),
                source=source,
                day=day,
                start_time=None,
                end_time=None,
                title="A",
                subtitle=None,
                details_ref=None,
                details_summary=None,
            )
        ]


def load(http):
    return [DummyArchive()]
        """.lstrip(),
        encoding="utf-8",
    )

    store.set_active_version("archive", "1.0.0")

    loader = PackLoader(store, app_version="0.1.0")
    orig_sys_path = list(sys.path)
    try:
        loaded = loader.load_kind("archive", _http(tmp_path))
        assert loaded is not None
        assert loaded.manifest.kind == "archive"
        assert len(loaded.providers) == 1
        provider = loaded.providers[0]
        assert isinstance(provider, ArchiveProvider)
        assert provider.list_years() == [2026]
    finally:
        sys.path[:] = orig_sys_path
        for name in list(sys.modules):
            if name.startswith("programista_providers_archive_test"):
                sys.modules.pop(name, None)
