from __future__ import annotations

from dataclasses import replace
from datetime import date

from tvguide_app.core.favorites import (
    FavoriteEntry,
    FavoriteRef,
    FavoritesStore,
    decode_favorite_source_id,
    encode_favorite_source_id,
)
from tvguide_app.core.models import ProviderId, ScheduleItem, Source, SourceId
from tvguide_app.core.providers.base import ScheduleProvider


class FavoritesProvider(ScheduleProvider):
    def __init__(
        self,
        store: FavoritesStore,
        *,
        tv: ScheduleProvider,
        radio: ScheduleProvider,
    ) -> None:
        self._store = store
        self._tv = tv
        self._radio = radio

    @property
    def provider_id(self) -> str:
        return "favorites"

    @property
    def display_name(self) -> str:
        return "Ulubione"

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        entries = self._store.list_entries()
        sources: list[Source] = []
        for entry in entries:
            sources.append(self._entry_to_source(entry))
        return sources

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        if not self._store.list_entries():
            return []
        today = date.today()
        tv_days = self._tv.list_days(force_refresh=force_refresh)
        radio_days = [d for d in self._radio.list_days(force_refresh=force_refresh) if d >= today]
        return sorted(set(tv_days).union(radio_days))

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        ref = decode_favorite_source_id(str(source.id))
        if not ref:
            return []
        delegate = self._delegate_for_ref(ref)
        if not delegate:
            return []

        original_source = self._ref_to_original_source(ref, preferred_name=self._name_from_source(source))
        items = delegate.get_schedule(original_source, day, force_refresh=force_refresh)
        return [self._wrap_item(it, source=source) for it in items]

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        ref = decode_favorite_source_id(str(item.source.id))
        if not ref:
            return ""
        delegate = self._delegate_for_ref(ref)
        if not delegate:
            return ""

        original_source = self._ref_to_original_source(ref, preferred_name=self._name_from_source(item.source))
        original_item = replace(item, provider_id=ProviderId(ref.provider_id), source=original_source)
        return delegate.get_item_details(original_item, force_refresh=force_refresh)

    @staticmethod
    def _name_from_source(source: Source) -> str:
        name = str(getattr(source, "name", "") or "")
        if name.startswith("TV: "):
            return name[4:]
        if name.startswith("Radio: "):
            return name[7:]
        return name

    def _ref_to_original_source(self, ref: FavoriteRef, *, preferred_name: str) -> Source:
        entry = self._store.get(ref)
        name = entry.name if entry else preferred_name
        return Source(provider_id=ProviderId(ref.provider_id), id=SourceId(ref.source_id), name=name)

    @staticmethod
    def _wrap_item(item: ScheduleItem, *, source: Source) -> ScheduleItem:
        return replace(item, provider_id=ProviderId("favorites"), source=source)

    def _entry_to_source(self, entry: FavoriteEntry) -> Source:
        label_prefix = "TV: " if entry.kind == "tv" else "Radio: "
        encoded_id = encode_favorite_source_id(entry)
        return Source(provider_id=ProviderId("favorites"), id=SourceId(encoded_id), name=f"{label_prefix}{entry.name}")

    def _delegate_for_ref(self, ref: FavoriteRef) -> ScheduleProvider | None:
        if ref.kind == "tv":
            return self._tv
        if ref.kind == "radio":
            return self._radio
        return None
