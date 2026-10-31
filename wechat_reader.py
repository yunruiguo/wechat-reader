"""WeChat-specific extraction. Page content is untrusted source material."""

import argparse
import asyncio
import json
import os
import platform
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

MAX_BYTES = 5 * 1024 * 1024
PROFILE = Path(os.environ.get("WECHAT_READER_PROFILE", str(Path.home() / ".wechat-reader" / "browser-profile"))).expanduser()
BLOCKS = {"p", "div", "section", "blockquote", "li", "h1", "h2", "h3", "h4", "tr"}


def chrome_executable() -> Path | None:
    """Locate installed Chrome without consulting the user's browser profile."""
    if platform.system() == "Darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                      Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    elif platform.system() == "Windows":
        candidates = [Path(os.environ[key]) / "Google/Chrome/Application/chrome.exe"
                      for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if key in os.environ]
    else:
        candidates = [Path("/opt/google/chrome/chrome"), Path("/usr/bin/google-chrome")]
    return next((path for path in candidates if path.is_file()), None)


def validate_url(url: str, *, article: bool = True) -> str:
    """Limit network access to HTTPS on the exact WeChat article host."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "mp.weixin.qq.com"
            or parsed.port not in (None, 443) or parsed.username or parsed.password):
        raise ValueError("只支持 https://mp.weixin.qq.com 的文章链接。")
    if article and not (parsed.path == "/s" or parsed.path.startswith("/s/")):
        raise ValueError("请提供微信公众号文章链接（/s 或 /s/…）。")
    return url


def _text(node: Tag) -> str:
    pieces = []

    def walk(item):
        if isinstance(item, Comment):
            return
        if isinstance(item, NavigableString):
            pieces.append(str(item))
        elif isinstance(item, Tag):
            if item.name in {"script", "style", "noscript", "template"}:
                return
            if item.name == "br" or item.name in BLOCKS:
                pieces.append("\n")
            for child in item.children:
                walk(child)
            if item.name in BLOCKS:
                pieces.append("\n")

    walk(node)
    lines = [re.sub(r"[\t\r\f\v \u00a0]+", " ", line).strip()
             for line in "".join(pieces).split("\n")]
    return "\n".join(line for line in lines if line)


def parse_article(html: str, url: str, *, offset: int = 0, max_chars: int = 20000) -> dict:
    validate_url(url)
    if len(html.encode("utf-8")) > MAX_BYTES:
        raise ValueError("页面超过 5 MiB，停止处理。")
    if offset < 0 or not 1 <= max_chars <= 100000:
        raise ValueError("offset 必须非负，max_chars 必须在 1 到 100000 之间。")
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("#js_content")
    text = _text(body) if body else ""

    def field(selector, attr=None):
        node = soup.select_one(selector)
        return (node.get(attr, "") if attr else node.get_text("", strip=True)) if node else ""

    images = []
    if body:
        for image in body.select("img"):
            src = image.get("data-src") or image.get("src")
            if src:
                src = urljoin(url, src)
                if urlsplit(src).scheme in {"http", "https"} and src not in images:
                    images.append(src)
    base = {"url": url, "content_is_untrusted": True}
    if not text and not images:
        visible = soup.get_text(" ", strip=True)
        if any(marker in visible for marker in ("环境异常", "完成验证", "去验证", "访问过于频繁")):
            return {**base, "status": "verification_required", "message":
                    "微信要求验证。请用 use_browser=true 打开窗口并手动完成验证，再读取。"}
        if any(marker in visible for marker in ("该内容已被发布者删除", "此内容因违规无法查看",
                                               "内容已删除", "该内容暂时无法查看", "此内容已被发布者删除")):
            return {**base, "status": "unavailable", "message": "微信提示文章已删除或无法查看。"}
        return {**base, "status": "unsupported_page", "message":
                "未找到微信文章正文。可能尚未加载、需要登录，或页面结构发生变化。"}

    title = field("#activity-name") or field('meta[property="og:title"]', "content")
    end = offset + max_chars
    return {**base, "status": "ok", "title": title,
            "author": field("#js_author_name") or field('meta[name="author"]', "content"),
            "account": field("#js_name"), "published_at": field("#publish_time"),
            "text": text[offset:end], "total_chars": len(text), "offset": offset,
            "next_offset": end if end < len(text) else None,
            "images": images, "images_require_vision": bool(images)}


async def fetch_html(url: str) -> str:
    validate_url(url)
    # Redirects are checked before each request, including captcha redirects.
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        current = url
        for _ in range(6):
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    current = validate_url(urljoin(current, response.headers["location"]), article=False)
                    continue
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", "text/html").lower():
                    raise ValueError("服务器没有返回 HTML 页面。")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError("页面超过 5 MiB，停止读取。")
                return bytes(data).decode("utf-8", errors="replace")
        raise ValueError("页面重定向次数过多。")


async def read_browser(url: str, wait_seconds: int, offset: int, max_chars: int) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        chrome = chrome_executable()
        context = await playwright.chromium.launch_persistent_context(
            str(PROFILE), headless=False, accept_downloads=False,
            **({"executable_path": str(chrome)} if chrome else {}))
        try:
            page = await context.new_page()

            async def guard_navigation(route):
                if route.request.is_navigation_request() and route.request.frame == page.main_frame:
                    try:
                        validate_url(route.request.url, article=False)
                    except ValueError:
                        await route.abort()
                        return
                await route.continue_()

            await page.route("**/*", guard_navigation)
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            deadline = asyncio.get_running_loop().time() + wait_seconds
            while True:
                validate_url(page.url, article=False)
                result = parse_article(await page.content(), url, offset=offset, max_chars=max_chars)
                if result["status"] in {"ok", "unavailable"}:
                    return result
                if asyncio.get_running_loop().time() >= deadline:
                    return result
                await asyncio.sleep(0.5)
        finally:
            await context.close()


async def read_article(url: str, *, use_browser: bool | None = None, wait_seconds: int = 20,
                       offset: int = 0, max_chars: int = 20000) -> dict:
    """Try HTTP, then a visible browser on verification; False forces HTTP only."""
    method = "browser" if use_browser else "http"
    attempted_methods = [method]
    try:
        validate_url(url)
        if not 0 <= wait_seconds <= 30 or offset < 0 or not 1 <= max_chars <= 100000:
            raise ValueError("wait_seconds: 0–30；offset: 非负；max_chars: 1–100000。")
        if use_browser:
            result = await read_browser(url, wait_seconds, offset, max_chars)
        else:
            result = parse_article(await fetch_html(url), url, offset=offset, max_chars=max_chars)
            if use_browser is None and result["status"] == "verification_required":
                method = "browser"
                attempted_methods.append(method)
                result = await read_browser(url, wait_seconds, offset, max_chars)
        if method == "browser" and result["status"] == "verification_required":
            result["message"] = "浏览器中仍需人工验证。请再次读取并在弹出的窗口中手动完成验证。"
        return {**result, "method": method, "attempted_methods": attempted_methods}
    except (ValueError, httpx.HTTPError) as error:
        return {"status": "error", "url": url, "message": str(error), "content_is_untrusted": True,
                "method": method, "attempted_methods": attempted_methods}
    except Exception as error:
        # Browser launch/close errors must become tool results, not kill the server.
        if method == "browser":
            return {"status": "browser_error", "url": url, "message": str(error),
                    "content_is_untrusted": True, "method": method,
                    "attempted_methods": attempted_methods}
        raise


def main():
    parser = argparse.ArgumentParser(description="读取微信公众号文章，输出 JSON")
    parser.add_argument("url")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--browser", dest="use_browser", action="store_const", const=True,
                       help="直接使用 Chrome（默认遇到验证时自动切换）")
    modes.add_argument("--http-only", dest="use_browser", action="store_const", const=False,
                       help="仅通过 HTTP 读取，不打开浏览器")
    parser.add_argument("--wait-seconds", type=int, default=20)
    parser.add_argument("--html", type=Path, help="读取已保存的文章 HTML")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-chars", type=int, default=20000)
    args = parser.parse_args()
    if args.html:
        try:
            if args.html.stat().st_size > MAX_BYTES:
                raise ValueError("HTML 文件超过 5 MiB。")
            result = parse_article(args.html.read_text(encoding="utf-8"), args.url,
                                   offset=args.offset, max_chars=args.max_chars)
            result["method"] = "saved_html"
        except (OSError, ValueError) as error:
            result = {"status": "error", "message": str(error)}
    else:
        result = asyncio.run(read_article(args.url, use_browser=args.use_browser,
                                          wait_seconds=args.wait_seconds,
                                          offset=args.offset, max_chars=args.max_chars))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
