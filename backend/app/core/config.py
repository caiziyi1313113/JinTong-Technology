from pydantic_settings import BaseSettings, SettingsConfigDict

#config.py 就是“全局配置中心”，统一从 .env / 环境变量加载
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Stock Intelligence MVP"
    api_v1_str: str = "/api/v1"
    database_url: str = "postgresql+psycopg2://postgres:msa@localhost:5432/msa"
    jwt_secret: str = "change-me"
    access_token_expire_minutes: int = 60 * 24
    allowed_origins: str = "http://localhost:5173"


settings = Settings()
