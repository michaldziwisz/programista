from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import NewType

ProviderId = NewType("ProviderId", str)
SourceId = NewType("SourceId", str)


@dataclass(frozen=True)
class Source:
    provider_id: ProviderId
    id: SourceId
    name: str


@dataclass(frozen=True)
class ScheduleItem:
    provider_id: ProviderId
    source: Source
    day: date
    start_time: time | None
    end_time: time | None
    title: str
    subtitle: str | None
    details_ref: str | None
    details_summary: str | None

