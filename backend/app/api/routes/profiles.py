from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.security import verify_password
from app.models.profile import UserProfile
from app.models.user import User
from app.schemas.profile import ProfileOut, ProfileUpdate
from app.services.profile_scoring import compute_questionnaire_profile

router = APIRouter(prefix="/profiles", tags=["profiles"])

QUESTIONNAIRE_TEMPLATE = {
    "required_order": [
        "disposable_funds",
        "loss_aversion",
        "risk_comfort",
        "time_horizon",
        "financial_literacy",
    ],
    "questions": {
        "disposable_funds": {
            "title": "当前可支配投资资金（人民币）",
            "type": "number",
            "min": 0,
        },
        "loss_aversion": {
            "title": "假设投资10万元，哪种亏损最接近你的心理极限？",
            "options": {
                "1": "亏损5000元就非常焦虑",
                "2": "亏损1.5万元会考虑清仓",
                "3": "亏损3万元内仍能保持冷静",
                "4": "只要逻辑不变，较大回撤也可继续持有",
            },
        },
        "risk_comfort": {
            "title": "重仓股一个月下跌20%时，你最可能做什么？",
            "options": {
                "1": "卖出全部仓位",
                "2": "卖出部分仓位",
                "3": "继续持有等待",
                "4": "认为跌出价值并加仓",
            },
        },
        "time_horizon": {
            "title": "这笔资金计划持有多久且期间不挪用？",
            "options": {
                "1": "1年以内",
                "2": "1-3年",
                "3": "3-5年",
                "4": "5年以上",
            },
        },
        "financial_literacy": {
            "title": "过去三年的主要投资经历",
            "options": {
                "1": "主要是存款或货币基金",
                "2": "买过股票/基金但交易较少",
                "3": "经常交易股票或公募基金",
                "4": "参与过期权/期货等杠杆产品",
            },
        },
    },
    "weights": {
        "loss_aversion": 0.35,
        "risk_comfort": 0.30,
        "time_horizon": 0.15,
        "financial_literacy": 0.20,
    },
    "formula": "RSI = ((D1*0.35 + D2*0.30 + D3*0.15 + D4*0.20) - 1) / 3",
    "risk_bands": {
        "0.00-0.25": "保守型",
        "0.25-0.50": "稳健型",
        "0.50-0.75": "进取型",
        "0.75-1.00": "激进型",
    },
}


def _get_or_create_profile(db: Session, user_id: int) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if profile:
        return profile
    profile = UserProfile(user_id=user_id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/me", response_model=ProfileOut)
def get_profile(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProfileOut:
    return _get_or_create_profile(db, current_user.id)


@router.get("/questionnaire/template")
def get_questionnaire_template(
    current_user: User = Depends(get_current_user),
) -> dict:
    _ = current_user
    return QUESTIONNAIRE_TEMPLATE


@router.put("/me", response_model=ProfileOut)
def update_profile(
    payload: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileOut:
    profile = _get_or_create_profile(db, current_user.id)

    updates = payload.model_dump(exclude_unset=True)
    current_password = updates.pop("current_password", None)

    sensitive_fields = {
        "assets",
        "disposable_funds",
        "income",
        "risk_level",
        "investment_horizon",
        "style",
        "persona",
        "questionnaire_answers",
        "risk_budget",
        "target_return",
        "max_single_position",
    }
    needs_password = any(field in updates for field in sensitive_fields)
    if needs_password and (not current_password or not verify_password(current_password, current_user.password_hash)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is required")

    if "questionnaire_answers" in updates and updates["questionnaire_answers"] is not None:
        questionnaire_answers = dict(updates["questionnaire_answers"])
        if questionnaire_answers.get("disposable_funds") is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="questionnaire_answers.disposable_funds is required",
            )
        scoring = compute_questionnaire_profile(questionnaire_answers)
        questionnaire_answers["scoring"] = scoring
        updates["questionnaire_answers"] = questionnaire_answers

        updates.setdefault("disposable_funds", float(scoring.get("disposable_funds", 0.0)))
        updates.setdefault("risk_level", scoring["risk_level"])
        updates.setdefault("style", scoring["style"])
        updates.setdefault("persona", scoring["persona"])
        updates.setdefault("risk_budget", scoring["risk_budget"])
        updates.setdefault("target_return", scoring["target_return"])
        updates.setdefault("max_single_position", scoring["max_single_position"])

    for field, value in updates.items():
        setattr(profile, field, value)

    if profile.disposable_funds > profile.assets > 0:
        profile.disposable_funds = profile.assets

    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile
