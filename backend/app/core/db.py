from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass

# print("DB URL:", settings.database_url.replace(settings.database_url.split(":")[2].split("@")[0], "***"))
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# db.py 提供 get_db，让每个接口函数都能安全拿到数据库连接并自动释放。
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
