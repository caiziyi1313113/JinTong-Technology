import argparse
import os


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify zai-sdk and optional Zhipu API call")
    parser.add_argument("--api-key", default=os.getenv("ZHIPU_API_KEY", ""))
    parser.add_argument("--model", default=os.getenv("ZHIPU_MODEL", "glm-4.7-flash"))
    parser.add_argument("--prompt", default="请返回JSON：{\"ok\": true}")
    parser.add_argument("--no-thinking", action="store_true")
    return parser.parse_args()


def main() -> None:
    import zai
    from zai import ZhipuAiClient

    args = parse_args()
    print("zai-sdk version:", zai.__version__)

    if not args.api_key:
        print("No API key provided. SDK import check passed.")
        return

    client = ZhipuAiClient(api_key=args.api_key)
    kwargs = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "temperature": 0.2,
        "max_tokens": 1024,
    }
    if not args.no_thinking:
        kwargs["thinking"] = {"type": "enabled"}

    response = client.chat.completions.create(**kwargs)
    print("response:", response.choices[0].message)


if __name__ == "__main__":
    main()
