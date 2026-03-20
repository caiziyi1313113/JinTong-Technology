from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def _json_default(dialect: str) -> str:
    if dialect == "postgresql":
        return "'{}'::json"
    return "'{}'"


def _float_type(dialect: str) -> str:
    if dialect == "sqlite":
        return "REAL"
    return "DOUBLE PRECISION"


def _ensure_column(conn, *, table: str, column: str, ddl: str, existing_columns: set[str]) -> bool:
    if column in existing_columns:
        return False
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    return True


def apply_schema_compat_migrations(engine: Engine) -> dict[str, int]:
    """
    Lightweight compatibility migrations for environments without Alembic.
    """
    inspector = inspect(engine)
    applied = 0
    dialect = engine.dialect.name
    json_default = _json_default(dialect)

    if not inspector.has_table("macro_news"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE macro_news (
                        id INTEGER PRIMARY KEY,
                        title VARCHAR(255) NOT NULL,
                        content TEXT NOT NULL,
                        source VARCHAR(255),
                        published_at TIMESTAMP NULL,
                        metadata JSON NOT NULL DEFAULT '{}',
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                    )
                )
        applied += 1

    if not inspector.has_table("company_financial_events"):
        id_ddl = "BIGSERIAL PRIMARY KEY" if dialect == "postgresql" else "INTEGER PRIMARY KEY"
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE company_financial_events (
                        id {id_ddl},
                        stock_id INTEGER NOT NULL,
                        event_date DATE NOT NULL,
                        event_name VARCHAR(96) NOT NULL,
                        event_type VARCHAR(64) NULL,
                        source VARCHAR(64) NULL,
                        dataset VARCHAR(64) NULL,
                        row_key VARCHAR(128) NULL,
                        object_id BIGINT NULL,
                        change_code INTEGER NULL,
                        declare_date DATE NULL,
                        start_date DATE NULL,
                        end_date DATE NULL,
                        raw JSON NOT NULL DEFAULT {json_default},
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_company_financial_event UNIQUE (stock_id, event_date, event_name)
                    )
                    """
                )
            )
        applied += 1

    if not inspector.has_table("user_stock_holdings"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE user_stock_holdings (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        stock_id INTEGER NOT NULL,
                        stock_symbol VARCHAR(32) NOT NULL,
                        quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                        avg_price DOUBLE PRECISION NOT NULL DEFAULT 0,
                        total_buy_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                        total_sell_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (user_id, stock_id)
                    )
                    """
                )
            )
        applied += 1

    if not inspector.has_table("user_profiles"):
        return {"applied": applied}

    if inspector.has_table("documents"):
        document_columns = {col["name"] for col in inspector.get_columns("documents")}
        with engine.begin() as conn:
            if _ensure_column(
                conn,
                table="documents",
                column="stock_id",
                ddl="INTEGER NULL",
                existing_columns=document_columns,
            ):
                applied += 1
                document_columns.add("stock_id")

    if inspector.has_table("expert_signals"):
        expert_signal_columns = {col["name"] for col in inspector.get_columns("expert_signals")}
        with engine.begin() as conn:
            if _ensure_column(
                conn,
                table="expert_signals",
                column="fallback",
                ddl="BOOLEAN NOT NULL DEFAULT FALSE",
                existing_columns=expert_signal_columns,
            ):
                applied += 1
                expert_signal_columns.add("fallback")

    if inspector.has_table("company_financials"):
        financial_columns = {col["name"] for col in inspector.get_columns("company_financials")}
        with engine.begin() as conn:
            financial_migrations = [
                ("source", "VARCHAR(64) NULL"),
                ("dataset", "VARCHAR(64) NULL"),
                ("row_key", "VARCHAR(128) NULL"),
                ("object_id", "BIGINT NULL"),
                ("change_code", "INTEGER NULL"),
                ("declare_date", "DATE NULL"),
                ("start_date", "DATE NULL"),
                ("end_date", "DATE NULL"),
                ("updated_at", "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"),
            ]
            for name, ddl in financial_migrations:
                if _ensure_column(
                    conn,
                    table="company_financials",
                    column=name,
                    ddl=ddl,
                    existing_columns=financial_columns,
                ):
                    applied += 1
                    financial_columns.add(name)

    float_type = _float_type(dialect)

    columns = {col["name"] for col in inspector.get_columns("user_profiles")}
    migrations = [
        ("risk_level", "VARCHAR(32) NOT NULL DEFAULT 'medium'"),
        ("investment_horizon", "VARCHAR(32) NOT NULL DEFAULT 'long'"),
        ("income", f"{float_type} NOT NULL DEFAULT 0"),
        ("assets", f"{float_type} NOT NULL DEFAULT 0"),
        ("disposable_funds", f"{float_type} NOT NULL DEFAULT 0"),
        ("experience_years", f"{float_type} NOT NULL DEFAULT 0"),
        ("max_drawdown", f"{float_type} NOT NULL DEFAULT 0.2"),
        ("risk_budget", f"{float_type} NOT NULL DEFAULT 0.02"),
        ("target_return", f"{float_type} NOT NULL DEFAULT 0.12"),
        ("max_single_position", f"{float_type} NOT NULL DEFAULT 0.15"),
        ("style", "VARCHAR(32) NOT NULL DEFAULT 'balanced'"),
        ("persona", "VARCHAR(64) NOT NULL DEFAULT 'balanced_growth'"),
        ("questionnaire_answers", f"JSON NOT NULL DEFAULT {json_default}"),
        ("preferences", f"JSON NOT NULL DEFAULT {json_default}"),
    ]

    with engine.begin() as conn:
        for name, ddl in migrations:
            if _ensure_column(
                conn,
                table="user_profiles",
                column=name,
                ddl=ddl,
                existing_columns=columns,
            ):
                columns.add(name)
                applied += 1

    return {"applied": applied}
