# Stock Intelligence MVP (WSL2 + Ubuntu 24.04 + PostgreSQL)

This is a full-stack MVP for a multi-expert stock analysis system with a decision fusion layer and personalized investor profiles. The backend is FastAPI + PostgreSQL. The frontend is React + Vite.

## Repository layout
- `backend/` FastAPI API, expert modules, decision fusion
- `frontend/` React UI
- `docker-compose.yml` One-command local deployment

## Local setup (WSL2 Ubuntu 24.04)

### 1. Install system dependencies
1. `sudo apt update`
2. `sudo apt install -y python3.11 python3.11-venv python3-pip postgresql postgresql-contrib nodejs npm`

### 2. Start PostgreSQL and create database
1. `sudo service postgresql start`
2. `sudo -u postgres psql -c "CREATE DATABASE stockai;"`
3. `sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'postgres';"`

### 3. Backend setup
1. `cd backend`
2. `python3.11 -m venv .venv`
3. `source .venv/bin/activate`
4. `pip install -r requirements.txt`
5. `export DATABASE_URL='postgresql+psycopg2://postgres:postgres@localhost:5432/stockai'`
6. `export JWT_SECRET='change-me'`
7. `export ALLOWED_ORIGINS='http://localhost:5173'`
8. `python scripts/init_db.py`
9. `python scripts/seed_demo.py`
10. `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`

If you already created tables from an older version, drop/recreate schema before step 8 or run migrations. This project currently uses `create_all` instead of Alembic migrations.

The API will be available at `http://localhost:8000`.

### 4. Frontend setup
1. `cd frontend`
2. `npm install`
3. `npm run dev`

The UI will be available at `http://localhost:5173`.

Main pages:
- `/home` Project home page
- `/login` Login / register page
- `/discover` 寻找标地（收盘复盘 + 开盘扫描）
- `/track` 跟踪股票（持仓 + 交易计划 + 交易信号）
- `/query` 查询股票（单股票智能分析与解释）

## Docker setup (recommended for quick start)
1. `docker compose up --build`
2. `docker compose exec backend python scripts/init_db.py`
3. `docker compose exec backend python scripts/seed_demo.py`

Open the UI at `http://localhost:8080`.

## Key API endpoints
- `POST /api/v1/users/guest` create a demo user session
- `GET /api/v1/profiles/me` get investor profile
- `PUT /api/v1/profiles/me` update investor profile
- `POST /api/v1/analysis` run multi-expert analysis
- `GET /api/v1/analysis/{id}` fetch analysis result
- `POST /api/v1/workflow/post-close-review` generate daily recap and candidate pool
- `POST /api/v1/workflow/pre-open-scan` rank pre-open TopN from candidate pool
- `GET /api/v1/portfolio/positions` list user positions
- `POST /api/v1/portfolio/positions` upsert user position
- `POST /api/v1/trades/plans` generate trade plan with entry/exit rules and suggested shares
- `POST /api/v1/trades/signals` generate trade signal from a trade plan

## Notes
- Expert models are rule-based placeholders with clear extension points.
- Documents in `documents` table act as RAG evidence; seed data is included.
- The decision engine now fuses by `risk_level + investment_horizon + style`.
- Rationale includes `sentiment_score`, `data_score`, `alignment`, `conflict_reason`, and `decision_note`.
- Trade advice includes entry range, ladder prices, stop-loss/take-profit, trailing stop, and suggested share size.

## Next steps you can build on
- Replace expert placeholders with actual ML models
- Add vector embeddings and pgvector extension
- Add backtesting pipeline and scheduler
