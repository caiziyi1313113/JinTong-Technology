智能股票分析系统
基于大语言模型的多专家协作股票分析与投资决策平台
项目简介
本项目是一个面向个人投资者的智能股票分析系统，采用 LangChain + LangGraph 框架构建多专家协作架构，集成 RAG 知识库、大语言模型、金融数据 API 与可视化工具，实现从数据采集、多维度分析到个性化投资建议生成的全链路自动化。
系统核心特色：
五专家并行分析：新闻、股票数据、宏观面、财务数据、公司基本面五大领域专家独立分析
个性化投资方案：基于用户风险画像与持仓情况，生成包含买入、仓位、止盈、止损的完整交易策略
智能复盘与追踪：每日闭盘后自动复盘生成推荐排行榜，实时追踪用户持仓股票并提供动态建议
情绪指数分析：集成金融情绪模型，量化市场情绪并应用于投资策略
量化因子计算：基于财务数据计算 ROE、PE、PB 等核心量化指标，提升分析专业性
系统架构
plain
复制
┌─────────────────────────────────────────────────────────────┐
│                        用户前端 (Web/App)                      │
└─────────────────────────────┬───────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│              用户画像 & 偏好系统 (问卷 / 行为 / 资产)           │
└─────────────────────────────┬───────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│              决策融合层 (LLM + Multi-Agent)                   │
│         投资专家 (整合5专家结果 + 用户个性 → 投资建议)          │
└─────────────┬─────────────┬─────────────┬───────────┬───────┘
              │             │             │           │
    ┌─────────▼────┐ ┌──────▼──────┐ ┌────▼─────┐ ┌───▼────┐ ┌──▼───┐
    │  新闻专家    │ │ 股票数据专家 │ │ 宏观面专家│ │财务专家│ │基本面│
    │ (舆情分析)   │ │ (技术分析)   │ │ (宏观分析)│ │(财报) │ │专家  │
    └──────────────┘ └─────────────┘ └──────────┘ └────────┘ └──────┘
                              │
                   ┌──────────▼──────────┐
                   │   数据采集与清洗层   │
                   │ (AKShare / 东方财富) │
                   └─────────────────────┘
