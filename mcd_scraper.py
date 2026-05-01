"""麦当劳 MCP 客户端（Streamable HTTP）"""

import json
import re

import httpx

_MCP_URL = "https://mcp.mcd.cn"
_PROTOCOL_VERSION = "2024-11-05"


class MCDClient:
    def __init__(self, token: str, timeout: float = 15.0):
        self._token = token
        self._session_id: str | None = None
        self._req_id = 0
        self._tools: list[dict] | None = None
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    # ── 公开接口 ──────────────────────────────────────────────────

    async def get_coupons(self) -> list[dict]:
        """返回麦麦省优惠券列表，每项含 title 和 status。"""
        tool = await self._find_tool(["coupon", "省", "优惠"])
        if not tool:
            raise RuntimeError(f"未找到优惠券工具，可用工具：{await self.list_tools()}")
        raw = await self._call_tool(tool["name"])
        return _parse_coupon_text(raw)

    async def find_stores(self, city: str, keyword: str | None = None) -> tuple[list[dict], str]:
        """查询城市附近门店。返回 (stores, raw_text)，stores 解析失败时为空列表。"""
        tool = await self._find_tool(["store", "restaurant", "门店", "附近", "餐厅"])
        if not tool:
            raise RuntimeError(f"未找到门店查询工具，可用工具：{await self.list_tools()}")
        raw = await self._call_tool(tool["name"], {
            "searchType": 2,
            "beType": 1,
            "city": city,
            "keyword": keyword or city,
        })
        result = _extract_data(raw, list)
        return (result if result is not None else []), raw

    async def get_menu(self, store_code: str, order_type: int = 1) -> dict:
        """返回菜单 {categories: [...], meals: {code: {name, currentPrice}}}。"""
        tool = await self._find_tool(["sellable", "menu", "meal", "餐品", "可售"])
        if not tool:
            raise RuntimeError(f"未找到菜单工具，可用工具：{await self.list_tools()}")
        raw = await self._call_tool(tool["name"], {
            "storeCode": store_code,
            "orderType": order_type,
        })
        result = _extract_data(raw, dict)
        if not result:
            raise RuntimeError(f"菜单数据解析失败，原始响应：{raw[:300]}")
        return result

    async def get_meal_detail(self, code: str, store_code: str, order_type: int = 1) -> dict:
        """返回餐品详情 {code, price, rounds: [{name, minQuantity, maxQuantity, choices}]}。"""
        tool = await self._find_tool(["detail", "详情"])
        if not tool:
            raise RuntimeError(f"未找到餐品详情工具，可用工具：{await self.list_tools()}")
        raw = await self._call_tool(tool["name"], {
            "code": code,
            "storeCode": store_code,
            "orderType": order_type,
        })
        result = _extract_data(raw, dict)
        return result if result is not None else {}

    async def list_tools(self) -> list[str]:
        await self._ensure_initialized()
        return [t.get("name", "") for t in (self._tools or [])]

    # ── MCP 协议 ──────────────────────────────────────────────────

    async def _ensure_initialized(self) -> None:
        if self._tools is not None:
            return
        await self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "astrbot-plugin", "version": "1.0.0"},
        })
        await self._notify("notifications/initialized")
        result = await self._rpc("tools/list")
        self._tools = result.get("result", {}).get("tools", [])

    async def _find_tool(self, keywords: list[str]) -> dict | None:
        await self._ensure_initialized()
        return next(
            (t for kw in keywords for t in (self._tools or []) if kw in t.get("name", "").lower()),
            None,
        )

    async def _call_tool(self, name: str, arguments: dict | None = None) -> str:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        content = result.get("result", {}).get("content", [])
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")

    # ── HTTP 层 ───────────────────────────────────────────────────

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


# ── 响应解析 ──────────────────────────────────────────────────────

def _parse_mcp_response(resp: httpx.Response) -> dict:
    ct = resp.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
                if data and data != "[DONE]":
                    return json.loads(data)
        return {}
    return resp.json()


def _extract_data(raw: str, expected_type: type):
    """从 MCP 响应文本中提取 data 字段，返回 expected_type 类型的值，否则返回 None。"""
    for candidate in _json_candidates(raw):
        try:
            obj = json.loads(candidate)
            payload = obj.get("data") if isinstance(obj, dict) else None
            if isinstance(payload, expected_type):
                return payload
        except (json.JSONDecodeError, AttributeError):
            pass
    return None


def _json_candidates(raw: str) -> list[str]:
    """按优先级返回候选 JSON 字符串列表。"""
    candidates = []
    # 优先从 【{...}】 中提取
    m = re.search(r"【(\{.+\})】", raw, re.DOTALL)
    if m:
        candidates.append(m.group(1))
    # 再尝试整段文本
    candidates.append(raw.strip())
    return candidates


def _parse_coupon_text(raw: str) -> list[dict]:
    """解析麦麦省优惠券 Markdown 响应为 [{title, status}] 列表。"""
    coupons: list[dict] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        line = re.sub(r"<[^>]+>", "", line).rstrip("\\").strip()
        if not line:
            continue
        m = re.match(r"^-?\s*优惠券标题[：:](.+)$", line)
        if m:
            if current.get("title"):
                coupons.append(current)
            current = {"title": m.group(1).strip(), "status": ""}
            continue
        m = re.match(r"^状态[：:](.+)$", line)
        if m and current:
            current["status"] = m.group(1).strip()
    if current.get("title"):
        coupons.append(current)
    return coupons
