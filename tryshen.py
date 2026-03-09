from playwright.sync_api import sync_playwright
import requests
import json

API_URL = "http://webapi.cninfo.com.cn/api/stock/p_stock2102"

def bootstrap_headers():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        captured = {"accept_enckey": None, "cookie": None}

        def on_request(req):
            if "p_stock2102" in req.url or "/api/stock/" in req.url:
                headers = req.headers
                captured["accept_enckey"] = headers.get("accept-enckey")
                # 从 context 里统一取 cookie 更稳
        page.on("request", on_request)

        # 1) 打开平台登录页
        page.goto("https://webapi.cninfo.com.cn/")

        # 2) 第一次运行时手动登录一次
        # input("请在浏览器里完成登录，然后进入会触发目标 API 的页面，完成后按回车...")

        # 3) 取 cookie
        cookies = context.cookies()
        cookie_str = "; ".join([f'{c["name"]}={c["value"]}' for c in cookies])
        captured["cookie"] = cookie_str

        browser.close()

        if not captured["accept_enckey"]:
            raise RuntimeError("没有捕获到 Accept-Enckey，请确认页面确实触发了目标 API 请求")

        return {
            "Accept-Enckey": captured["accept_enckey"],
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://webapi.cninfo.com.cn/",
            "Cookie": captured["cookie"],
        }

def fetch_data(headers, scode="600519"):
    params = {"scode": scode, "format": "json"}
    r = requests.get(API_URL, params=params, headers=headers, timeout=20)
    print("HTTP:", r.status_code)
    print(r.text[:500])
    return r

if __name__ == "__main__":
    headers = bootstrap_headers()
    fetch_data(headers, "600518")