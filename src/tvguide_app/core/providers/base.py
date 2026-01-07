from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from tvguide_app.core.models import ScheduleItem, Source


class ScheduleProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    def list_sources(self, *, force_refresh: bool = False) -> list[Source]: ...

    @abstractmethod
    def list_days(self, *, force_refresh: bool = False) -> list[date]: ...

    @abstractmethod
    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]: ...

    @abstractmethod
    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str: ...

