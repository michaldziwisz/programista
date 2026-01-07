from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import NewType, Literal

ProviderId = NewType("ProviderId", str)
SourceId = NewType("SourceId", str)

AccessibilityFeature = Literal["AD", "JM", "N"]

ACCESSIBILITY_FEATURE_LABELS: dict[AccessibilityFeature, str] = {
    "AD": "audiodeskrypcja",
    "JM": "język migowy",
    "N": "napisy",
}


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
    accessibility: tuple[AccessibilityFeature, ...] = ()
