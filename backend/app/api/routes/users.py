from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.security import create_access_token, get_password_hash
from app.models.user import User
from app.schemas.auth import Token
from app.schemas.user import UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
def read_me(current_user: User = Depends(get_current_user)) -> UserOut:
    return current_user


@router.post("/guest")
def create_guest(db: Session = Depends(get_db)) -> dict:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    email = f"guest-{stamp}@local"
    user = User(email=email, password_hash=get_password_hash(stamp))
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(str(user.id))
    return {"user": UserOut.model_validate(user), "token": Token(access_token=token)}
