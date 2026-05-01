"""麦当劳 MCP 客户端

通过 Streamable HTTP 协议调用麦当劳官方 MCP Server。
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
        self._tools: list[dict] | None = None
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self._client.aclose()

    # ── 公开接口 ─────────────────────────────────────────────────

    async def get_menu_text(self, store_code: str, order_type: int = 1) -> str:
        """获取可售餐品列表（纯文本）。"""
        tool = await self._find_tool(["sellable", "meal", "menu", "product", "item"])
        if not tool:
            raise RuntimeError(await self._no_tool_msg("菜单"))
        args = {"storeCode": store_code, "orderType": order_type}
        raw = await self._call_tool(tool["name"], args)
        return _parse_to_plain(raw)

    async def get_meal_detail_text(self, code: str, store_code: str, order_type: int = 1) -> str:
        """获取餐品详情（纯文本）。"""
        tool = await self._find_tool(["detail"])
        if not tool:
            raise RuntimeError(await self._no_tool_msg("餐品详情"))
        args = {"code": code, "storeCode": store_code, "orderType": order_type}
        raw = await self._call_tool(tool["name"], args)
        return _parse_to_plain(raw)

    async def get_coupons_text(self) -> str:
        """获取麦当劳优惠券列表（纯文本）。"""
        tool = await self._find_tool(["coupon", "offer", "省", "优惠"])
        if not tool:
            raise RuntimeError(await self._no_tool_msg("优惠券"))
        raw = await self._call_tool(tool["name"])
        return _parse_to_plain(raw)

    async def find_stores(self, city: str, keyword: str | None = None) -> list[dict]:
        """查询城市附近麦当劳门店列表。"""
        tool = await self._find_tool(["store", "restaurant", "nearby", "门店", "餐厅", "附近"])
        if not tool:
            raise RuntimeError(await self._no_tool_msg("门店"))
        args = {
            "searchType": 2,
            "beType": 1,
            "city": city,
            "keyword": keyword or city,
        }
        raw = await self._call_tool(tool["name"], args)
        return _parse_stores(raw)

    async def get_nearest_store_code(self, city: str) -> str:
        """返回城市附近第一家门店的 storeCode。"""
        stores = await self.find_stores(city)
        if not stores:
            raise RuntimeError(f"未找到 {city} 附近的麦当劳门店")
        return stores[0]["storeCode"]

    async def list_tool_names(self) -> list[str]:
        """列出所有可用工具名（调试用）。"""
        await self._ensure_initialized()
        return [t.get("name", "") for t in (self._tools or [])]

    # ── MCP 协议层 ───────────────────────────────────────────────

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

    async def _no_tool_msg(self, label: str) -> str:
        names = [t.get("name", "") for t in (self._tools or [])]
        return f"未找到{label}相关工具，可用工具：{names}"

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


# ── 响应解析 ─────────────────────────────────────────────────────

def _parse_to_plain(raw: str) -> str:
    """将 MCD MCP 响应转换为纯文本。"""
    # 从 【{json}】 中提取实际 JSON
    m = re.search(r"【(\{.+\})】", raw, re.DOTALL)
    if m:
        try:
            return _format_json_data(json.loads(m.group(1)))
        except (json.JSONDecodeError, KeyError):
            pass

    # Markdown 列表格式（优惠券等）
    return _parse_markdown_list(raw)


def _format_json_data(data: dict) -> str:
    payload = data.get("data")

    # ── 菜单列表：data.categories + data.meals ───────────────────
    if isinstance(payload, dict) and "categories" in payload and "meals" in payload:
        categories = payload["categories"]
        meals_dict: dict = payload["meals"]
        lines: list[str] = []
        for cat in categories:
            lines.append(f"\n【{cat.get('name', '')}】")
            for ref in cat.get("meals", []):
                code = ref.get("code", "")
                meal = meals_dict.get(code, {})
                name = meal.get("name", code)
                price = meal.get("currentPrice", "")
                tags = ref.get("tags", [])
                price_str = f"  ¥{price}" if price else ""
                tag_str = f"  [{', '.join(tags)}]" if tags else ""
                lines.append(f"  {name}{price_str}{tag_str}")
        return "\n".join(lines).strip()

    # ── 餐品详情：data.rounds ────────────────────────────────────
    if isinstance(payload, dict) and "rounds" in payload:
        name = payload.get("name", "")
        code = payload.get("code", "")
        price = payload.get("price", "")
        lines = [f"【{name}】  编码:{code}  ¥{price}"]
        for rnd in payload.get("rounds", []):
            rnd_name = rnd.get("name", "")
            min_q = rnd.get("minQuantity", 1)
            max_q = rnd.get("maxQuantity", 1)
            qty_str = f"必选{min_q}个" if min_q == max_q else f"选{min_q}~{max_q}个"
            lines.append(f"  ▸ {rnd_name}（{qty_str}）")
            for choice in rnd.get("choices", []):
                lines.append(f"      · {choice.get('name', '')}")
        return "\n".join(lines)

    # ── 积分商品列表：data 为数组且含 spuName ───────────────────
    if isinstance(payload, list) and payload and "spuName" in payload[0]:
        lines = ["【积分兑换商品】"]
        for item in payload:
            name = item.get("spuName", "")
            point = item.get("point", "")
            lines.append(f"  {name}  {point}积分")
        return "\n".join(lines)

    # ── 优惠券列表 ───────────────────────────────────────────────
    if isinstance(payload, list) and payload and (
        "couponTitle" in payload[0] or "title" in payload[0]
    ):
        lines = ["【优惠券列表】"]
        for item in payload:
            title = item.get("couponTitle") or item.get("title", "")
            status = item.get("status") or item.get("state", "")
            lines.append(f"  {title}  {status}".rstrip())
        return "\n".join(lines)

    return data.get("message") or "暂无数据"


def _parse_markdown_list(raw: str) -> str:
    lines: list[str] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.rstrip().rstrip("\\").strip()
        if re.match(r"^<[^>]+>$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line:
            continue
        if line.startswith("【") and line.endswith("】"):
            if current:
                lines.append(_fmt_kv(current))
                current = {}
            lines.append(f"\n{line}")
            continue
        m = re.match(r"^-\s*(.+?)：(.+)$", line)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            if current and key in ("优惠券标题", "名称", "商品名"):
                lines.append(_fmt_kv(current))
                current = {}
            current[key] = val
            continue
        m2 = re.match(r"^(.+?)：(.+)$", line)
        if m2 and current:
            current[m2.group(1).strip()] = m2.group(2).strip()
            continue
        heading = re.sub(r"^#+\s*", "", line)
        if heading and not current:
            lines.append(f"\n【{heading}】")
    if current:
        lines.append(_fmt_kv(current))
    result = "\n".join(lines).strip()
    return result or re.sub(r"<[^>]+>", "", raw).strip()


def _fmt_kv(item: dict[str, str]) -> str:
    name = item.get("优惠券标题") or item.get("名称") or item.get("商品名") or ""
    extra = item.get("状态") or item.get("价格") or item.get("售价") or ""
    return f"  {name}  {extra}".rstrip()


def _parse_stores(raw: str) -> list[dict]:
    """解析门店列表响应，返回 storeCode/storeName/address/distance 列表。"""
    m = re.search(r"【(\{.+\})】", raw, re.DOTALL)
    if m:
        try:
            payload = json.loads(m.group(1)).get("data")
            if isinstance(payload, list):
                return payload
        except (json.JSONDecodeError, KeyError):
            pass
    try:
        payload = json.loads(raw).get("data")
        if isinstance(payload, list):
            return payload
    except (json.JSONDecodeError, TypeError):
        pass
    return []


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
