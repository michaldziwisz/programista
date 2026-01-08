from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

from tvguide_app.core.models import ScheduleItem, Source
from tvguide_app.core.providers.archive_base import ArchiveProvider
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.search_index import SearchIndex, SearchKind


PrefetchStage = Literal["tv", "tv_accessibility", "radio", "archive"]


@dataclass(frozen=True)
class PrefetchUpdate:
    stage: PrefetchStage
    message: str
    done: int
    total: int | None
    errors: int
    finished: bool = False
    cancelled: bool = False


class PrefetchManager:
    def __init__(
        self,
        *,
        tv: ScheduleProvider,
        tv_accessibility: ScheduleProvider,
        radio: ScheduleProvider,
        archive: ArchiveProvider,
        search_index: SearchIndex,
        on_update: Callable[[PrefetchUpdate], None],
    ) -> None:
        self._tv = tv
        self._tv_accessibility = tv_accessibility
        self._radio = radio
        self._archive = archive
        self._search_index = search_index
        self._on_update = on_update

        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start_full_sync(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        errors = 0

        def update(stage: PrefetchStage, message: str, done: int, total: int | None) -> None:
            self._on_update(
                PrefetchUpdate(
                    stage=stage,
                    message=message,
                    done=done,
                    total=total,
                    errors=errors,
                    finished=False,
                    cancelled=False,
                )
            )

        def done_update(stage: PrefetchStage, message: str, done: int, total: int | None, *, cancelled: bool) -> None:
            self._on_update(
                PrefetchUpdate(
                    stage=stage,
                    message=message,
                    done=done,
                    total=total,
                    errors=errors,
                    finished=not cancelled,
                    cancelled=cancelled,
                )
            )

        try:
            if self._stop.is_set():
                return
            errors = self._prefetch_schedule_provider(
                stage="tv",
                provider=self._tv,
                kind="tv",
                errors=errors,
                update=update,
            )
            if self._stop.is_set():
                done_update("tv", "Przerwano.", 0, None, cancelled=True)
                return

            errors = self._prefetch_schedule_provider(
                stage="tv_accessibility",
                provider=self._tv_accessibility,
                kind="tv_accessibility",
                errors=errors,
                update=update,
            )
            if self._stop.is_set():
                done_update("tv_accessibility", "Przerwano.", 0, None, cancelled=True)
                return

            errors = self._prefetch_schedule_provider(
                stage="radio",
                provider=self._radio,
                kind="radio",
                errors=errors,
                update=update,
                day_filter=lambda d: d >= date.today(),
            )
            if self._stop.is_set():
                done_update("radio", "Przerwano.", 0, None, cancelled=True)
                return

            errors = self._prefetch_archive(errors=errors, update=update)
            if self._stop.is_set():
                done_update("archive", "Przerwano.", 0, None, cancelled=True)
                return
        finally:
            self._on_update(
                PrefetchUpdate(
                    stage="archive",
                    message="Gotowe." if not self._stop.is_set() else "Przerwano.",
                    done=0,
                    total=None,
                    errors=errors,
                    finished=not self._stop.is_set(),
                    cancelled=self._stop.is_set(),
                )
            )

    def _prefetch_schedule_provider(
        self,
        *,
        stage: PrefetchStage,
        provider: ScheduleProvider,
        kind: SearchKind,
        errors: int,
        update: Callable[[PrefetchStage, str, int, int | None], None],
        day_filter: Callable[[date], bool] | None = None,
    ) -> int:
        update(stage, "Ładowanie listy kanałów i dni…", 0, None)
        try:
            sources = provider.list_sources(force_refresh=False)
            days_all = provider.list_days(force_refresh=False)
        except Exception as e:  # noqa: BLE001
            update(stage, f"Błąd listowania: {e}", 0, None)
            return errors + 1

        days_by_provider_id: dict[str, list[date]] = {}
        list_days_for_provider = getattr(provider, "list_days_for_provider", None)
        provider_ids = sorted({str(s.provider_id) for s in sources})
        for pid in provider_ids:
            if callable(list_days_for_provider):
                try:
                    d = list_days_for_provider(pid, force_refresh=False)
                except Exception:  # noqa: BLE001
                    d = list(days_all)
            else:
                d = list(days_all)
            if day_filter:
                d = [x for x in d if day_filter(x)]
            days_by_provider_id[pid] = list(d)

        total = sum(len(days_by_provider_id.get(str(src.provider_id), [])) for src in sources)
        done = 0
        update(stage, "Pobieranie ramówek…", done, total)

        for src in sources:
            if self._stop.is_set():
                break
            pid = str(src.provider_id)
            days = days_by_provider_id.get(pid, [])
            for d in days:
                if self._stop.is_set():
                    break
                done += 1
                update(stage, f"{src.name} {d.isoformat()}", done, total)
                try:
                    items = provider.get_schedule(src, d, force_refresh=False)
                except Exception:  # noqa: BLE001
                    errors += 1
                    continue

                try:
                    self._search_index.add_items(kind, list(items))
                except Exception:  # noqa: BLE001
                    errors += 1

                time.sleep(0)

        return errors

    def _prefetch_archive(
        self,
        *,
        errors: int,
        update: Callable[[PrefetchStage, str, int, int | None], None],
    ) -> int:
        stage: PrefetchStage = "archive"
        done = 0
        update(stage, "Ładowanie listy lat…", done, None)
        try:
            years = self._archive.list_years()
        except Exception as e:  # noqa: BLE001
            update(stage, f"Błąd listowania lat: {e}", done, None)
            return errors + 1

        for year in years:
            if self._stop.is_set():
                break
            for month in range(1, 13):
                if self._stop.is_set():
                    break
                update(stage, f"{year}-{month:02d}: szukanie dni…", done, None)
                try:
                    days = self._archive.list_days_in_month(year, month, force_refresh=False)
                except Exception:  # noqa: BLE001
                    errors += 1
                    continue

                for d in days:
                    if self._stop.is_set():
                        break
                    try:
                        sources = self._archive.list_sources_for_day(d, force_refresh=False)
                    except Exception:  # noqa: BLE001
                        errors += 1
                        continue

                    for idx, src in enumerate(sources, start=1):
                        if self._stop.is_set():
                            break
                        update(stage, f"{d.isoformat()} ({idx}/{len(sources)}): {src.name}", done, None)
                        try:
                            items = self._archive.get_schedule(src, d, force_refresh=False)
                        except Exception:  # noqa: BLE001
                            errors += 1
                            continue

                        try:
                            self._search_index.add_items("archive", list(items))
                        except Exception:  # noqa: BLE001
                            errors += 1

                    done += 1
                    time.sleep(0)

        return errors

