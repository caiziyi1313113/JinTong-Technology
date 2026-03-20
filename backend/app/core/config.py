from pydantic_settings import BaseSettings, SettingsConfigDict

# Optional local model paths.
# If set, these values override remote HuggingFace repo names.
# Example (WSL): "/mnt/d/master_dynamic/backend/models/sentiment/guba_model"
LOCAL_SENTIMENT_GUBA_MODEL = ""
LOCAL_SENTIMENT_GUBA_TOKENIZER = ""
LOCAL_SENTIMENT_NEWS_MODEL = ""
LOCAL_HF_CACHE_DIR = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    # database
    app_name: str = "Stock Intelligence MVP"
    api_v1_str: str = "/api/v1"
    database_url: str = "postgresql+psycopg2://postgres:msa@localhost:5432/msa"
    jwt_secret: str = "change-me"
    access_token_expire_minutes: int = 60 * 24
    allowed_origins: str = "http://localhost:5173"
    # zhipu api key
    zhipu_api_key: str = "fa23effc7d4149afaa56b2db89327c1e.OgquEufbWCj4Ggze"
    zhipu_api_key_news: str = "a56116b4f49d42829588371a36e7dea7.96K7CzgFQwh3jJb5"
    zhipu_api_key_stock_data: str = "5159dd6c5415417683ad9931c4a0b23d.m9EERIbBaDErqiCV"
    zhipu_api_key_macro: str = "2950033ddd854dd19d6bddd137fd98d0.QAdZHnWqdMvXSLTi"
    zhipu_api_key_financial: str = "574f6509e1b14668abfb471515feb250.0fyRlzjU8ajrkdOx"
    zhipu_api_key_fundamental: str = "fa23effc7d4149afaa56b2db89327c1e.OgquEufbWCj4Ggze"
    zhipu_api_key_investment: str = "85dbce2fbb2840c79d5a617562f5a5f9.fMstzGSpTdmJLWPJ"
    zhipu_model: str = "glm-4.7-flash"
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    llm_timeout_seconds: int = 45
    zhipu_thinking_type: str = "enabled"
    zhipu_max_tokens: int = 65536
    # LLM rate-limit and retry controls
    zhipu_retry_max_attempts: int = 3
    zhipu_retry_base_delay_seconds: float = 1.2
    zhipu_retry_max_delay_seconds: float = 8.0
    zhipu_retry_jitter_seconds: float = 0.3
    zhipu_rate_limit_interval_seconds: float = 1.0
    zhipu_allow_cross_role_key_fallback: bool = True
    expert_parallel_workers: int = 5

    max_ranking_symbols: int = 60
    default_history_days: int = 180
    log_level: str = "INFO"
    analysis_worker_threads: int = 4
    ranking_worker_threads: int = 2
    max_background_futures: int = 1000
    # CNInfo ingestion
    cninfo_enabled: bool = True
    cninfo_base_url: str = "http://webapi.cninfo.com.cn"
    cninfo_accept_enckey: str = ""
    cninfo_cookie: str = ""
    cninfo_referer: str = "https://webapi.cninfo.com.cn/"
    cninfo_user_agent: str = "Mozilla/5.0"
    cninfo_timeout_seconds: int = 25
    cninfo_increment_rowcount: int = 1000
    cninfo_financial_strict: bool = True
    cninfo_auto_bootstrap: bool = False
    cninfo_bootstrap_headless: bool = False
    cninfo_bootstrap_retry_on_401: bool = True
    cninfo_headers_cache_file: str = ""
    cninfo_profile_dir: str = ""
    cninfo_bootstrap_wait_seconds: int = 8
    cninfo_header_max_age_seconds: int = 300

    # sentiment module
    hf_cache_dir: str = LOCAL_HF_CACHE_DIR or "/mnt/d/master_dynamic/backend/models/sentiment/.hf_cache"
    sentiment_batch_size: int = 16
    sentiment_guba_model_name: str = (
        LOCAL_SENTIMENT_GUBA_MODEL or "/mnt/d/master_dynamic/backend/models/sentiment/guba_model"
    )
    sentiment_guba_tokenizer_name: str = (
        LOCAL_SENTIMENT_GUBA_TOKENIZER or "/mnt/d/master_dynamic/backend/models/sentiment/guba_tokenizer"
    )
    sentiment_news_model_name: str = (
        LOCAL_SENTIMENT_NEWS_MODEL or "/mnt/d/master_dynamic/backend/models/sentiment/news_model"
    )


settings = Settings()
