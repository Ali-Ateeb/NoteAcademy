"""map_concurrently: ordering, failure isolation, and the stop-everything rule."""

import threading
import time

import pytest

from noteacademy_pipeline.parallel import map_concurrently


class Fatal(Exception):
    pass


@pytest.mark.parametrize("workers", [1, 4])
def test_results_come_back_in_input_order_whatever_order_they_finish(workers):
    # Later items finish first: the first is the slowest.
    delays = {0: 0.15, 1: 0.05, 2: 0.0, 3: 0.0}

    def fn(i):
        time.sleep(delays[i])
        return i * 10

    out = map_concurrently([0, 1, 2, 3], fn, workers=workers)
    assert [(item, result, error) for item, result, error in out] == [
        (0, 0, None), (1, 10, None), (2, 20, None), (3, 30, None),
    ]


@pytest.mark.parametrize("workers", [1, 4])
def test_one_failure_is_captured_and_the_rest_still_run(workers):
    def fn(i):
        if i == 1:
            raise ValueError("bad reply")
        return i

    out = map_concurrently([0, 1, 2], fn, workers=workers)
    assert out[0] == (0, 0, None)
    assert out[2] == (2, 2, None)
    item, result, error = out[1]
    assert (item, result) == (1, None)
    assert isinstance(error, ValueError) and str(error) == "bad reply"


@pytest.mark.parametrize("workers", [1, 4])
def test_a_stop_on_exception_propagates_instead_of_being_captured(workers):
    def fn(i):
        if i == 2:
            raise Fatal("no balance")
        return i

    with pytest.raises(Fatal):
        map_concurrently(list(range(6)), fn, workers=workers, stop_on=(Fatal,))


def test_a_stop_on_exception_cancels_work_that_has_not_started():
    started: list[int] = []
    lock = threading.Lock()

    def fn(i):
        with lock:
            started.append(i)
        if i == 0:
            raise Fatal("stop")
        time.sleep(0.05)
        return i

    with pytest.raises(Fatal):
        map_concurrently(list(range(200)), fn, workers=2, stop_on=(Fatal,))

    # Two workers cannot have gone through two hundred items after item 0 failed.
    assert len(started) < 200


def test_without_stop_on_even_a_fatal_looking_error_is_just_captured():
    def fn(i):
        raise Fatal("x")

    out = map_concurrently([1, 2], fn, workers=1)
    assert all(isinstance(error, Fatal) for _, _, error in out)


@pytest.mark.parametrize("workers", [1, 3])
def test_progress_is_reported_once_per_item_ending_at_the_total(workers):
    seen: list[tuple[int, int]] = []
    map_concurrently(list(range(5)), lambda i: i, workers=workers,
                     on_done=lambda done, total: seen.append((done, total)))
    assert len(seen) == 5
    assert seen[-1] == (5, 5)
    assert [d for d, _ in seen] == [1, 2, 3, 4, 5]


def test_one_worker_runs_in_the_calling_thread():
    threads: set[int] = set()
    map_concurrently([1, 2, 3], lambda i: threads.add(threading.get_ident()), workers=1)
    assert threads == {threading.get_ident()}


def test_several_workers_really_overlap():
    running = 0
    peak = 0
    lock = threading.Lock()

    def fn(i):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1

    map_concurrently(list(range(8)), fn, workers=4)
    assert peak > 1


def test_empty_input():
    assert map_concurrently([], lambda i: i, workers=4) == []
