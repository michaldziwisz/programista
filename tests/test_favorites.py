from datetime import date, timedelta
from pathlib import Path

from tvguide_app.core.favorites import FavoriteRef, FavoritesStore
from tvguide_app.core.models import ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.providers.favorites import FavoritesProvider


def test_favorites_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "favorites.json"
    store = FavoritesStore(path)

    assert store.list_entries() == []

    src = Source(provider_id=ProviderId("teleman"), id=SourceId("13ulica"), name="13 Ulica")
    assert store.add_source("tv", src) is True
    assert store.add_source("tv", src) is False

    ref = FavoriteRef(kind="tv", provider_id="teleman", source_id="13ulica")
    assert store.is_favorite(ref) is True

    store2 = FavoritesStore(path)
    assert store2.is_favorite(ref) is True

    assert store2.remove(ref) is True
    assert store2.remove(ref) is False


class _DummyScheduleProvider(ScheduleProvider):
    def __init__(self, provider_id: str, *, days: list[date]) -> None:
        self._provider_id = provider_id
        self._days = list(days)
        self.calls: list[tuple[str, object]] = []

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def display_name(self) -> str:
        return self._provider_id

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        return []

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        return list(self._days)

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        self.calls.append(("get_schedule", (source, day)))
        return [
            ScheduleItem(
                provider_id=ProviderId(self._provider_id),
                source=source,
                day=day,
                start_time=None,
                end_time=None,
                title="Test",
                subtitle=None,
                details_ref="x",
                details_summary="summary",
            )
        ]

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        self.calls.append(("get_item_details", item))
        return f"details:{self._provider_id}"


def test_favorites_provider_routes_and_wraps(tmp_path: Path) -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    store = FavoritesStore(tmp_path / "favorites.json")
    store.add_source("tv", Source(provider_id=ProviderId("tv-p"), id=SourceId("tv-id"), name="TV Name"))
    store.add_source(
        "radio",
        Source(provider_id=ProviderId("radio-p"), id=SourceId("radio-id"), name="Radio Name"),
    )

    tv = _DummyScheduleProvider("tv-p", days=[today, tomorrow])
    radio = _DummyScheduleProvider("radio-p", days=[yesterday, today])

    fav = FavoritesProvider(store, tv=tv, radio=radio)

    sources = fav.list_sources()
    assert [s.name for s in sources] == ["Radio: Radio Name", "TV: TV Name"]

    assert fav.list_days() == [today, tomorrow]

    tv_source = next(s for s in sources if s.name.startswith("TV:"))
    items = fav.get_schedule(tv_source, today)
    assert len(items) == 1
    assert str(items[0].provider_id) == "favorites"
    assert str(items[0].source.provider_id) == "favorites"

    assert tv.calls
    call_name, (called_source, called_day) = tv.calls[0]
    assert call_name == "get_schedule"
    assert str(called_source.provider_id) == "tv-p"
    assert str(called_source.id) == "tv-id"
    assert called_source.name == "TV Name"
    assert called_day == today

    assert fav.get_item_details(items[0]) == "details:tv-p"
    assert tv.calls[-1][0] == "get_item_details"
