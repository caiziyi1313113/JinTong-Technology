from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
import threading
from typing import Any, Callable

from app.core.config import settings


logger = logging.getLogger(__name__)


class TaskManager:
    """
    Shared background task executors.
    Keeps analysis and ranking workloads isolated so long-running ranking
    jobs do not block single-stock analysis requests.
    """

    def __init__(self) -> None:
        self._executors: dict[str, ThreadPoolExecutor] = {
            "analysis": ThreadPoolExecutor(
                max_workers=max(1, settings.analysis_worker_threads),
                thread_name_prefix="analysis-worker",
            ),
            "ranking": ThreadPoolExecutor(
                max_workers=max(1, settings.ranking_worker_threads),
                thread_name_prefix="ranking-worker",
            ),
        }
        self._futures: dict[str, tuple[str, Future[Any]]] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        *,
        pool: str,
        tracking_id: str,
        fn: Callable[..., Any],
        **kwargs: Any,
    ) -> Future[Any]:
        executor = self._executors.get(pool)
        if executor is None:
            raise ValueError(f"Unknown task pool: {pool}")

        with self._lock:
            if len(self._futures) >= settings.max_background_futures:
                self._trim_done_locked()
            if len(self._futures) >= settings.max_background_futures:
                raise RuntimeError("background queue is full, please retry later")

        future = executor.submit(fn, **kwargs)
        with self._lock:
            self._futures[tracking_id] = (pool, future)

        def _done_callback(done_future: Future[Any]) -> None:
            with self._lock:
                self._futures.pop(tracking_id, None)
            exc = done_future.exception()
            if exc:
                logger.exception("background task failed | pool=%s task_id=%s error=%s", pool, tracking_id, exc)
            else:
                logger.info("background task finished | pool=%s task_id=%s", pool, tracking_id)

        future.add_done_callback(_done_callback)
        logger.info("background task submitted | pool=%s task_id=%s", pool, tracking_id)
        return future

    def _trim_done_locked(self) -> None:
        done_ids = [task_id for task_id, (_, future) in self._futures.items() if future.done()]
        for task_id in done_ids:
            self._futures.pop(task_id, None)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._futures)
            by_pool = {
                pool: sum(1 for pool_name, _ in self._futures.values() if pool_name == pool)
                for pool in self._executors
            }
        return {"futures_total": total, "by_pool": by_pool}

    def shutdown(self) -> None:
        for pool_name, executor in self._executors.items():
            logger.info("task manager shutting down executor | pool=%s", pool_name)
            executor.shutdown(wait=False, cancel_futures=True)


task_manager = TaskManager()
