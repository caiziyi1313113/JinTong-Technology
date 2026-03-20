# Stock Intelligence System (AKShare + Five Experts + Investment Expert)

本项目已升级为一个可落地的多专家股票分析系统：
- `数据层`: AKShare 拉取实时/历史/公告/财务/大宗交易，并入库
- `分析层`: 新闻专家、股票数据专家、宏观面专家、财务数据专家、公司基本面专家
- `决策层`: 投资专家整合五专家结论 + 用户画像 + 持仓，输出可执行交易方案
- `展示层`: 前端展示排行榜、冲突信号（红/绿灯）、专家依据、投资方案
- `市场范围`: 仅支持深圳主板 A 股（`000/001/002/003` 开头）

## 1. 目录
- `backend/`: FastAPI + SQLAlchemy + PostgreSQL
- `frontend/`: React + Vite
- `backend/scripts/run_pipeline.py`: 一键跑数据同步 + 排行

## 2. 核心能力（已实现）
### 2.1 数据采集与入库
- 实时行情: `stock_sz_a_spot_em`（并过滤为深圳主板 A 股）
- 历史 K 线（日/周/月）: `stock_zh_a_hist`
- 大宗交易: `stock_dzjy_mrmx`
- 公司基本面: `stock_individual_info_em` + `stock_zyjs_ths`
- 财务指标: `stock_financial_analysis_indicator_em`
- 全局快讯（新闻/宏观）: `stock_info_global_em`
- 公司公告: `stock_zh_a_disclosure_report_cninfo`

### 2.2 数据表（新增）
- `stock_klines`: 日/周/月 K 线
- `stock_quotes`: 实时快照
- `block_trade_records`: 大宗交易明细
- `company_fundamentals`: 公司基本面快照（月度手动更新）
- `company_financials`: 财务指标快照（月度手动更新）
- `ranking_snapshots` + `ranking_items`: 排行榜快照及明细
- `portfolio_trades`: 用户交易流水
- `data_sync_logs`: 同步任务日志

### 2.3 专家体系
- 五专家由 LLM 驱动（优先智谱），若无 Key 自动回退到规则专家
- 投资专家输出结构化交易方案：
  - 买入策略
  - 仓位管理
  - 止盈计划
  - 回本策略
  - 止损策略
  - 动态调整机制

### 2.4 冲突信号
- 数据驱动分数: 股票数据专家
- 情绪驱动分数: 新闻专家 + 宏观专家均值
- 方向相反则 `conflict_signal=true`（前端红灯），一致则绿灯

## 3. 后端启动
### 3.1 安装依赖
```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate
pip install -r requirements.txt
```

### 3.2 环境变量（示例）
```bash
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/stockai
JWT_SECRET=change-me
ALLOWED_ORIGINS=http://localhost:5173

# 启用智谱 LLM（zai-sdk）
ZHIPU_API_KEY=your_key
ZHIPU_MODEL=glm-4.7-flash
ZHIPU_THINKING_TYPE=enabled
ZHIPU_MAX_TOKENS=65536
LLM_TIMEOUT_SECONDS=45
```

### 3.3 智谱 SDK 验证（可选）
```bash
# 项目依赖已包含 zai-sdk==0.2.2，也可单独安装
pip install zai-sdk==0.2.2

# 检查 SDK 与 API 连通性（无 key 仅检查安装）
python scripts/check_zhipu.py

# 指定 key 做一次真实调用
python scripts/check_zhipu.py --api-key your_key --model glm-4.7-flash
```

### 3.4 初始化数据库并启动
```bash
python scripts/init_db.py
python scripts/seed_demo.py
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 4. 前端启动
```bash
cd frontend
npm install
npm run dev
```

## 5. 新增 API
### 5.1 数据同步
- `POST /api/v1/data/sync/daily`
- `POST /api/v1/data/sync/static`
- `GET /api/v1/data/sync/logs`

### 5.2 排行榜
- `POST /api/v1/workflow/ranking/run`
- `GET /api/v1/workflow/ranking/latest`
- `GET /api/v1/workflow/ranking/{snapshot_id}`

### 5.3 单股分析
- `POST /api/v1/analysis`
- `GET /api/v1/analysis/{id}`

### 5.4 持仓与交易流水
- `GET /api/v1/portfolio/positions`
- `POST /api/v1/portfolio/positions`
- `POST /api/v1/portfolio/positions/{id}/close`
- `GET /api/v1/portfolio/trades`
- `POST /api/v1/portfolio/trades`

### 5.5 单股情绪分析（新闻 + 股吧双模型）
- `GET /api/v1/sentiment/{symbol}/latest`
- `POST /api/v1/sentiment/{symbol}/compute`

## 6. 自动化脚本
```bash
# 全流程（同步 + 排行）
python backend/scripts/run_pipeline.py --mode all --date 2026-03-09 --top-n 30 --snapshot-type post_close

