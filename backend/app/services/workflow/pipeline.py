from datetime import date
from statistics import mean, pstdev

from sqlalchemy.orm import Session

from app.core.market_scope import TARGET_MARKET
from app.models.candidate_pool import CandidatePool
from app.models.daily_recap import DailyRecap
from app.models.document import Document
from app.models.market import MarketData
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.services.sentiment.sentiment import score_text

#每日“复盘 + 次日开盘前扫描”自动流水线
'''
6.1 generate_post_close_review(db, trade_date, top_n)
做“收盘复盘”：
先把当天旧的 CandidatePool/DailyRecap 删掉（重新生成）
计算宏观情绪 _macro_sentiment()（来自 macro/policy 文档）
遍历所有 Stock：拿最近 30 天行情，算：
今日 vs 昨日涨跌幅 pct_change
20 日波动 volatility

算两个分：
data_score：涨跌幅加分、波动扣分
sentiment_score：7 日新闻情绪 + 宏观情绪混合
合成 total_score = 0.55*data + 0.45*sentiment

满足“波动/涨跌异常”或 total_score 高的加入候选池
保存 CandidatePool（候选股票池）+ 保存 DailyRecap（复盘总结+top movers）

输出：(DailyRecap, [CandidatePool])

6.2 generate_pre_open_scan(db, scan_date, top_n)

做“开盘前扫描”：
删当天旧 ScanResult 
从 CandidatePool 取同日数据；没有就回退到最近一次候选池日期
用“更近的 2 日新闻情绪”做 rescore：rescore = 0.7*base + 0.3*fresh_news 
根据 rescore 给动作：
=0.62 → buy
<=0.4 → avoid
else → watch 
pipeline
排名写入 ScanResult 并返回 top_n 

pipeline
'''
def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _latest_rows(db: Session, stock_id: int, limit: int = 30) -> list[MarketData]:
    return (
        db.query(MarketData)
        .filter(MarketData.stock_id == stock_id)
        .order_by(MarketData.date.desc())
        .limit(limit)
        .all()
    )


def _news_sentiment(db: Session, stock_symbol: str, days_back: int = 7) -> float:
    docs = (
        db.query(Document)
        .filter(Document.stock_symbol == stock_symbol)
        .filter(Document.doc_type.in_(["news", "announcement", "fundamental"]))
        .order_by(Document.published_at.desc().nullslast(), Document.created_at.desc())
        .limit(max(3, days_back))
        .all()
    )
    if not docs:
        return 0.5
    score = sum(score_text(doc.content) for doc in docs) / len(docs)
    return _clip(0.5 + score / 8)


def _macro_sentiment(db: Session) -> float:
    docs = (
        db.query(Document)
        .filter(Document.doc_type.in_(["macro", "policy"]))
        .order_by(Document.published_at.desc().nullslast(), Document.created_at.desc())
        .limit(10)
        .all()
    )
    if not docs:
        return 0.5
    score = sum(score_text(doc.content) for doc in docs) / len(docs)
    return _clip(0.5 + score / 10)


