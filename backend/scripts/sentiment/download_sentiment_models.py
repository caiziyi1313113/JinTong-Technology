import argparse
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download sentiment models to local directories for offline/local inference."
    )
    parser.add_argument(
        "--base-dir",
        default="backend/models/sentiment",
        help="Base directory for local model folders.",
    )
    parser.add_argument(
        "--hf-endpoint",
        default="",
        help="Optional HuggingFace endpoint, e.g. https://hf-mirror.com",
    )
    parser.add_argument(
        "--token",
        default="",
        help="Optional HF token for private/gated repos.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if files already exist.",
    )
    return parser.parse_args()


def _download_repo(
    *,
    repo_id: str,
    local_dir: Path,
    endpoint: str | None,
    token: str | None,
    force: bool,
) -> None:
    from huggingface_hub import snapshot_download

    local_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(local_dir),
        endpoint=endpoint,
        token=token,
        force_download=force,
        resume_download=not force,
    )


def main() -> None:
    args = parse_args()
    base_dir = Path(args.base_dir).resolve()
    endpoint = args.hf_endpoint.strip() or None
    token = args.token.strip() or None

    targets = [
        (
            "guba_model",
            "Fearao/RoBERTa_based_on_eastmoney_guba_comments",
            base_dir / "guba_model",
        ),
        (
            "guba_tokenizer",
            "uer/roberta-base-finetuned-chinanews-chinese",
            base_dir / "guba_tokenizer",
        ),
        (
            "news_model",
            "yiyanghkust/finbert-tone-chinese",
            base_dir / "news_model",
        ),
    ]

    print(f"base_dir={base_dir}")
    if endpoint:
        print(f"hf_endpoint={endpoint}")

    for alias, repo_id, local_dir in targets:
        print(f"[download] {alias}: {repo_id} -> {local_dir}")
        _download_repo(
            repo_id=repo_id,
            local_dir=local_dir,
            endpoint=endpoint,
            token=token,
            force=args.force,
        )

    print("")
    print("Done. Set these values in backend/app/core/config.py:")
    print(f'LOCAL_SENTIMENT_GUBA_MODEL = "{(base_dir / "guba_model").as_posix()}"')
    print(f'LOCAL_SENTIMENT_GUBA_TOKENIZER = "{(base_dir / "guba_tokenizer").as_posix()}"')
    print(f'LOCAL_SENTIMENT_NEWS_MODEL = "{(base_dir / "news_model").as_posix()}"')
    print(f'LOCAL_HF_CACHE_DIR = "{(base_dir / ".hf_cache").as_posix()}"')
    print("")
    print("Then run your test script again.")


if __name__ == "__main__":
    main()
