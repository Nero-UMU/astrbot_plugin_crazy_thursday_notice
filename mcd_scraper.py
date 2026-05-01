"""麦当劳 MCP 客户端

通过 Streamable HTTP 协议调用麦当劳官方 MCP Server 获取菜单。
MCP Server: https://mcp.mcd.cn
文档: https://open.mcd.cn/mcp/doc
"""

import json
import re

import httpx

_MCP_URL = "https://mcp.mcd.cn"
_PROTOCOL_VERSION = "2024-11-05"


class MCDMenuFetcher:
    def __init__(self, token: str, timeout: float = 15.0):
        self._token = token
        self._session_id: str | None = None
        self._req_id = 0
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    # ── 公开接口 ─────────────────────────────────────────────────

    async def get_menu_text(self) -> str:
        """获取麦当劳菜单（纯文本）。"""
        raw = await self._invoke_by_keywords(["menu", "菜单", "product", "item"], "菜单")
        return _parse_to_plain(raw)

    async def get_coupons_text(self) -> str:
        """获取麦当劳优惠券列表（纯文本）。"""
        raw = await self._invoke_by_keywords(["coupon", "优惠", "offer", "省"], "优惠券")
        return _parse_to_plain(raw)

    async def list_tool_names(self) -> list[str]:
        """列出所有可用工具名（调试用）。"""
        await self._initialize()
        tools = await self._list_tools()
        return [t.get("name", "") for t in tools]

    async def _invoke_by_keywords(self, keywords: list[str], label: str) -> str:
        """按关键词优先级查找 tool 并调用，找不到则用第一个 tool。"""
        await self._initialize()
        tools = await self._list_tools()
        if not tools:
            raise RuntimeError("MCD MCP 未返回任何工具")
        tool = next(
            (t for kw in keywords for t in tools if kw in t.get("name", "").lower()),
            None,
        )
        if tool is None:
            raise RuntimeError(f"未找到{label}相关工具，可用工具：{[t['name'] for t in tools]}")
        return await self._call_tool(tool["name"])

    # ── MCP 协议层 ───────────────────────────────────────────────

    async def _initialize(self) -> None:
        await self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "astrbot-plugin", "version": "1.0.0"},
        })
        await self._notify("notifications/initialized")

    async def _list_tools(self) -> list[dict]:
        result = await self._rpc("tools/list")
        return result.get("result", {}).get("tools", [])

    async def _call_tool(self, name: str, arguments: dict | None = None) -> str:
        result = await self._rpc("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        content = result.get("result", {}).get("content", [])
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")

    # ── HTTP 层 ──────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {},
        }
        resp = await self._client.post(_MCP_URL, headers=self._headers(), json=body)
        resp.raise_for_status()
        if sid := resp.headers.get("mcp-session-id"):
            self._session_id = sid
        return _parse_mcp_response(resp)

    async def _notify(self, method: str, params: dict | None = None) -> None:
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        resp = await self._client.post(_MCP_URL, headers=self._headers(), json=body)
        resp.raise_for_status()


def _parse_to_plain(raw: str) -> str:
    """将 MCD MCP 返回的 Markdown 响应提取为纯文本。

    支持两种常见格式：
    1. 优惠券列表（含 优惠券标题/状态 字段）
    2. 菜单列表（含 名称/价格 等字段）
    对于无法识别的内容，直接去除 HTML 标签后返回。
    """
    # 尝试提取结构化列表项（- key：value 格式）
    lines: list[str] = []
    current: dict[str, str] = {}

    for line in raw.splitlines():
        # 去除行尾的 Markdown 换行符 \
        line = line.rstrip().rstrip("\\").strip()
        # 跳过 HTML 标签行
        if re.match(r"<[^>]+>", line):
            continue
        # 列表项起始
        m = re.match(r"^-\s*(.+?)：(.+)$", line)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            if current and key in ("优惠券标题", "名称", "商品名"):
                lines.append(_format_item(current))
                current = {}
            current[key] = val
            continue
        # 缩进续行（key：value）
        m2 = re.match(r"^(.+?)：(.+)$", line)
        if m2 and current:
            current[m2.group(1).strip()] = m2.group(2).strip()
            continue
        # 普通标题行（如 ### 麦麦省优惠券列表）
        heading = re.sub(r"^#+\s*", "", line)
        if heading and not current:
            lines.append(f"\n【{heading}】")

    if current:
        lines.append(_format_item(current))

    result = "\n".join(lines).strip()
    # 兜底：如果没解析出任何内容，直接去除 HTML 后返回
    if not result:
        result = re.sub(r"<[^>]+>", "", raw).strip()
    return result


def _format_item(item: dict[str, str]) -> str:
    name = item.get("优惠券标题") or item.get("名称") or item.get("商品名") or ""
    status = item.get("状态") or ""
    price = item.get("价格") or item.get("售价") or ""
    extra = status or price
    return f"  {name}  {extra}".rstrip()


def _parse_mcp_response(resp: httpx.Response) -> dict:
    """兼容 application/json 和 text/event-stream 两种响应格式。"""
    ct = resp.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    return json.loads(data)
        return {}
    return resp.json()