def generate_post_close_review(db: Session, trade_date: date, top_n: int = 20) -> tuple[DailyRecap, list[CandidatePool]]:
    db.query(CandidatePool).filter(CandidatePool.trade_date == trade_date).delete(synchronize_session=False)
    db.query(DailyRecap).filter(DailyRecap.trade_date == trade_date).delete(synchronize_session=False)

    macro_score = _macro_sentiment(db)
    stocks = db.query(Stock).filter(Stock.market == TARGET_MARKET).order_by(Stock.symbol).all()
    candidates_payload = []
    movers = []

    for stock in stocks:
        rows = _latest_rows(db, stock.id, limit=30)
        if len(rows) < 2:
            continue
        latest = rows[0]
        prev = rows[1]
        pct_change = (latest.close - prev.close) / max(1e-6, prev.close)
        closes = [r.close for r in reversed(rows[:20])]
        volatility = pstdev(closes) / max(1e-6, mean(closes)) if len(closes) >= 5 else 0.0

        data_score = _clip(0.5 + pct_change * 2 - volatility)
        sentiment_score = _clip((_news_sentiment(db, stock.symbol, days_back=7) * 0.7) + (macro_score * 0.3))
        total_score = _clip(data_score * 0.55 + sentiment_score * 0.45)

        reasons = []
        if abs(pct_change) >= 0.02:
            reasons.append(f"abnormal_close_change:{pct_change:.2%}")
        if volatility >= 0.04:
            reasons.append(f"high_volatility:{volatility:.2%}")
        if sentiment_score >= 0.58:
            reasons.append("positive_sentiment")
        if data_score >= 0.58:
            reasons.append("positive_market_data")
        if not reasons:
            reasons.append("score_based_candidate")

        evidence = [
            {"metric": "pct_change", "value": round(pct_change, 6)},
            {"metric": "volatility_20d", "value": round(volatility, 6)},
            {"metric": "macro_score", "value": round(macro_score, 6)},
        ]

        movers.append({"symbol": stock.symbol, "pct_change": round(pct_change, 6), "close": latest.close})

        if abs(pct_change) >= 0.02 or total_score >= 0.6:
            candidates_payload.append(
                {
                    "stock_symbol": stock.symbol,
                    "sentiment_score": sentiment_score,
                    "data_score": data_score,
                    "total_score": total_score,
                    "reasons": reasons,
                    "evidence": evidence,
                }
            )

    candidates_payload.sort(key=lambda x: x["total_score"], reverse=True)
    selected = candidates_payload[: max(1, top_n)]

    candidates: list[CandidatePool] = []
    for item in selected:
        row = CandidatePool(
            trade_date=trade_date,
            stock_symbol=item["stock_symbol"],
            sentiment_score=round(item["sentiment_score"], 4),
            data_score=round(item["data_score"], 4),
            total_score=round(item["total_score"], 4),
            reasons=item["reasons"],
            evidence=item["evidence"],
        )
        db.add(row)
        candidates.append(row)

    movers.sort(key=lambda x: abs(x["pct_change"]), reverse=True)
    rising = sum(1 for m in movers if m["pct_change"] > 0)
    falling = sum(1 for m in movers if m["pct_change"] < 0)
    market_summary = (
        f"Post-close recap on {trade_date.isoformat()}: "
        f"{rising} rising, {falling} falling, {len(selected)} candidates selected."
    )
    macro_summary = (
        "Macro sentiment supportive."
        if macro_score >= 0.55
        else "Macro sentiment neutral."
        if macro_score >= 0.45
        else "Macro sentiment headwind."
    )
    recap = DailyRecap(
        trade_date=trade_date,
        market_summary=market_summary,
        macro_summary=macro_summary,
        top_movers=movers[:10],
    )
    db.add(recap)
    db.commit()
    db.refresh(recap)
    for row in candidates:
        db.refresh(row)
    return recap, candidates


def generate_pre_open_scan(db: Session, scan_date: date, top_n: int = 10) -> list[ScanResult]:
    db.query(ScanResult).filter(ScanResult.scan_date == scan_date).delete(synchronize_session=False)
    source_date = scan_date
    candidates = db.query(CandidatePool).filter(CandidatePool.trade_date == source_date).order_by(CandidatePool.total_score.desc()).all()
    if not candidates:
        latest_pool_date = db.query(CandidatePool.trade_date).order_by(CandidatePool.trade_date.desc()).first()
        if latest_pool_date:
            source_date = latest_pool_date[0]
            candidates = (
                db.query(CandidatePool)
                .filter(CandidatePool.trade_date == source_date)
                .order_by(CandidatePool.total_score.desc())
                .all()
            )

    ranked = []
    for item in candidates:
        fresh_news = _news_sentiment(db, item.stock_symbol, days_back=2)
        rescore = _clip(item.total_score * 0.7 + fresh_news * 0.3)
        alignment = "aligned" if abs(item.sentiment_score - item.data_score) <= 0.08 else "conflict"
        if rescore >= 0.62:
            action = "buy"
        elif rescore <= 0.4:
            action = "avoid"
        else:
            action = "watch"
        ranked.append(
            {
                "stock_symbol": item.stock_symbol,
                "score": round(rescore, 4),
                "action": action,
                "notes": {
                    "source_trade_date": source_date.isoformat(),
                    "alignment": alignment,
                    "sentiment_score": item.sentiment_score,
                    "data_score": item.data_score,
                    "base_score": item.total_score,
                    "fresh_news_score": round(fresh_news, 4),
                },
            }
        )
    ranked.sort(key=lambda x: x["score"], reverse=True)

    rows: list[ScanResult] = []
    for idx, item in enumerate(ranked[: max(1, top_n)], start=1):
        row = ScanResult(
            scan_date=scan_date,
            stock_symbol=item["stock_symbol"],
            rank=idx,
            score=item["score"],
            action=item["action"],
            notes=item["notes"],
        )
        db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows
