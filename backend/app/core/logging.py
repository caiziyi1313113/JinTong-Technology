import logging
import os


DEFAULT_LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | %(threadName)s | "
    "%(message)s"
)


def setup_logging(level: str = "INFO") -> None:
    normalized = (level or "INFO").upper()
    log_level = getattr(logging, normalized, logging.INFO)

    root_logger = logging.getLogger()

    # Respect pre-configured handlers if the runtime already set one.
    if not root_logger.handlers:
        logging.basicConfig(
            level=log_level,
            format=DEFAULT_LOG_FORMAT,
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        root_logger.setLevel(log_level)
        formatter = logging.Formatter(DEFAULT_LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)

    # Quiet noisy libraries unless explicitly overridden.
    for noisy in ("urllib3", "httpx", "zai", "matplotlib"):
        env_key = f"LOG_LEVEL_{noisy.upper()}"
        noisy_level = os.getenv(env_key, "WARNING").upper()
        logging.getLogger(noisy).setLevel(getattr(logging, noisy_level, logging.WARNING))
