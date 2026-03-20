from __future__ import annotations

from datetime import date
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import SessionLocal, get_db
from app.core.task_manager import task_manager
from app.models.data_sync_log import DataSyncLog
from app.models.user import User
from app.schemas.data_sync import DailySyncRequest, StaticSyncRequest, SyncLogOut
from app.services.data_ingest import AkshareServiceError, akshare_service

router = APIRouter(prefix="/data/sync", tags=["data-sync"])


def _daily_sync_worker(*, log_id: int, payload: dict) -> None:
    db = SessionLocal()
    log: DataSyncLog | None = None
    try:
        log = db.query(DataSyncLog).filter(DataSyncLog.id == log_id).first()
        if not log:
            return

        trade_date_text = payload.get("trade_date")
        target_date = date.fromisoformat(trade_date_text) if trade_date_text else date.today()

        detail = akshare_service.daily_sync(
            db,
            trade_date=target_date,
            symbols=payload.get("symbols"),
            history_days=int(payload.get("history_days") or 90),
            include_block_trade=bool(payload.get("include_block_trade", True)),
            include_news=bool(payload.get("include_news", True)),
            include_macro=bool(payload.get("include_macro", True)),
        )
        akshare_service.finish_sync_log(db, log=log, status="completed", detail=detail)
    except Exception as exc:
        db.rollback()
        if log is None:
            log = db.query(DataSyncLog).filter(DataSyncLog.id == log_id).first()
        if log:
            akshare_service.finish_sync_log(
                db,
                log=log,
                status="failed",
                detail={},
                error_message=str(exc),
            )
    finally:
        db.close()


@router.post("/daily", response_model=SyncLogOut)
def run_daily_sync(
    payload: DailySyncRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncLogOut:
    _ = current_user
    target_date = payload.trade_date or date.today()

    log = akshare_service.start_sync_log(db, job_type="daily_sync", scope=target_date.isoformat())
    try:
        detail = akshare_service.daily_sync(
            db,
            trade_date=target_date,
            symbols=payload.symbols,
            history_days=payload.history_days,
            include_block_trade=payload.include_block_trade,
            include_news=payload.include_news,
            include_macro=payload.include_macro,
        )
        return akshare_service.finish_sync_log(db, log=log, status="completed", detail=detail)
    except AkshareServiceError as exc:
        db.rollback()
        return akshare_service.finish_sync_log(
            db,
            log=log,
            status="failed",
            detail={},
            error_message=str(exc),
        )
    except Exception as exc:
        db.rollback()
        return akshare_service.finish_sync_log(
            db,
            log=log,
            status="failed",
            detail={},
            error_message=str(exc),
        )


@router.post("/daily/tasks", response_model=SyncLogOut)
def create_daily_sync_task(
    payload: DailySyncRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncLogOut:
    _ = current_user
    target_date = payload.trade_date or date.today()
    log = akshare_service.start_sync_log(db, job_type="daily_sync_task", scope=target_date.isoformat())

    task_payload = {
        "trade_date": target_date.isoformat(),
        "symbols": payload.symbols,
        "history_days": payload.history_days,
        "include_block_trade": payload.include_block_trade,
        "include_news": payload.include_news,
        "include_macro": payload.include_macro,
    }
    tracking_id = f"daily-sync-{log.id}-{uuid.uuid4().hex}"

    try:
        task_manager.submit(
            pool="ranking",
            tracking_id=tracking_id,
            fn=_daily_sync_worker,
            log_id=log.id,
            payload=task_payload,
        )
    except RuntimeError as exc:
        return akshare_service.finish_sync_log(
            db,
            log=log,
            status="failed",
            detail={},
            error_message=str(exc),
        )

    return log


@router.get("/daily/tasks/{task_id}", response_model=SyncLogOut)
def get_daily_sync_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncLogOut:
    _ = current_user
    row = (
        db.query(DataSyncLog)
        .filter(
            DataSyncLog.id == task_id,
            DataSyncLog.job_type.in_(["daily_sync_task", "daily_sync"]),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync task not found")
    return row


@router.post("/static", response_model=SyncLogOut)
def run_static_sync(
    payload: StaticSyncRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncLogOut:
    _ = current_user
    if not payload.symbols:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="symbols cannot be empty")

    log = akshare_service.start_sync_log(db, job_type="static_sync", scope=",".join(payload.symbols[:10]))
    try:
        detail = akshare_service.static_sync(db, symbols=payload.symbols)
        return akshare_service.finish_sync_log(db, log=log, status="completed", detail=detail)
    except AkshareServiceError as exc:
        db.rollback()
        return akshare_service.finish_sync_log(
            db,
            log=log,
            status="failed",
            detail={},
            error_message=str(exc),
        )
    except Exception as exc:
        db.rollback()
        return akshare_service.finish_sync_log(
            db,
            log=log,
            status="failed",
            detail={},
            error_message=str(exc),
        )


@router.get("/logs", response_model=list[SyncLogOut])
def list_sync_logs(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SyncLogOut]:
    _ = current_user
    rows = db.query(DataSyncLog).order_by(DataSyncLog.started_at.desc()).limit(max(1, min(limit, 200))).all()
    return rows
