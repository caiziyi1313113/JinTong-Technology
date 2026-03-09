from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.schemas.workflow import (
    PostCloseReviewOut,
    PostCloseReviewRequest,
    PreOpenScanRequest,
    ScanResultOut,
)
from app.services.workflow.pipeline import generate_post_close_review, generate_pre_open_scan

router = APIRouter(prefix=  "/workflow", tags=["workflow"])

'''
自动交易流程模块。
收盘复盘
功能：
总结市场
选出候选股票
开盘前扫描
功能：
扫描市场
找机会
'''

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
