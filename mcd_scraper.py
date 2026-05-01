"""麦当劳 MCP 客户端（Streamable HTTP）"""

import json
import re

import httpx

_MCP_URL = "https://mcp.mcd.cn"
_PROTOCOL_VERSION = "2024-11-05"

_TOOL_COUPONS = "available-coupons"
_TOOL_STORES  = "query-nearby-stores"
_TOOL_MENU    = "query-meals"
_TOOL_DETAIL  = "query-meal-detail"


class MCDClient:
    def __init__(self, token: str, timeout: float = 15.0):
        self._token = token
        self._session_id: str | None = None
        self._req_id = 0
        self._session_ready = False
        self._tools: list[dict] | None = None
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    # ── 公开接口 ──────────────────────────────────────────────────

    async def get_coupons(self) -> list[dict]:
        """返回麦麦省可领取优惠券列表，每项含 title 和 status。"""
        await self._ensure_session()
        raw = await self._call_tool(_TOOL_COUPONS)
        return _parse_coupon_text(raw)

    async def find_stores(self, city: str, keyword: str | None = None) -> tuple[list[dict], str]:
        """查询城市附近门店。返回 (stores, raw_text)，解析失败时 stores 为空列表。"""
        await self._ensure_session()
        raw = await self._call_tool(_TOOL_STORES, {
            "searchType": 2,
            "beType": 1,
            "city": city,
            "keyword": keyword or city,
        })
        result = _extract_data(raw, list)
        return (result if result is not None else []), raw

    async def get_menu_raw(self, store_code: str, order_type: int = 1) -> str:
        """返回 query-meals 的原始响应文本，用于调试。"""
        await self._ensure_session()
        return await self._call_tool(_TOOL_MENU, {
            "storeCode": store_code,
            "orderType": order_type,
        })

    async def get_menu(self, store_code: str, order_type: int = 1) -> dict:
        """返回菜单 {categories: [...], meals: {code: {name, currentPrice}}}。"""
        raw = await self.get_menu_raw(store_code, order_type)
        return self.parse_menu(raw)

    @staticmethod
    def parse_menu(raw: str) -> dict:
        """从原始响应文本解析菜单数据，失败则抛出 RuntimeError。"""
        result = _extract_data(raw, dict)
        if not result:
            raise RuntimeError(f"菜单数据解析失败，原始响应：{raw}")
        return result

    async def get_meal_detail(self, code: str, store_code: str, order_type: int = 1) -> dict:
        """返回餐品详情 {code, price, rounds: [...]}。"""
        await self._ensure_session()
        raw = await self._call_tool(_TOOL_DETAIL, {
            "code": code,
            "storeCode": store_code,
            "orderType": order_type,
        })
        result = _extract_data(raw, dict)
        return result if result is not None else {}

    async def list_tools(self) -> list[str]:
        """列出所有可用工具名（调试用）。"""
        await self._ensure_session()
        if self._tools is None:
            result = await self._rpc("tools/list")
            self._tools = result.get("result", {}).get("tools", [])
        return [t.get("name", "") for t in (self._tools or [])]

    # ── MCP 协议 ──────────────────────────────────────────────────

    async def _ensure_session(self) -> None:
        if self._session_ready:
            return
        await self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "astrbot-plugin", "version": "1.0.0"},
        })
        await self._notify("notifications/initialized")
        self._session_ready = True

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
    """从 MCP 响应文本中提取嵌套的 data 字段，返回 expected_type 类型的值。

    遍历 _json_candidates 返回的每个 JSON 片段，优先匹配带 success/code/data
    的标准 API 响应包，其次再尝试其他结构。
    """
    for candidate in _json_candidates(raw):
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(obj, dict):
            if isinstance(obj, expected_type):
                return obj
            continue
        # 标准 API 响应包：{"success": true, "data": ...}
        if "data" in obj and "success" in obj:
            payload = obj["data"]
            if isinstance(payload, expected_type):
                return payload
        # 直接匹配：{"categories": [...], "meals": {...}} 等裸结构
        if isinstance(obj, expected_type):
            return obj
    return None


def _json_candidates(raw: str) -> list[str]:
    """从各种可能的格式中提取 JSON 候选字符串。"""
    candidates: list[str] = []

    # 1. JSON 代码块（```json / ``` 包裹）
    for m in re.finditer(r"```(?:json)?[\t ]*\n([\s\S]*?)```", raw):
        candidates.append(m.group(1).strip())

    # 2. 【{json}】 中文括号包裹（旧格式）
    m = re.search(r"【(\{.+\})】", raw, re.DOTALL)
    if m:
        candidates.append(m.group(1))

    # 3. 独立 JSON 行 —— 紧跟某个 Markdown 小节标题后的纯 JSON
    for m in re.finditer(
        r"#+\s*(?:Original\s*Response|Response\s*Structure|原始响应|响应数据|响应示例|响应内容)[\t ]*\n+([\s\S]+?)(?=\n#+ |\n```|\Z)",
        raw,
        re.IGNORECASE,
    ):
        candidates.append(m.group(1).strip())

    # 4. 所有以 { 或 [ 开头、以 } 或 ] 结尾的完整 JSON 行组
    for m in re.finditer(r"((?:^|\n)[\{\[][^\n\{\[\}\]\]\r]*[\}\]](?=\n|$))", raw):
        line = m.group(1).strip()
        if line:
            candidates.append(line)

    # 5. 从后往前找第一个以 { / [ 开头且匹配闭合的段落
    brace_count = 0
    start = -1
    for i in range(len(raw) - 1, -1, -1):
        ch = raw[i]
        if ch == "}":
            brace_count += 1
            if start == -1:
                start = i
        elif ch == "{":
            brace_count -= 1
            if brace_count == 0 and start != -1:
                candidates.append(raw[i : start + 1])
                start = -1
        elif ch == "]":
            brace_count += 1
            if start == -1:
                start = i
        elif ch == "[":
            brace_count -= 1
            if brace_count == 0 and start != -1:
                candidates.append(raw[i : start + 1])
                start = -1

    # 6. 原始文本兜底
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
