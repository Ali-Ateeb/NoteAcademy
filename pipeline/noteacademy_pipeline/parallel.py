"""Running many independent model calls at once.

Every automated verifier has the same shape: read some hundreds of items with
no database connection open, send each to a model, then write all the results
in one short pass. The middle step is a pile of independent, network-bound
calls, and doing them one after another is the difference between minutes and
hours. DeepSeek's documented concurrency ceiling is 2,500 connections for
`deepseek-flash`, so a handful of workers is far below anything that would
push back.

Kept generic (no model, no database) so the ordering and failure rules can be
tested on their own.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def map_concurrently(
    items: Sequence[T],
    fn: Callable[[T], R],
    *,
    workers: int = 1,
    stop_on: tuple[type[BaseException], ...] = (),
    on_done: Callable[[int, int], None] | None = None,
) -> list[tuple[T, R | None, Exception | None]]:
    """Apply `fn` to every item, returning `(item, result, error)` in the order
    the items were given, whatever order they finished in.

    An ordinary exception from one item is captured as that item's `error` and
    never stops the others — one bad reply must not lose the rest. An
    exception whose type is in `stop_on` is different: it means every remaining
    item would fail the same way (a rejected API key, an empty balance), so it
    cancels whatever has not started and propagates immediately.

    `workers <= 1` runs in the calling thread with the same rules, so a single
    worker is a true sequential run and not a different code path to trust.
    """
    total = len(items)
    results: list[tuple[T, R | None, Exception | None]] = [(item, None, None) for item in items]

    if workers <= 1 or total <= 1:
        for index, item in enumerate(items):
            try:
                results[index] = (item, fn(item), None)
            except stop_on:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad item must not lose the rest
                results[index] = (item, None, exc)
            if on_done:
                on_done(index + 1, total)
        return results

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): index for index, item in enumerate(items)}
        done = 0
        try:
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = (items[index], future.result(), None)
                except stop_on:
                    raise
                except Exception as exc:  # noqa: BLE001
                    results[index] = (items[index], None, exc)
                done += 1
                if on_done:
                    on_done(done, total)
        except BaseException:
            for future in futures:
                future.cancel()
            raise

    return results
