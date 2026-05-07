"""Generic batch-per-thread executor.

Splits work items into per-thread batches, fans out via ThreadPoolExecutor,
and processes each batch sequentially within a dedicated worker thread.

Subclasses define:
  - what work items to process (grouped by a key)
  - how to process a batch of items
  - thread labelling for logging
"""

import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generic, TypeVar

from app.base.config import settings

log = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


def split_into_batches(items: list, n: int) -> list[list]:
    """Round-robin distribute *items* into *n* roughly-equal batches."""
    batches: list[list] = [[] for _ in range(n)]
    for i, item in enumerate(items):
        batches[i % n].append(item)
    return [b for b in batches if b]


class BatchExecutor(ABC, Generic[T, R]):
    """Fan out work items across threads, one batch per worker.

    Items are grouped by a key (typically platform name), split into
    batches of roughly equal size, and each batch is processed
    sequentially within a dedicated thread.
    """

    @property
    @abstractmethod
    def stage(self) -> str:
        """Pipeline stage identifier (e.g. 's3_outreach')."""
        ...

    @property
    @abstractmethod
    def action(self) -> str:
        """Action identifier (e.g. 'send_inquiry')."""
        ...

    @abstractmethod
    def get_work_items(self) -> dict[str, list[T]]:
        """Return work items grouped by a key (e.g. platform name)."""
        ...

    @abstractmethod
    def _process_batch(
        self, batch: list[T], group_key: str
    ) -> list[tuple[T, R | None]]:
        """Process a batch of items sequentially. Called from a worker thread."""
        ...

    @abstractmethod
    def thread_label(self, item: T) -> str:
        """Return a label for the worker thread name."""
        ...

    def execute(self) -> list[tuple[T, R | None]]:
        """Run all work items concurrently. Returns (item, result) pairs."""
        grouped = self.get_work_items()
        if not grouped:
            return []

        all_results: list[tuple[T, R | None]] = []

        for group_key, items in grouped.items():
            log.info(
                "Processing %d items in group '%s' [%s/%s]",
                len(items),
                group_key,
                self.stage,
                self.action,
            )

            num_workers = min(settings.MAX_WORKERS, len(items))
            batches = split_into_batches(items, num_workers)

            with ThreadPoolExecutor(max_workers=num_workers) as pool:
                futures = {
                    pool.submit(self._process_batch, batch, group_key): i
                    for i, batch in enumerate(batches)
                }
                for future in as_completed(futures):
                    try:
                        all_results.extend(future.result())
                    except Exception:
                        batch_idx = futures[future]
                        log.exception(
                            "Unhandled batch error in group '%s' (batch %d)",
                            group_key,
                            batch_idx,
                        )

        return all_results