# 仅静态全量（每月）
python backend/scripts/run_pipeline.py --mode static --symbols 000001,002594,003816

# 静态全量（深圳主板A股全部，推荐月度手动跑）
python backend/scripts/run_static_all_sz_main.py --refresh-universe --batch-size 50 --sleep-seconds 0.8

# 仅排行（不拉数据）
python backend/scripts/run_pipeline.py --mode ranking --date 2026-03-09 --top-n 30 --snapshot-type pre_open
```

## 6.1 查询触发增量更新（已实现）
- 前端触发 `POST /api/v1/analysis` 时，后端会先对该股票执行 `sync_symbol_hot_data` 增量检查：
  - 单股实时行情
  - 必要时日/周/月K线补齐
  - 公司公告增量
  - 公司相关新闻（从全局快讯筛该股票）
  - 宏观快讯
- 然后再运行五专家与投资专家，返回最新分析、投资建议与信号。
- 触发交易计划 `POST /api/v1/trades/plans` 时也会先做同样的单股增量刷新。

## 6.3 情绪模块验证脚本
```bash
# 1) 验证东方财富股吧抓取
python backend/scripts/sentiment/test_guba_scraper.py --symbol 000056 --date 2026-03-13 --max-pages 5 --show 10

# 2) 验证 AKShare 新闻抓取
python backend/scripts/sentiment/test_akshare_news.py --symbol 000056 --date 2026-03-13 --show 10

# 3) 验证新闻文本 -> 模型打分（finbert-tone-chinese）
python backend/scripts/sentiment/test_news_model_scoring.py --symbol 000056 --date 2026-03-13 --max-items 60 --show 15

# 4) 验证股吧文本 -> 模型打分（RoBERTa_based_on_eastmoney_guba_comments）
python backend/scripts/sentiment/test_guba_model_scoring.py --symbol 000056 --date 2026-03-13 --show 15

# 5) 按指定日期计算并入库（新闻+股吧一起，适用于历史回填）
python backend/scripts/sentiment/run_sentiment_for_date.py --symbol 000056 --date 2026-03-13

# 6) 按日期区间批量回填（start/end 一次补齐）
python backend/scripts/sentiment/run_sentiment_backfill_range.py --symbol 002080 --start-date 2026-02-01 --end-date 2026-03-20 --continue-on-error --print-each
```
002648
## 6.4 重新启动程序（启用情绪功能）
```bash
# 1) 安装后端依赖（情绪模型需要 transformers + torch）
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 2) 初始化/更新表结构（会创建 stock_sentiment_daily / stock_sentiment_item）
python scripts/init_db.py

# 3) 启动后端
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 4) 新开终端启动前端
cd frontend
npm install
npm run dev
```

## 6.5 本地下载情绪模型（不依赖 .env）
如果你希望模型完全走本地目录，不走线上 HuggingFace，可执行：

```bash
# 在项目根目录
python backend/scripts/sentiment/download_sentiment_models.py --base-dir backend/models/sentiment --hf-endpoint https://hf-mirror.com
```

下载后，修改 `backend/app/core/config.py` 顶部四个常量：
- `LOCAL_SENTIMENT_GUBA_MODEL`
- `LOCAL_SENTIMENT_GUBA_TOKENIZER`
- `LOCAL_SENTIMENT_NEWS_MODEL`
- `LOCAL_HF_CACHE_DIR`

> 这些常量有值时会优先覆盖默认远程模型名，因此无需再在 `.env` 里配置情绪模型路径。

验证：
```bash
python backend/scripts/sentiment/test_guba_model_scoring.py --symbol 601669 --date 2026-03-13 --max-pages 5 --max-items 50 --show 15
```

## 6.2 首次启动建议（先有数据再做“是否更新”检测）
```bash
# 1) 初始化库
python backend/scripts/init_db.py

# 2) 建立股票池 + 高频基础数据
python backend/scripts/run_pipeline.py --mode daily --date 2026-03-09

# 3) 一次性补齐低频静态数据（全市场）
python backend/scripts/run_static_all_sz_main.py --refresh-universe --batch-size 50 --sleep-seconds 0.8
```

## 7. 推荐调度
- 每个交易日 `16:00`：`daily sync + post_close ranking`
- 次日开盘前（如 `08:50`）：`pre_open ranking`
- 每月 1 次：`static sync`（公司基本面、财务全量）

## 8. 注意事项
- 当前仅支持深圳主板 A 股；非目标市场代码会在同步与分析阶段被自动跳过或拒绝。
- 如果本地没有配置 `ZHIPU_API_KEY`，系统自动使用规则专家 fallback，保证可运行。
- 首次跑 A 股全量同步耗时较长，建议先用 `symbols` 小范围试跑。
- 前端/后端需分别安装依赖；当前环境未安装 `npm` 或 Python 包时无法本地直接运行。
