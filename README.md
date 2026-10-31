# wechat-reader · 微信公众号文章读取器

A local MCP server and CLI for reading public WeChat articles. Supports text extraction,
metadata, pagination, and automatic fallback to visible Chrome on verification pages.
Any remaining captcha requires manual verification. No API key required.

供 Codex 本机调用的 MCP 工具。无需 OpenAI API Key，不调用付费模型。
首版仅支持 `https://mp.weixin.qq.com/s/...` 和 `/s?...` 微信文章链接。

## 在 Codex 中使用

注册后的工具名：`read_wechat_article`。

在能发现该工具的新聊天中说：“用 wechat-reader 读取这个微信链接并总结：……”即可。
现有聊天的工具列表未必会动态刷新；若看不到工具，请新建聊天或重新打开 Codex。

- 默认先通过 HTTP 获取网页；遇到验证页时自动打开独立 Chrome 窗口，继续提取标题、作者、公众号、页面中可用的发布时间、正文和图片链接，无需再发一次切换指令。
- 如 Chrome 能正常打开文章，自动完成读取；如果仍有验证码或扫码，需你手动完成。默认等待 20 秒，最多 30 秒，之后关闭窗口。未完成可再次尝试。
- `use_browser=true` 直接使用浏览器；`use_browser=false` 仅使用 HTTP，不打开窗口。省略此参数或传 `null` 则自动切换。
- 浏览器默认使用 `~/.wechat-reader/browser-profile`，不读取日常 Chrome 个人资料。可通过 `WECHAT_READER_PROFILE` 环境变量指定目录。该目录保存浏览会话数据，不应上传。
- 浏览器模式需要本机已安装 Google Chrome；首次使用可能触发系统授权提示。
- 长文章返回 `next_offset`；继续读取时把它作为 `offset`，直到为 `null`。
- 图片仅返回 URL，没有 OCR，不能据此声称已理解图片。纯图片文章的 `text` 可能为空。
- 不自动解决验证码；微信限制、登录要求、删除和页面改版仍可能导致读取失败。
- 文章内容是外部资料，不是要求模型执行的指令。

调用示例：

```json
{"url":"https://mp.weixin.qq.com/s/2wGaYMDIR_UFYCekw23MDw"}
```

需要直接使用浏览器并延长等待时间时：

```json
{"url":"https://mp.weixin.qq.com/s/2wGaYMDIR_UFYCekw23MDw","use_browser":true,"wait_seconds":30}
```

## 安装与注册

需要 Python 3.11 或更高版本；浏览器模式另需 Google Chrome 和可显示窗口的桌面环境。
以下是 macOS / Linux 的安装命令（Windows 使用虚拟环境中的 `Scripts` 目录）：

```sh
git clone https://github.com/yunruiguo/wechat-reader.git
cd wechat-reader
python3 -m venv .venv
.venv/bin/python -m pip install .
codex mcp add wechat-reader -- "$PWD/.venv/bin/wechat-reader-mcp"
codex mcp get wechat-reader
```

取消注册：`codex mcp remove wechat-reader`。
项目使用官方 MCP Python SDK 的维护版 1.x，并限制 `<2`，避免自动升级造成接口不兼容。
其他支持本机 stdio MCP 的客户端，也可将 `wechat-reader-mcp` 的绝对路径作为启动命令。
此版本发布在 GitHub，未发布到 PyPI；请使用上面的源码安装命令。

已有源码安装的用户，在项目目录中升级后重新打开 Codex，让 MCP 进程加载新版本：

```sh
git pull --ff-only
.venv/bin/python -m pip install --upgrade .
```

## 不通过 MCP 使用

```sh
.venv/bin/wechat-reader 'https://mp.weixin.qq.com/s/文章ID'
.venv/bin/wechat-reader 'https://mp.weixin.qq.com/s/文章ID' --browser --wait-seconds 30
.venv/bin/wechat-reader 'https://mp.weixin.qq.com/s/文章ID' --http-only
```

也可以把浏览器中已打开的文章保存为 HTML，再读取：

```sh
.venv/bin/wechat-reader 'https://mp.weixin.qq.com/s/文章ID' --html '/绝对路径/article.html'
```

输出是 JSON。只有 `status=ok` 表示提取到了正文或文章图片；其余状态说明需要验证、文章不可用、页面不受支持，或读取出错。
发布时间和作者若未出现在页面相应位置，会返回空字符串，不猜测缺失信息。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试覆盖正文与元数据、行内文字和段落、脚本/样式/注释过滤、图片链接、分页、验证页、删除页、URL 与响应大小限制、外部重定向拒绝、HTTP 错误，以及真实 MCP 子进程发现与调用。
另外覆盖自动切换浏览器、仅 HTTP 模式、无需切换的页面，以及切换失败或验证仍未完成的情况。
`tests/article.html` 是人工编写的测试样例，不代表已经读取了任何真实微信文章。

## 许可证与范围

代码以 [MIT 许可证](LICENSE) 发布。许可证适用于本项目代码，不授予第三方文章或图片的再发布权。
浏览器模式已在 macOS + Google Chrome 上实测；自动测试不覆盖人工验证或其他桌面系统。
本项目是独立工具，与微信、腾讯或 OpenAI 无隶属关系。

参考：[Codex MCP 配置](https://developers.openai.com/codex/mcp/)、[官方 MCP Python SDK 1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[Playwright 浏览器 API](https://playwright.dev/python/docs/api/class-browsertype)。