核心功能模块
1. 智能复盘系统
定时触发：每日闭盘后（16:00）自动执行
数据更新：自动吸纳当日交易数据、新闻、宏观面变化；财务与基本面数据按月手动更新
排行榜生成：五专家分别评分，加权计算综合得分并排序
详情分析：点击股票进入分析页，展示公司概况、五专家分析、投资建议与信号
信号冲突检测：自动识别数据驱动专家（股票数据）与情绪驱动专家（新闻、宏观）的信号冲突，以红/绿灯警示
2. 持仓追踪与实时建议
展示用户持仓股票的代码、名称、交易记录（时间、股数、单价、买卖方向）
开盘实时追踪：监测交易数据、新闻、宏观面变化，实时更新分析与建议
闭盘复用：无新数据时复用历史分析结果
3. 单股查询分析
输入股票代码查询单只股票
展示公司基本情况、五专家分析结果、投资建议与方案
支持日K线、周K线、月K线、成交量等交互式可视化图表（可拖拽、缩放、点击查看数据）
4. 情绪分析模块
双模型融合：
RoBERTa_based_on_eastmoney_guba_comments（东方财富股吧评论情绪）
finbert-tone-chinese（金融分析师报告情绪）
情绪指数构建：基于新闻与股吧评论计算每日情绪指数
趋势分析：滚动计算多日情绪变化，识别情绪拐点
策略应用：结合估值偏离度与情绪趋势，生成做多/卖出/观望/反转布局建议
价格相关性验证：计算情绪指数与股价的相关性，评估信号可靠性
5. 独立宏观分析
独立导航栏模块，一键生成今日宏观分析报告
综合分析：中国宏观经济、证券市场、行业板块轮动、全球宏观局势
输出结构化报告：总判断 → 宏观环境 → 市场分析 → 行业轮动 → 全球影响 → 投资结论
技术栈
表格
层级	技术选型
前端框架	React / Vue (Web/App)
后端框架	Python + FastAPI
AI 框架	LangChain + LangGraph
大语言模型	智谱 AI (Zhipu GLM)
金融数据	AKShare、东方财富爬虫
情绪模型	HuggingFace Transformers (RoBERTa / FinBERT)
数据库	MySQL / PostgreSQL
可视化	ECharts / TradingView Charting Library
部署	Docker + Nginx
专家体系详解
新闻专家
公司重大新闻事件、行业政策变化、监管动态
并购合作、市场舆情、媒体报道
区分短期情绪影响与中长期经营预期
识别关键事件与噪音，判断市场是否已交易该信息
股票数据专家
历史/实时交易数据、大宗交易、K线走势
技术指标（均线、MACD、RSI）、成交量与换手率
资金流向分析（主力/散户）
判定趋势状态，识别主力资金动向与交易活跃度
宏观面专家
国内宏观经济（GDP、CPI、PMI、社融、利率等）
货币政策与财政政策变化
国际经济形势、全球市场变化
提炼宏观主线，分析"宏观变量 → 传导路径 → 公司影响"
财务数据专家
三大报表分析（资产负债表、利润表、现金流量表）
量化因子计算：ROE、ROA、PE、PB、PS、毛利率、资产负债率等
近三年财务趋势与行业对比
判断公司状态：稳健 / 修复 / 承压 / 恶化
公司基本情况专家
主营业务、商业模式、行业地位（申万分类）
核心竞争优势与可持续壁垒
股权结构、前十大股东变化
公司战略与长期发展潜力
投资专家（整合层）
融合五专家分析结果
结合用户风险画像（RSI 指数）与持仓情况
生成完整投资方案：买入策略、仓位管理、止盈、回本、止损、动态调整
用户风险画像模型
基于 Kahneman-Tversky 前景理论 与 Grable-Lytton 财务风险容忍度量表，构建四维风险敏感度量化模型：
表格
维度	权重	说明
D1 损失厌恶	35%	最大可承受亏损金额
D2 风险舒适区	30%	下跌20%时的应对行为
D3 投资视界	15%	计划投资时长
D4 金融素养	20%	历史投资经验与品类
风险敏感度指数（RSI）：[0, 1] 连续值
[0.00, 0.25)：保守型
[0.25, 0.50)：稳健型
[0.50, 0.75)：进取型
[0.75, 1.00]：激进型
项目结构
plain
复制
├── backend/
│   ├── agents/                 # LangGraph 智能体定义
│   │   ├── news_agent.py
│   │   ├── stock_data_agent.py
│   │   ├── macro_agent.py
│   │   ├── financial_agent.py
│   │   ├── fundamental_agent.py
│   │   └── investment_agent.py
│   ├── prompts/                # 专家提示词模板
│   │   └── prompt.py
│   ├── data_collection/        # 数据采集模块
│   │   ├── akshare_client.py
│   │   └── eastmoney_crawler.py
│   ├── financial_analysis/     # 财务数据与量化因子
│   │   ├── financial_crawler.py
│   │   ├── quant_factors.py
│   │   └── tests/
│   │       ├── test_crawler.py
│   │       └── test_factors.py
│   ├── sentiment/              # 情绪分析模块
│   │   ├── sentiment_model.py
│   │   ├── emotion_index.py
│   │   └── strategy.py
│   ├── database/               # 数据库模型与操作
│   ├── api/                    # FastAPI 路由
│   └── main.py
├── frontend/
│   ├── src/
│   │   ├── components/         # 可视化图表组件
│   │   ├── pages/              # 页面路由
│   │   └── services/           # API 调用
│   └── package.json
├── docker-compose.yml
└── README.md
快速开始
环境要求
Python 3.10+
Node.js 18+
MySQL 8.0+
安装依赖
bash
复制
# 后端
cd backend
pip install -r requirements.txt

# 前端
cd frontend
npm install
配置环境变量
bash
复制
cp .env.example .env
# 编辑 .env 文件，配置数据库连接与智谱 API Key
初始化数据库
bash
复制
python backend/scripts/init_db.py
启动服务
bash
复制
# 后端
uvicorn backend.main:app --reload

# 前端
cd frontend && npm run dev
验证脚本
项目提供独立的验证脚本，确保核心功能可靠性：
bash
复制
# 验证东方财富财务数据爬取
python backend/financial_analysis/tests/test_crawler.py

# 验证量化因子计算
python backend/financial_analysis/tests/test_factors.py

# 验证股吧评论爬取
python backend/sentiment/tests/test_guba_crawler.py

# 验证新闻数据获取
python backend/sentiment/tests/test_news_fetch.py

# 验证情绪指标计算（支持指定日期回溯）
python backend/sentiment/tests/test_emotion_index.py --date 2026-04-30
核心算法说明
情绪指数构建
爬取东方财富股吧评论与当日新闻
数据清洗：过滤表情包、删除 >200 字评论
双模型推理：分别输出 Positive / Neutral / Negative
日度情绪指数 = 加权平均情感得分
滚动趋势：计算 (t, t-1), (t-1, t-2) ... (t-4, t-5) 五日变化量
并行专家加速
利用 6 个智谱 API Key 分别为 5 个专家实例分配独立账号
数据就绪后同时触发 5 个专家并行分析
全部返回后由投资专家整合生成最终建议
参考文献
Kahneman, D., & Tversky, A. (1979). Prospect Theory: An Analysis of Decision under Risk. Econometrica, 47(2), 263-291. https://doi.org/10.2307/1914185
Grable, J. E., & Lytton, R. H. (1999). Financial risk tolerance revisited. Financial Services Review, 8(3), 163-181. https://doi.org/10.1016/S1057-0810(99)00041-4
LLM Sentiment Scoring for Next-Day Return Prediction. https://arxiv.org/abs/2412.19245
