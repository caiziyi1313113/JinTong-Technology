from datetime import date, datetime, timezone
import logging
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import SessionLocal, get_db
from app.core.task_manager import task_manager
from app.models.user import User
from app.schemas.ranking import RankingRunRequest, RankingSnapshotOut, RankingTaskOut
from app.schemas.workflow import (
    PostCloseReviewOut,
    PostCloseReviewRequest,
    PreOpenScanRequest,
    ScanResultOut,
)
from app.services.workflow.pipeline import generate_post_close_review, generate_pre_open_scan
from app.services.workflow.review_pipeline import (
    RankingPipelineError,
    get_latest_snapshot,
    get_snapshot,
    run_ranking_snapshot,
)

router = APIRouter(prefix="/workflow", tags=["workflow"])
logger = logging.getLogger(__name__)

RANKING_MAX_TASKS = 200
_ranking_tasks_lock = threading.Lock()
_ranking_tasks: dict[str, dict] = {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ranking_task_cleanup() -> None:
    with _ranking_tasks_lock:
        if len(_ranking_tasks) <= RANKING_MAX_TASKS:
            return
        ordered = sorted(_ranking_tasks.items(), key=lambda item: item[1]["updated_at"])
        for task_id, _ in ordered[: len(_ranking_tasks) - RANKING_MAX_TASKS]:
            _ranking_tasks.pop(task_id, None)


def _ranking_task_create(*, snapshot_type: str, snapshot_date: date, top_n: int) -> dict:
    now = _now_utc()
    task_id = uuid.uuid4().hex
    task = {
        "task_id": task_id,
        "snapshot_type": snapshot_type,
        "snapshot_date": snapshot_date,
        "top_n": top_n,
        "status": "queued",
        "stage": "queued",
        "message": "Task created and waiting in queue",
        "error": None,
        "snapshot_id": None,
        "created_at": now,
        "updated_at": now,
    }
    with _ranking_tasks_lock:
        _ranking_tasks[task_id] = task
    _ranking_task_cleanup()
    return task


def _ranking_task_update(task_id: str, **kwargs) -> None:
    with _ranking_tasks_lock:
        task = _ranking_tasks.get(task_id)
        if not task:
            return
        task.update(kwargs)
        task["updated_at"] = _now_utc()
        logger.info(
            "ranking task update | task_id=%s status=%s stage=%s message=%s",
            task_id,
            task.get("status"),
            task.get("stage"),
            task.get("message"),
        )


def _ranking_task_get(task_id: str) -> dict | None:
    with _ranking_tasks_lock:
        task = _ranking_tasks.get(task_id)
        if not task:
            return None
        return dict(task)


def _ranking_task_to_out(task: dict) -> RankingTaskOut:
    return RankingTaskOut(
        task_id=task["task_id"],
        snapshot_type=task["snapshot_type"],
        snapshot_date=task["snapshot_date"],
        top_n=task["top_n"],
        status=task["status"],
        stage=task["stage"],
        message=task.get("message"),
        error=task.get("error"),
        snapshot_id=task.get("snapshot_id"),
        created_at=task["created_at"],
        updated_at=task["updated_at"],
    )


def _ranking_task_worker(
    task_id: str,
    *,
    snapshot_date: date,
    snapshot_type: str,
    top_n: int,
    symbols: list[str] | None,
) -> None:
    db = SessionLocal()
    try:
        logger.info(
            "ranking task start | task_id=%s date=%s type=%s top_n=%s",
            task_id,
            snapshot_date,
            snapshot_type,
            top_n,
        )
        _ranking_task_update(
            task_id,
            status="running",
            stage="running_pipeline",
            message="Analyzing to generate a new ranking...",
        )
        snapshot = run_ranking_snapshot(
            db,
            snapshot_date=snapshot_date,
            snapshot_type=snapshot_type,
            top_n=top_n,
            symbols=symbols,
        )
        _ranking_task_update(
            task_id,
            status="completed",
            stage="completed",
            message="Ranking snapshot is updated",
            snapshot_id=snapshot.id,
            error=None,
        )
        logger.info("ranking task completed | task_id=%s snapshot_id=%s", task_id, snapshot.id)
    except RankingPipelineError as exc:
        _ranking_task_update(
            task_id,
            status="failed",
            stage="failed",
            message="Ranking generation failed",
            error=str(exc),
        )
        logger.exception("ranking task failed | task_id=%s error=%s", task_id, exc)
    except Exception as exc:
        _ranking_task_update(
            task_id,
            status="failed",
            stage="failed",
            message="Ranking generation failed",
            error=str(exc),
        )
        logger.exception("ranking task failed unexpectedly | task_id=%s error=%s", task_id, exc)
    finally:
        db.close()


@router.post("/post-close-review", response_model=PostCloseReviewOut)
def post_close_review(
    payload: PostCloseReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostCloseReviewOut:
    _ = current_user
    target_date = payload.trade_date or date.today()
    recap, candidates = generate_post_close_review(db, target_date, top_n=payload.top_n)
    return PostCloseReviewOut(recap=recap, candidates=candidates)


@router.post("/pre-open-scan", response_model=list[ScanResultOut])
def pre_open_scan(
    payload: PreOpenScanRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ScanResultOut]:
    _ = current_user
    target_date = payload.scan_date or date.today()
    return generate_pre_open_scan(db, target_date, top_n=payload.top_n)


@router.post("/ranking/run", response_model=RankingSnapshotOut)
def run_ranking(
    payload: RankingRunRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RankingSnapshotOut:
    _ = current_user
    target_date = payload.snapshot_date or date.today()
    if payload.snapshot_type not in {"post_close", "pre_open", "realtime"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="snapshot_type must be post_close/pre_open/realtime",
        )

    try:
        snapshot = run_ranking_snapshot(
            db,
            snapshot_date=target_date,
            snapshot_type=payload.snapshot_type,
            top_n=payload.top_n,
            symbols=payload.symbols,
        )
    except RankingPipelineError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    snapshot.items.sort(key=lambda item: item.rank)
    return snapshot


@router.post("/ranking/tasks", response_model=RankingTaskOut)
def create_ranking_task(
    payload: RankingRunRequest,
    current_user: User = Depends(get_current_user),
) -> RankingTaskOut:
    _ = current_user
    target_date = payload.snapshot_date or date.today()
    if payload.snapshot_type not in {"post_close", "pre_open", "realtime"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="snapshot_type must be post_close/pre_open/realtime",
        )

    task = _ranking_task_create(snapshot_type=payload.snapshot_type, snapshot_date=target_date, top_n=payload.top_n)
    try:
        task_manager.submit(
            pool="ranking",
            tracking_id=task["task_id"],
            fn=_ranking_task_worker,
            task_id=task["task_id"],
            snapshot_date=target_date,
            snapshot_type=payload.snapshot_type,
            top_n=payload.top_n,
            symbols=payload.symbols,
        )
    except RuntimeError as exc:
        _ranking_task_update(
            task["task_id"],
            status="failed",
            stage="queue_rejected",
            message="Background queue is full, please retry",
            error=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    return _ranking_task_to_out(task)


@router.get("/ranking/tasks/{task_id}", response_model=RankingTaskOut)
def get_ranking_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
) -> RankingTaskOut:
    _ = current_user
    task = _ranking_task_get(task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ranking task not found")
    return _ranking_task_to_out(task)


@router.get("/ranking/latest", response_model=RankingSnapshotOut)
def read_latest_ranking(
    snapshot_type: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RankingSnapshotOut:
    _ = current_user
    if snapshot_type and snapshot_type not in {"post_close", "pre_open", "realtime"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid snapshot_type")

    snapshot = get_latest_snapshot(db, snapshot_type=snapshot_type)
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No ranking snapshot found")
    snapshot.items.sort(key=lambda item: item.rank)
    return snapshot


@router.get("/ranking/{snapshot_id}", response_model=RankingSnapshotOut)
def read_ranking(
    snapshot_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RankingSnapshotOut:
    _ = current_user
    snapshot = get_snapshot(db, snapshot_id=snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ranking snapshot not found")
    snapshot.items.sort(key=lambda item: item.rank)
    return snapshot
