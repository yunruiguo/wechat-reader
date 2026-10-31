import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import wechat_reader as reader

URL = "https://mp.weixin.qq.com/s/example"
HTML = Path(__file__).with_name("article.html").read_text()


class ExtractionTests(unittest.TestCase):
    def test_article_metadata_body_images_and_noise(self):
        result = reader.parse_article(HTML, URL)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["title"], "示例文章")
        self.assertEqual(result["author"], "示例作者")
        self.assertEqual(result["account"], "示例公众号")
        self.assertEqual(result["published_at"], "2026年10月31日")
        self.assertEqual(result["text"], "第一段加粗文字。\n第二段 & 内容\n换行。")
        self.assertEqual(result["images"], ["https://mmbiz.qpic.cn/example.jpg"])
        self.assertTrue(result["content_is_untrusted"])

    def test_verification_is_never_article(self):
        result = reader.parse_article("<h1>环境异常</h1><p>当前环境异常，完成验证后即可继续访问。</p>", URL)
        self.assertEqual(result["status"], "verification_required")
        self.assertNotIn("text", result)

    def test_deleted_and_unknown(self):
        self.assertEqual(reader.parse_article("此内容已被发布者删除", URL)["status"], "unavailable")
        self.assertEqual(reader.parse_article("登录页面", URL)["status"], "unsupported_page")

    def test_pagination_reassembles_exact_text(self):
        full = reader.parse_article(HTML, URL)["text"]
        parts, offset = [], 0
        while offset is not None:
            result = reader.parse_article(HTML, URL, offset=offset, max_chars=5)
            parts.append(result["text"])
            offset = result["next_offset"]
        self.assertEqual("".join(parts), full)

    def test_image_only_article_reports_no_ocr(self):
        result = reader.parse_article('<div id="js_content"><img data-src="//mmbiz.qpic.cn/a.png"></div>', URL)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["text"], "")
        self.assertTrue(result["images_require_vision"])

    def test_metadata_fallback(self):
        result = reader.parse_article('<meta property="og:title" content="标题"><meta name="author" content="作者"><div id="js_content">正文</div>', URL)
        self.assertEqual((result["title"], result["author"]), ("标题", "作者"))

    def test_invalid_urls_and_limits(self):
        for url in ["file:///etc/passwd", "http://mp.weixin.qq.com/s/a",
                    "https://mp.weixin.qq.com.evil.test/s/a", "https://127.0.0.1/s/a",
                    "https://user@mp.weixin.qq.com/s/a", "https://mp.weixin.qq.com:9000/s/a",
                    "https://mp.weixin.qq.com/other"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                reader.parse_article(HTML, url)
        for options in ({"offset": -1}, {"max_chars": 0}, {"max_chars": 100001}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                reader.parse_article(HTML, URL, **options)


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def fetch_with(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.object(reader.httpx, "AsyncClient", return_value=client):
            return await reader.fetch_html(URL)

    async def test_external_redirect_is_rejected_before_request(self):
        requested = []

        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

        with self.assertRaises(ValueError):
            await self.fetch_with(handler)
        self.assertEqual(requested, [URL])

    async def test_same_host_captcha_redirect(self):
        def handler(request):
            if request.url.path.startswith("/s/"):
                return httpx.Response(302, headers={"location": "/mp/verify"})
            return httpx.Response(200, text="环境异常，完成验证", headers={"content-type": "text/html"})

        html = await self.fetch_with(handler)
        self.assertEqual(reader.parse_article(html, URL)["status"], "verification_required")

    async def test_response_size_limit(self):
        with patch.object(reader, "MAX_BYTES", 10), self.assertRaises(ValueError):
            await self.fetch_with(lambda request: httpx.Response(200, text="x" * 11))

    async def test_http_failure_becomes_tool_result(self):
        with patch.object(reader, "fetch_html", side_effect=httpx.ConnectError("failed")):
            result = await reader.read_article(URL)
        self.assertEqual(result["status"], "error")

    async def test_invalid_browser_arguments_do_not_launch(self):
        result = await reader.read_article(URL, use_browser=True, wait_seconds=100)
        self.assertEqual(result["status"], "error")


class AutomaticFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_verification_automatically_falls_back_with_same_options(self):
        expected = reader.parse_article(HTML, URL, offset=5, max_chars=8)
        with patch.object(reader, "fetch_html", return_value="环境异常，完成验证"), \
                patch.object(reader, "read_browser", return_value=expected) as browser:
            result = await reader.read_article(URL, offset=5, max_chars=8)
        browser.assert_awaited_once_with(URL, 20, 5, 8)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["text"], expected["text"])
        self.assertEqual(result["attempted_methods"], ["http", "browser"])

    async def test_explicit_http_only_never_opens_browser(self):
        with patch.object(reader, "fetch_html", return_value="环境异常，完成验证"), \
                patch.object(reader, "read_browser") as browser:
            result = await reader.read_article(URL, use_browser=False)
        browser.assert_not_awaited()
        self.assertEqual(result["status"], "verification_required")
        self.assertEqual(result["attempted_methods"], ["http"])

    async def test_final_http_results_do_not_trigger_browser(self):
        for html, status in [(HTML, "ok"), ("此内容已被发布者删除", "unavailable"),
                             ("未知页面", "unsupported_page")]:
            with self.subTest(status=status), \
                    patch.object(reader, "fetch_html", return_value=html), \
                    patch.object(reader, "read_browser") as browser:
                result = await reader.read_article(URL)
                browser.assert_not_awaited()
                self.assertEqual(result["status"], status)

    async def test_explicit_browser_skips_http(self):
        with patch.object(reader, "fetch_html") as fetch, \
                patch.object(reader, "read_browser", return_value=reader.parse_article(HTML, URL)):
            result = await reader.read_article(URL, use_browser=True)
        fetch.assert_not_awaited()
        self.assertEqual(result["attempted_methods"], ["browser"])

    async def test_remaining_verification_is_reported_without_retry_loop(self):
        pending = reader.parse_article("环境异常，完成验证", URL)
        with patch.object(reader, "fetch_html", return_value="环境异常，完成验证"), \
                patch.object(reader, "read_browser", return_value=pending) as browser:
            result = await reader.read_article(URL)
        browser.assert_awaited_once()
        self.assertEqual(result["status"], "verification_required")
        self.assertIn("仍需人工验证", result["message"])
        self.assertNotIn("text", result)

    async def test_automatic_browser_failure_is_a_tool_result(self):
        with patch.object(reader, "fetch_html", return_value="环境异常，完成验证"), \
                patch.object(reader, "read_browser", side_effect=RuntimeError("Chrome not installed")):
            result = await reader.read_article(URL)
        self.assertEqual(result["status"], "browser_error")
        self.assertEqual(result["attempted_methods"], ["http", "browser"])


class CLITests(unittest.TestCase):
    def test_cli_default_and_explicit_modes(self):
        for flags, mode in [([], None), (["--browser"], True), (["--http-only"], False)]:
            with self.subTest(flags=flags), \
                    patch.object(sys, "argv", ["wechat-reader", URL, *flags]), \
                    patch.object(reader, "read_article", new_callable=AsyncMock,
                                 return_value={"status": "ok"}) as read, \
                    redirect_stdout(StringIO()):
                self.assertEqual(reader.main(), 0)
                self.assertIs(read.await_args.kwargs["use_browser"], mode)


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_discovery_and_real_tool_call(self):
        await self.check_tool(["-m", "wechat_reader_server"])

    async def test_legacy_server_entry_point(self):
        await self.check_tool([str(Path(reader.__file__).with_name("server.py"))])

    async def check_tool(self, args):
        params = StdioServerParameters(command=sys.executable,
                                      args=args)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                self.assertEqual([tool.name for tool in tools.tools], ["read_wechat_article"])
                self.assertIsNone(tools.tools[0].inputSchema["properties"]["use_browser"]["default"])
                result = await session.call_tool("read_wechat_article", {"url": "file:///etc/passwd"})
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["status"], "error")


if __name__ == "__main__":
    unittest.main()
