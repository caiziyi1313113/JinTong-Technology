from datetime import datetime, timedelta
import bcrypt
import hashlib
from typing import Optional

from jose import jwt

from app.core.config import settings

ALGORITHM = "HS256"


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode = {"sub": str(subject), "exp": expire}
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)


def _prehash_password(password: str) -> bytes:
    # Pre-hash avoids bcrypt 72-byte input limit.
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("utf-8")


def _safe_checkpw(raw_password: bytes, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(raw_password, hashed_password.encode("utf-8"))
    except Exception:
        return False


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if _safe_checkpw(_prehash_password(plain_password), hashed_password):
        return True
    # Backward compatibility for users created before pre-hash migration.
    return _safe_checkpw(plain_password.encode("utf-8"), hashed_password)


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(_prehash_password(password), bcrypt.gensalt()).decode("utf-8")
