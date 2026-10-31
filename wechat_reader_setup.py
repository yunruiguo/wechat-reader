"""Environment checks and a backed-up, narrowly scoped Codex configuration update."""

import argparse
import asyncio
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

import tomlkit

from wechat_reader import chrome_executable


def config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser() / "config.toml"


def browser_path() -> Path | None:
    chrome = chrome_executable()
    if chrome:
        return chrome
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        chromium = Path(playwright.chromium.executable_path)
    return chromium if chromium.is_file() else None


def configure_codex(path: Path, python: Path) -> Path | None:
    """Preserve comments, other servers and existing per-tool policy. Return backup path."""
    original = path.read_bytes() if path.exists() else b""
    document = tomlkit.parse(original.decode("utf-8"))
    servers = document.setdefault("mcp_servers", tomlkit.table())
    server = servers.setdefault("wechat-reader", tomlkit.table())
    if "url" in server:
        raise ValueError("已有同名远程服务 wechat-reader，请先重命名它，再安装。")
    server["command"] = str(python.absolute())
    server["args"] = ["-m", "wechat_reader_server"]
    server["enabled"] = True
    updated = tomlkit.dumps(document).encode("utf-8")
    if updated == original:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        with tempfile.NamedTemporaryFile(prefix="config.toml.backup-", dir=path.parent, delete=False) as stream:
            stream.write(original)
            backup = Path(stream.name)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".wechat-reader-config-", dir=path.parent, delete=False) as stream:
            stream.write(updated)
            temporary = Path(stream.name)
        if path.exists():
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        # Do not overwrite a concurrent change from Codex or another installer.
        current = path.read_bytes() if path.exists() else b""
        if current != original:
            raise RuntimeError("Codex 配置在安装期间发生变化，请关闭 Codex 后重试。")
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return backup


async def verify_mcp() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=["-m", "wechat_reader_server"])
    async with asyncio.timeout(30):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                if "read_wechat_article" not in {tool.name for tool in tools.tools}:
                    raise RuntimeError("读取工具未正确加载。")


def verify_browser(browser: Path) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(executable_path=str(browser), headless=True)
        try:
            page = instance.new_page()
            page.set_content("<title>wechat-reader setup check</title>")
            if page.title() != "wechat-reader setup check":
                raise RuntimeError("浏览器自检失败。")
        finally:
            instance.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="检查运行环境，并自动配置 Codex")
    parser.add_argument("--check", action="store_true", help="仅检查，不安装浏览器或修改配置")
    args = parser.parse_args()
    try:
        print("[1/4] 检查 Python…", flush=True)
        if sys.version_info < (3, 11):
            raise RuntimeError("需要 Python 3.11 以上版本，请重新运行一键安装器。")
        print(f"      Python {sys.version.split()[0]}：通过", flush=True)
        print("[2/4] 检查浏览器…", flush=True)
        browser = browser_path()
        if not browser and not args.check:
            print("      未发现 Chrome，正在下载工具专用 Chromium…", flush=True)
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
            browser = browser_path()
        if not browser:
            raise RuntimeError("没有可用浏览器，请重新运行一键安装器。")
        verify_browser(browser)
        print("      浏览器：通过", flush=True)
        print("[3/4] 检查 Codex 工具连接…", flush=True)
        asyncio.run(verify_mcp())
        print("      MCP 工具：通过", flush=True)
        path = config_path()
        print("[4/4] 检查 Codex 配置…", flush=True)
        if args.check:
            document = tomlkit.parse(path.read_text(encoding="utf-8")) if path.exists() else {}
            server = document.get("mcp_servers", {}).get("wechat-reader", {})
            if (server.get("command") != str(Path(sys.executable).absolute())
                    or server.get("args") != ["-m", "wechat_reader_server"]
                    or server.get("enabled", True) is not True):
                raise RuntimeError("当前环境尚未注册到 Codex，请运行一键安装器。")
        else:
            backup = configure_codex(path, Path(sys.executable))
            if backup:
                print(f"      原配置备份：{backup}", flush=True)
        print(f"      配置：{path}", flush=True)
        print("\n安装就绪。请重新打开 Codex，新建聊天，输入：\n用 wechat-reader 读取这个微信链接并总结：你的链接", flush=True)
        if not shutil.which("codex"):
            print("提示：若尚未安装 Codex，请先安装并登录桌面应用。", flush=True)
        return 0
    except Exception as error:
        print(f"\n[未完成] {error}\n可修复后重新运行；其他 Codex 配置不会被清空。", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
