from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import create_access_token, get_password_hash, verify_password
from app.models.profile import UserProfile
from app.models.user import User
from app.schemas.auth import LoginRequest, Token
from app.schemas.user import UserCreate, UserOut
from app.services.profile_scoring import compute_questionnaire_profile

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> UserOut:
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(email=payload.email, password_hash=get_password_hash(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    profile_payload = payload.profile
    profile = UserProfile(user_id=user.id)
    if profile_payload:
        if profile_payload.assets is not None:
            profile.assets = max(0.0, float(profile_payload.assets))
        if profile_payload.disposable_funds is not None:
            profile.disposable_funds = max(0.0, float(profile_payload.disposable_funds))
        if profile_payload.income is not None:
            profile.income = max(0.0, float(profile_payload.income))
        if profile_payload.investment_horizon:
            profile.investment_horizon = profile_payload.investment_horizon
        if profile_payload.risk_level:
            profile.risk_level = profile_payload.risk_level
        if profile_payload.style:
            profile.style = profile_payload.style
        if profile_payload.persona:
            profile.persona = profile_payload.persona
        if profile_payload.questionnaire_answers:
            questionnaire_answers = dict(profile_payload.questionnaire_answers)
            disposable_funds_raw = questionnaire_answers.get("disposable_funds")
            if disposable_funds_raw is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="questionnaire_answers.disposable_funds is required",
                )
            scored = compute_questionnaire_profile(profile_payload.questionnaire_answers)
            profile.questionnaire_answers = {
                **questionnaire_answers,
                "scoring": scored,
            }
            profile.disposable_funds = max(0.0, float(scored.get("disposable_funds", profile.disposable_funds)))
            profile.risk_level = scored["risk_level"]
            profile.style = scored["style"]
            profile.persona = scored["persona"]
            profile.target_return = scored["target_return"]
            profile.risk_budget = scored["risk_budget"]
            profile.max_single_position = scored["max_single_position"]
    if profile.disposable_funds > profile.assets > 0:
        profile.disposable_funds = profile.assets

    db.add(profile)
    db.commit()
    return user


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> Token:
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(str(user.id))
    return Token(access_token=token)
