import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.sentiment.guba_crawler import fetch_guba_posts
from app.sentiment.inference import DualModelSentimentScorer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate guba sentiment model scoring")
    parser.add_argument("--symbol", required=True, help="stock symbol, e.g. 000056")
    parser.add_argument("--date", required=True, help="target date, format YYYY-MM-DD")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-items", type=int, default=50)
    parser.add_argument("--show", type=int, default=15)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    posts = fetch_guba_posts(
        args.symbol,
        target_date=target_date,
        max_pages=args.max_pages,
        max_items=args.max_items,
    )
    if not posts:
        print("No guba posts fetched for scoring.")
        return

    scorer = DualModelSentimentScorer()
    try:
        scored = scorer.score_guba(posts)
    except Exception as exc:
        print(f"model scoring failed: {exc}")
        return
    print(f"symbol={args.symbol} date={target_date} scored={len(scored)}")
    for row in scored[: max(1, args.show)]:
        print(
            json.dumps(
                {
                    "published_at": row.published_at.isoformat() if row.published_at else None,
                    "title": row.title,
                    "text": row.text,
                    "label": row.label,
                    "score_raw": round(row.score_raw, 4),
                    "score_norm": round(row.score_norm, 4),
                    "positive_prob": round(row.positive_prob, 4),
                    "neutral_prob": round(row.neutral_prob, 4),
                    "negative_prob": round(row.negative_prob, 4),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
