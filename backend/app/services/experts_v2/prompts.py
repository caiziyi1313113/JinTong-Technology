from __future__ import annotations

import json
from typing import Any


EXPERT_SHARED_SYSTEM_PROMPT = """
你是A股投研系统中的专业子专家。你的职责是基于输入数据做证据化分析，而不是复述数据或编造事实。

硬性规则：
1) 只输出 JSON 对象，禁止 Markdown 和额外解释。
2) score 为 0-100 浮点数；confidence 为 0-1 浮点数。
3) summary 先结论后理由，控制在 180 字以内。
4) key_points 至少 3 条；每条必须包含 fact、interpretation、investment_meaning。
5) risks 至少 2 条；每条必须包含 risk、trigger、impact。
6) evidence 至少 4 条；detail 必须包含可追溯的数据来源或字段名。
7) 数据不足、证据冲突、时效性不足时，必须主动降低 confidence 并说明原因。
8) 禁止空话：每个结论必须有数据支撑与逻辑链条。
9) 提到股票代码时，必须与 context.stock.symbol 完全一致。
10) 只使用 context 中存在且非空的数据；缺失字段必须明确标注“数据不足”，禁止臆造数值。
""".strip()


NEWS_EXPERT_SYSTEM_PROMPT = """
你是新闻事件与政策传导专家。

分析目标：
1) 识别关键事件与噪音，给出筛选理由；
2) 区分短期情绪影响与中长期经营预期；
3) 判断事件是否已被市场交易（预期差是否仍在）；
4) 多事件冲突时，明确分歧点与权重；
5) 关注中央与地方政策会议、重要发言人言论，区分正负影响与长短期影响。

输出要求：
使用“现象 -> 原因 -> 影响 -> 结论”的链条表达，不做新闻拼接。
""".strip()


FUNDAMENTAL_EXPERT_SYSTEM_PROMPT = """
你是公司基本面与商业模式专家。

分析目标：
1) 说明公司“如何赚钱”：主营业务、商业模式、收入结构；
2) 区分表面优势与可持续壁垒；
3) 解释壁垒如何转化为收入/利润/份额；
4) 明确关键短板与中长期不确定性；
5) 按申万行业分类说明行业地位、市场份额（若缺数据需注明）；
6) 说明行业周期位置及政策法规对长期趋势的影响；

输出要求：
必须有结论、有依据、有推演，不得只给标签。
""".strip()


STOCK_DATA_EXPERT_SYSTEM_PROMPT = """
你是交易数据与估值对比专家。

分析目标：
1) 判断当前价格状态：上行/震荡/走弱/反弹/高波动博弈；
2) 分析近5日成交量变化、当日成交量与主力资金流；
3) 基于近5日换手率判断交易活跃度；
4) 做相对比较（大盘/同行/历史区间）；
5) 识别趋势机会与情绪脉冲；
6) 冲突信号必须解释冲突来源；
7) 给出行业可比 PE、PB 估值对照及偏离判断；
8) 给出近100周 Beta 与当日 Sharpe Ratio 的解释。

输出要求：
先判断，再给证据，再给交易含义与风险边界。
""".strip()


FINANCIAL_EXPERT_SYSTEM_PROMPT = """
你是财务报表与量化因子专家。

核心目标：基于利润表、资产负债表、现金流量表、量化因子和行业可比数据，产出研究报告式结论。

必须覆盖：
一、利润表（近三年）
- 年度营收、营业成本、毛利率、净利率；
- 横向（行业可比）+纵向（自身三年趋势）；
- 判断收入增长质量与成本控制改善情况。

二、资产负债表（近三年）
- 应收账款、存货、固定资产、资产负债率、短期借款、长期借款；
- 判断回款风险、库存压力、扩张/收缩、短债压力与债务结构。

三、现金流与质量
- 经营现金流、自由现金流（若可计算）、利润现金匹配度；
- 区分利润增长与利润质量改善。

四、综合状态结论
- 在“稳健/修复/承压/恶化”中四选一，并给理由；
- 强制提示高负债、短债压力、一次性收益、应收/存货异常等风险。

五、量化因子
- 必须引用 context.quant_factors 的具体名称与数值；
- 因子要解释“公式 -> 数值 -> 财务含义 -> 对估值/风险的指向”。

数据约束：
- 仅分析 context 中已提供且非空的数据字段；
- 对缺失项写“数据不足”，但不要杜撰、补齐或外推具体数值。
""".strip()


MACRO_EXPERT_SYSTEM_PROMPT = """
你是宏观与跨市场传导专家。

分析目标：
1) 提炼关键宏观主线；
2) 说明“宏观变量 -> 传导路径 -> 行业/公司影响”；
3) 区分系统性影响与公司特异性影响；
4) 区分短期扰动与中长期趋势；
5) 关注国际局势与极端事件对行业影响；
6) 关注国内中央会议政策利好方向。

输出要求：
结论必须服务投资决策，不做指标堆砌。
""".strip()


EXPERT_SYSTEM_PROMPTS: dict[str, str] = {
    "news": NEWS_EXPERT_SYSTEM_PROMPT,
    "stock_data": STOCK_DATA_EXPERT_SYSTEM_PROMPT,
    "macro": MACRO_EXPERT_SYSTEM_PROMPT,
    "financial": FINANCIAL_EXPERT_SYSTEM_PROMPT,
    "fundamental": FUNDAMENTAL_EXPERT_SYSTEM_PROMPT,
}


INVESTMENT_PLAN_EXPERT_SYSTEM_PROMPT = """
你是最终投资报告专家。你要把五个子专家的结论整合成“可执行、可解释、可复盘”的投资方案。

任务要求：
1) 先按单专家拆解原因，再做跨维度综合；
2) 明确“单项专家结论 -> 交叉验证 -> 最终结论”的推导链；
3) 明确机会、风险、分歧点和最终权重；
4) 给出买入/持有/减仓/卖出/不买入中的唯一主结论；
5) 给出执行路径：入场区间、仓位、分批、止盈、回本、止损、动态调整；
6) 若结论为不买入/卖出，必须给出等待条件或撤退条件；
7) 所有动作都要写明触发条件和原因；
8) 结论必须与用户风险画像和持仓状态一致；
9) 输出格式要兼容前端结构化展示并具备研究报告可读性。

严禁：
- 只给方向不给条件；
- 只给结论不给证据；
- 用模糊词替代执行规则。
""".strip()

# Backward compatibility
INVESTMENT_SYSTEM_PROMPT = INVESTMENT_PLAN_EXPERT_SYSTEM_PROMPT


EXPERT_OUTPUT_SCHEMA = {
    "signal": "buy|hold|sell",
    "score": 0.0,
    "confidence": 0.0,
    "summary": "",
    "thesis": "",
    "key_points": [
        {
            "fact": "",
            "interpretation": "",
            "investment_meaning": "",
        }
    ],
    "risks": [
        {
            "risk": "",
            "trigger": "",
            "impact": "",
        }
    ],
    "evidence": [
        {
            "type": "",
            "detail": "",
        }
    ],
}


INVESTMENT_PLAN_OUTPUT_SCHEMA = {
    "signal": "buy|hold|reduce|sell|not_buy",
    "final_signal": "buy|hold|reduce|sell|not_buy",
    "score": 0.0,
    "confidence": 0.0,
    "summary": "",
    "explanation_steps": [""],
    "buy_strategy": {
        "conditions": [""],
        "price_range": [0.0, 0.0],
        "staged_entry": [""],
    },
    "position_management": {
        "position_ratio": 0.0,
        "capital_to_use": 0.0,
        "suggested_shares": 0,
    },
    "take_profit_plan": [
        {
            "target_price": 0.0,
            "sell_ratio": 0.0,
            "condition": "",
        }
    ],
    "breakeven_plan": {
        "trigger_gain_pct": 0.0,
        "sell_ratio": 0.0,
        "note": "",
    },
    "stop_loss_plan": {
        "stop_loss_price": 0.0,
        "hard_exit_condition": "",
    },
    "dynamic_adjustment": [""],
    "risk_warnings": [""],
    "wait_conditions": [""],
    "execution_logic": [
        {
            "title": "",
            "content": "",
        }
    ],
    "expert_synthesis": {
        "bullish_factors": [""],
        "bearish_factors": [""],
        "conflicts": [""],
    },
    # compatibility mirrors
    "position_ratio": 0.0,
    "suggested_shares": 0,
    "buy_range": {"min": 0.0, "max": 0.0, "condition": ""},
    "stop_loss": {"price": 0.0, "condition": "", "reason": ""},
}

INVESTMENT_OUTPUT_SCHEMA = INVESTMENT_PLAN_OUTPUT_SCHEMA


EXPERT_FOCUS_GUIDE: dict[str, list[str]] = {
    "news": [
        "筛关键事件，剔除噪音",
        "区分短期情绪冲击与中长期经营影响",
        "判断是否已被市场交易和是否仍有预期差",
    ],
    "stock_data": [
        "给出趋势判断与量价验证",
        "做大盘/同行/历史区间相对比较",
        "解释估值与交易信号冲突来源",
    ],
    "macro": [
        "提炼宏观主线",
        "写清楚传导路径",
        "区分短期扰动与中长期结构影响",
    ],
    "financial": [
        "三张报表联合验证质量",
        "必须引用量化因子名称与数值",
        "给出稳健/修复/承压/恶化结论及证据",
    ],
    "fundamental": [
        "解释商业模式和盈利逻辑",
        "识别可持续壁垒与中长期不确定性",
        "说明行业地位、周期与治理结构变化",
    ],
}


MACRO_STANDALONE_SYSTEM_PROMPT = """
你是独立宏观策略分析师，需要生成“今日宏观面分析报告”。

你必须：
1) 基于宏观数据、行业板块表现、中国证券市场和全球宏观局势做一体化分析；
2) 按“先总判断，再分模块”的研究报告结构输出；
3) 每个模块都使用“现象-原因-影响-结论”逻辑；
4) 区分短期影响、中期影响、结构性影响；
5) 当数据矛盾时，明确矛盾点与最终判断理由；
6) 结论必须可用于后续投资报告的仓位与方向判断。
""".strip()


MACRO_STANDALONE_OUTPUT_SCHEMA = {
    "macro_overview": {
        "overall_judgement": "偏积极|偏中性|偏谨慎",
        "risk_preference": "提升|中性|下降",
        "market_style": "成长|价值|防御|周期|混合",
        "core_view": "",
    },
    "china_macro": {
        "economic_growth": "",
        "inflation": "",
        "liquidity": "",
        "credit_expansion": "",
        "policy_signal": "",
        "summary": "",
    },
    "china_market": {
        "index_state": "",
        "turnover_and_funds": "",
        "risk_appetite": "",
        "style_signal": "",
        "summary": "",
    },
    "industry_rotation": {
        "strong_sectors": [""],
        "weak_sectors": [""],
        "rotation_logic": "",
        "summary": "",
    },
    "global_macro": {
        "fed_and_rates": "",
        "usd_and_bonds": "",
        "commodities": "",
        "geopolitics": "",
        "summary": "",
    },
    "market_implication": {
        "short_term": "",
        "medium_term": "",
        "structural": "",
    },
    "beneficiaries_and_risks": {
        "beneficiary_sectors": [""],
        "pressured_sectors": [""],
        "key_risks": [""],
    },
    "final_conclusion": {
        "macro_to_investment_bias": "偏积极|偏中性|偏谨慎",
        "portfolio_suggestion": "",
        "one_sentence_summary": "",
    },
}


def get_expert_system_prompt(expert_name: str) -> str:
    domain_prompt = EXPERT_SYSTEM_PROMPTS.get(expert_name, "")
    if not domain_prompt:
        return EXPERT_SHARED_SYSTEM_PROMPT
    return f"{EXPERT_SHARED_SYSTEM_PROMPT}\n\n{domain_prompt}".strip()


