"""麦当劳 MCP 客户端

通过 Streamable HTTP 协议调用麦当劳官方 MCP Server 获取菜单。
MCP Server: https://mcp.mcd.cn
文档: https://open.mcd.cn/mcp/doc
"""

import json

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
        """初始化会话、自动寻找菜单 tool 并返回纯文本菜单。"""
        await self._initialize()
        tools = await self._list_tools()
        if not tools:
            raise RuntimeError("MCD MCP 未返回任何工具")
        menu_tool = next(
            (t for t in tools if "menu" in t.get("name", "").lower()),
            tools[0],
        )
        return await self._call_tool(menu_tool["name"])

    async def list_tool_names(self) -> list[str]:
        """列出所有可用工具名（调试用）。"""
        await self._initialize()
        tools = await self._list_tools()
        return [t.get("name", "") for t in tools]

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