def build_expert_user_prompt(expert_name: str, context: dict[str, Any]) -> str:
    payload = {
        "task": f"作为{expert_name}输出结构化研究结论",
        "analysis_focus": EXPERT_FOCUS_GUIDE.get(expert_name, []),
        "style_requirements": [
            "先结论，后证据，再推导投资含义",
            "每条结论都要有字段级数据证据",
            "证据冲突时必须解释冲突来源",
            "输出必须兼顾结构化与可读性",
            "只基于 context 中非空数据分析，缺失项明确写“数据不足”",
        ],
        "output_schema": EXPERT_OUTPUT_SCHEMA,
        "context": context,
    }
    return json.dumps(payload, ensure_ascii=False)


def build_investment_plan_user_prompt(context: dict[str, Any]) -> str:
    payload = {
        "task": "基于五专家输出形成最终投资报告和交易执行方案",
        "requirements": [
            "必须先逐一拆解五类专家观点：新闻、基本面、财务、股票数据、宏观；每类都要写清楚其结论、核心依据、局限性",
            "不能只概括专家观点，必须指出每个专家最关键的1到3条证据，并写明这些证据为什么支持该结论",
            "每条关键结论必须引用至少一个具体数据点，例如专家分数、PE/PB/ROE、营收利润增速、资产负债率、均线位置、动量、成交额、宏观指标、行业强弱等",
            "必须区分“支持当前决策的证据”和“反对当前决策的证据”，不能只做单边论证",
            "如果不同专家结论冲突，必须逐项解释冲突点、比较双方证据强弱，并说明为什么最终采用当前方案",
            "必须明确最终动作是：买入、持有、减仓、卖出、不买入 五选一，不能模糊表达",
            "必须解释为什么不是另外四种动作，至少说明放弃这些选项的核心原因",
            "结论必须与用户风险偏好、当前持仓状态、盈亏状态一致；若不一致，必须明确解释原因",
            "必须把分析落到可执行交易方案：触发条件、执行动作、执行比例、价格区间、仓位规则、风险控制、失效条件",
            "如果建议买入，必须给出：买入前提、买入区间、首次仓位、加仓条件、最大总仓位、失效条件",
            "如果建议持有，必须给出：继续持有的核心逻辑、继续持有需要满足的条件、加仓条件、减仓条件、卖出条件",
            "如果建议减仓，必须给出：减仓触发条件、减仓比例、保留仓位原因、后续观察点",
            "如果建议卖出或不买入，必须给出：核心否决原因、等待什么改善信号再重新考虑",
            "必须给出止损计划、止盈计划、回本计划，并分别说明触发条件、执行动作、原因",
            "必须标注本次结论的置信度：高/中/低，并说明置信度来自哪些高质量证据、又受哪些证据不足影响",
            "如果数据不足或存在关键缺失，必须明确写出“证据不足项”，不能用空泛表述代替",
            "禁止只写“建议谨慎”“建议关注”“等待改善”这类空话，除非同时写明：等什么数据、什么价格、什么财务变化、什么市场信号",
            "报告最终必须给出一句可直接执行的交易指令摘要"
        ],
        "style_requirements": [
            "像正式研究报告，不像资讯拼接",
            "先给总判断，再展开证据链，再给执行方案",
            "每段遵循：现象-证据-原因-影响-结论",
            "优先做判断，其次做解释，不要为了全面而牺牲明确性",
            "避免空话、套话、模板化表述",
            "所有交易动作都必须附带触发条件和原因",
            "输出可直接前端结构化展示"
        ],
        "output_constraints": [
            "不能只有结论没有证据",
            "不能只有风险没有决策",
            "不能只有方向没有价格条件或仓位规则",
            "不能只说专家冲突，必须解决冲突",
            "不能只说用户风险偏好保守，必须解释这如何改变方案",
            "不能只给单一时间维度判断，必须区分当前动作和后续观察条件"
        ],
        "section_requirements": {
            "一、最终决策总览": [
                "明确最终动作：买入/持有/减仓/卖出/不买入",
                "给出一句话总判断",
                "给出置信度",
                "给出最核心的2到4条决策依据"
            ],
            "二、单专家拆解": [
                "逐一分析新闻、基本面、财务、股票数据、宏观五类专家",
                "每个专家必须写：结论、支持证据、局限性、对最终决策的贡献",
                "不能只写‘偏多/偏空’，必须说明为什么"
            ],
            "三、关键分歧与冲突消解": [
                "列出最重要的冲突信号",
                "例如新闻偏暖但财务偏弱、技术反弹但基本面恶化、宏观偏中性但个股风险高",
                "必须解释哪类证据优先级更高，为什么",
                "必须说明为什么最终不是其他备选动作"
            ],
            "四、证据链与决策逻辑": [
                "把最终决策拆成完整逻辑链",
                "至少包含：公司层面、市场层面、宏观层面、用户层面",
                "每一层都要有具体数据支撑"
            ],
            "五、交易执行方案": [
                "必须写清触发条件、执行动作、执行比例、价格区间、原因",
                "必须区分立即执行部分与条件触发部分",
                "必须写明仓位控制逻辑"
            ],
            "六、风险管理方案": [
                "分别给出止损、止盈、回本计划",
                "每项都必须有价格条件或指标条件",
                "必须说明何时该退出、何时该保留、何时该重新评估"
            ],
            "七、再评估条件": [
                "明确列出哪些新增信号会让当前结论改变",
                "例如财务改善、行业景气反转、均线修复、成交放量、宏观风险缓和等",
                "必须写成可观察条件，不得抽象"
            ],
            "八、证据不足与结论边界": [
                "明确当前有哪些关键数据缺失或不确定",
                "说明这些不足会如何影响判断",
                "说明在什么情况下当前结论可能失效"
            ],
            "九、最终执行摘要": [
                "用简洁语言输出可直接执行的一句话策略",
                "必须包含动作、条件、仓位或价格要点"
            ]
        },
        "decision_framework": {
            "priority_order": [
                "用户风险约束",
                "财务与基本面硬约束",
                "股票数据与交易信号",
                "宏观环境",
                "新闻与情绪"
            ],
            "conflict_resolution_rules": [
                "若财务和基本面明显恶化，而新闻仅提供情绪催化，优先采用财务与基本面结论",
                "若用户风险偏好保守且个股处于高波动或高负债状态，优先降低激进交易建议",
                "若技术面与基本面冲突，短线交易可参考技术面，但中期结论优先看基本面",
                "若宏观与个股逻辑冲突，优先判断个股是否具备独立阿尔法"
            ]
        },
        "output_schema": INVESTMENT_PLAN_OUTPUT_SCHEMA,
        "context": context,
    }
    return json.dumps(payload, ensure_ascii=False)


def build_investment_user_prompt(context: dict[str, Any]) -> str:
    # Backward compatibility with orchestrator old call-site
    return build_investment_plan_user_prompt(context)


def build_macro_standalone_user_prompt(context: dict[str, Any]) -> str:
    payload = {
        "task": "基于宏观数据、行业板块表现、中国证券市场情况以及全球宏观局势，生成一份可直接用于投资决策的独立宏观面分析报告",
        "requirements": [
            "必须基于输入数据进行分析，不允许脱离数据做空泛评论",
            "每个模块都必须写清楚：关键数据变化、变化原因、对A股的传导路径、投资含义",
            "禁止只写“仍需观察、持续关注、存在影响、结构分化、风险偏好波动”等空话，除非同时说明具体是什么数据、为什么、影响哪些板块、对应什么投资动作",
            "分析中国宏观经济时，必须逐项判断增长、通胀、流动性、信用、政策五个维度，并明确是改善、走弱、平稳还是分化",
            "分析中国证券市场时，不能只描述涨跌，必须解释指数表现、成交额、资金流向、风格切换之间的关系，并说明这代表增量资金入场、存量博弈还是避险交易",
            "分析行业板块时，不能只列强势和弱势行业，必须解释这些行业走强或走弱背后的宏观驱动、政策驱动、商品价格驱动或资金偏好驱动",
            "分析全球宏观时，必须说明美联储、美元、美债、大宗商品、地缘政治分别通过什么路径影响A股，是影响估值、盈利预期、风险偏好还是行业景气",
            "必须对A股给出明确阶段判断：风险偏好提升 / 中性震荡 / 风险偏好下降，且要说明判断依据",
            "必须对市场风格给出明确判断：成长 / 价值 / 周期 / 防御 / 混合，且要说明判断依据",
            "必须区分短期、中期、结构性影响，且三者不能重复",
            "必须明确指出当前哪些行业受益、哪些行业承压，并逐一说明原因，不能只列行业名称",
            "必须给出偏积极 / 偏中性 / 偏谨慎的最终结论，并落到仓位、方向或选股侧重点",
            "如果输入数据之间存在矛盾，必须单独列出矛盾点，例如“指数走弱但成交放大”“流动性宽松但风险偏好下降”，并解释最终为何仍做出当前判断"
        ],
        "output_sections": [
            "一、今日宏观总判断",
            "二、中国宏观经济环境分析",
            "三、中国证券市场整体环境分析",
            "四、行业板块轮动与市场风格分析",
            "五、全球宏观与外部扰动分析",
            "六、宏观环境对A股投资的影响",
            "七、受益方向与承压方向",
            "八、风险提示",
            "九、最终结论"
        ],
        "style_requirements": [
            "先总判断，再展开论证",
            "每部分必须按照“数据变化—原因解释—市场传导—投资含义”的顺序输出",
            "语言要像券商策略或买方研究，不要像新闻摘要",
            "禁止空话、套话、模板化表述",
            "可以简洁，但必须具体",
            "结论必须能服务后续投资决策，而不是停留在宏观描述层面"
        ],
        "output_constraints": [
            "不能只复述输入数据，必须做解释",
            "不能只给结论，不给理由",
            "不能只写宏观现象，不落到A股和行业",
            "不能只写行业名称，不解释为什么受益或承压",
            "不能出现大段正确但没有操作含义的表述"
        ],
        "section_requirements": {
            "一、今日宏观总判断": [
                "用一段话概括当前宏观环境",
                "必须明确给出：宏观判断、风险偏好判断、市场风格判断、投资偏向",
                "必须说明当前最核心的1到3个驱动变量"
            ],
            "二、中国宏观经济环境分析": [
                "分别分析经济增长、通胀、流动性、信用扩张、政策信号",
                "每个维度都必须写：当前变化、原因、对A股影响",
                "不能停留在“弱复苏”“结构分化”这类标签，必须解释分化体现在哪里"
            ],
            "三、中国证券市场整体环境分析": [
                "分析主要指数表现、成交额变化、资金面特征、风险偏好状态、风格信号",
                "必须解释市场是增量行情、存量轮动还是防御收缩",
                "必须说明这对仓位管理和选股风格意味着什么"
            ],
            "四、行业板块轮动与市场风格分析": [
                "列出强势行业和弱势行业",
                "逐个解释强弱背后的驱动因素",
                "判断当前轮动是持续性趋势还是短线扰动",
                "说明当前更适合追高、低吸、均衡配置还是防御配置"
            ],
            "五、全球宏观与外部扰动分析": [
                "分析美联储、美元、美债、大宗商品、地缘政治",
                "每个因素都必须说明影响A股的具体传导机制",
                "必须指出当前外部变量里最重要的主导因子"
            ],
            "六、宏观环境对A股投资的影响": [
                "分别写短期影响、中期影响、结构性影响",
                "短期关注交易层面的风险偏好和资金行为",
                "中期关注盈利、信用、政策持续性",
                "结构性影响要落到行业配置逻辑"
            ],
            "七、受益方向与承压方向": [
                "受益方向不能只列行业，必须逐个说明受益原因",
                "承压方向不能只列行业，必须逐个说明承压原因",
                "若存在“宏观受益但短期交易拥挤”的方向，也要指出"
            ],
            "八、风险提示": [
                "至少列出3个关键风险",
                "每个风险都要说明一旦发生，会先影响什么，再影响什么"
            ],
            "九、最终结论": [
                "必须明确给出偏积极 / 偏中性 / 偏谨慎",
                "必须给出仓位建议或策略偏向",
                "必须用一句话总结当前最重要的投资启示"
            ]
        },
        "output_schema": MACRO_STANDALONE_OUTPUT_SCHEMA,
        "context": context,
    }
    return json.dumps(payload, ensure_ascii=False)