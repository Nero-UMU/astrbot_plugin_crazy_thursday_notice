import json
from datetime import datetime
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain

from .kfc_scraper import KFCMenuFetcher
from .mcd_scraper import MCDClient

_PLUGIN_DATA_DIR = Path(__file__).parent.parent.parent / "plugin_data" / "astrbot_plugin_crazy_thursday_notice"
_MENU_CACHE_FILE = _PLUGIN_DATA_DIR / "menu_cache.json"


def _load_menu_cache() -> dict | None:
    try:
        return json.loads(_MENU_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_menu_cache(store_code: str, store_name: str, order_type: int, menu: dict) -> None:
    _PLUGIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache = {
        "store_code": store_code,
        "store_name": store_name,
        "order_type": order_type,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "categories": menu.get("categories", []),
        "meals": menu.get("meals", {}),
    }
    _MENU_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _format_menu(store_code: str, store_name: str, order_type: int, menu: dict) -> str:
    order_label = "到店取餐" if order_type == 1 else "外送"
    header = f"📋 {store_name or store_code} 菜单（{order_label}）："
    lines = [header]
    categories = menu.get("categories", [])
    meals_dict: dict = menu.get("meals", {})
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
            lines.append(f"  {name} [{code}]{price_str}{tag_str}")
    lines.append("\n菜单已缓存，可用 /mcdfood <名称或编号> 查询餐品详情。")
    return "\n".join(lines).strip()


def _format_detail(detail: dict) -> str:
    lines: list[str] = []
    for rnd in detail.get("rounds", []):
        name = rnd.get("name", "")
        min_q = rnd.get("minQuantity", 1)
        max_q = rnd.get("maxQuantity", 1)
        qty = f"必选{min_q}个" if min_q == max_q else f"选{min_q}~{max_q}个"
        lines.append(f"  ▸ {name}（{qty}）")
        for choice in rnd.get("choices", []):
            lines.append(f"      · {choice.get('name', '')}")
    return "\n".join(lines)


def _cmd_arg(message_str: str) -> str | None:
    """从消息文本中提取命令后的参数（去除指令本身），无参数返回 None。"""
    parts = message_str.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else None


@register("astrbot_plugin_crazy_thursday_notice", "NeroUMU", "每到周四自动向 QQ 群推送疯狂星期四提醒及菜单", "1.0.0")
class CrazyThursdayPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context: Context = context
        self.config: dict = config or {}
        self._cron_job = None

    async def initialize(self):
        self.group_ids: list[str] = self.config.get("group_ids", [])
        self.push_days: list[str] = self.config.get("push_days", ["周四"])
        self.push_times: list[str] = self.config.get("push_times", ["12:00"])
        self.message_text: str = self.config.get("message", "今天是肯德基疯狂星期四！V我50！")
        self.platform_id: str = self._resolve_platform_id(self.config.get("platform_id", ""))
        self.city: str = self.config.get("city", "上海市")
        self.mcd_token: str = self.config.get("mcd_token", "")
        self.mcd_store_code: str = self.config.get("mcd_store_code", "")
        self.mcd_order_type: int = int(self.config.get("mcd_order_type", "1") or 1)

        if not self.group_ids:
            logger.warning("[疯狂星期四] 未配置群号，定时推送不会执行。请在插件配置中填写 group_ids。")
            return

        _day_map = {"周日": "0", "周一": "1", "周二": "2", "周三": "3", "周四": "4", "周五": "5", "周六": "6"}
        days_str = ",".join(_day_map[d] for d in self.push_days if d in _day_map) or "4"
        hours_str = ",".join(t.split(":")[0].lstrip("0") or "0" for t in self.push_times) or "12"
        cron_expression = f"0 {hours_str} * * {days_str}"

        self._cron_job = await self.context.cron_manager.add_basic_job(
            name="crazy_thursday_notice",
            cron_expression=cron_expression,
            handler=self._push_notice,
            description=f"KFC 菜单推送（{', '.join(self.push_days)} {', '.join(self.push_times)}）",
            timezone="Asia/Shanghai",
        )
        logger.info(f"[疯狂星期四] 定时任务已注册，将向 {len(self.group_ids)} 个群推送。")

    def _resolve_platform_id(self, configured: str) -> str:
        if configured:
            return configured
        for platform in self.context.platform_manager.platform_insts:
            if platform.meta().name == "aiocqhttp":
                return platform.meta().id
        return "aiocqhttp"

    # ── KFC ───────────────────────────────────────────────────────

    async def _build_kfc_message(self) -> str:
        text = self.message_text
        try:
            async with KFCMenuFetcher(city=self.city) as fetcher:
                menu_text = await fetcher.get_menu_text()
            text = f"{text}\n\n📋 今日菜单：\n{menu_text}"
        except Exception as e:
            logger.warning(f"[疯狂星期四] 获取菜单失败，将只发送文案：{e}")
        return text

    async def _push_notice(self):
        content = await self._build_kfc_message()
        message = MessageChain([Plain(content)])
        for group_id in self.group_ids:
            session = f"{self.platform_id}:GroupMessage:{group_id}"
            try:
                success = await self.context.send_message(session, message)
                if success:
                    logger.info(f"[疯狂星期四] 已向群 {group_id} 推送消息。")
                else:
                    logger.warning(f"[疯狂星期四] 向群 {group_id} 发送失败：未找到平台 {self.platform_id}。")
            except Exception as e:
                logger.error(f"[疯狂星期四] 向群 {group_id} 发送出错：{e}")

    @filter.command("kfctest")
    async def kfc_test(self, event: AstrMessageEvent):
        """手动触发一次疯狂星期四推送（用于测试）"""
        await self._push_notice()
        yield event.plain_result("疯狂星期四推送已触发。")

    @filter.command("kfcmenu")
    async def kfc_menu(self, event: AstrMessageEvent):
        """获取当前 KFC 菜单"""
        try:
            async with KFCMenuFetcher(city=self.city) as fetcher:
                menu_text = await fetcher.get_menu_text()
            yield event.plain_result(menu_text)
        except Exception as e:
            yield event.plain_result(f"获取菜单失败：{e}")

    # ── 麦当劳 ────────────────────────────────────────────────────

    def _check_mcd_token(self, event: AstrMessageEvent):
        if not self.mcd_token:
            return event.plain_result("未配置麦当劳 MCP Token，请在插件配置中填写 mcd_token。")
        return None

    @filter.command("mcdcoupon")
    async def mcd_coupon(self, event: AstrMessageEvent):
        """查看麦麦省当前可领取的优惠券"""
        if err := self._check_mcd_token(event):
            yield err
            return
        try:
            async with MCDClient(token=self.mcd_token) as client:
                coupons = await client.get_coupons()
            if not coupons:
                yield event.plain_result("暂无优惠券数据。")
                return
            lines = ["🎫 麦麦省优惠券列表：\n"]
            for c in coupons:
                status = f"  {c['status']}" if c.get("status") else ""
                lines.append(f"  {c.get('title', '')}{status}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            yield event.plain_result(f"获取优惠券失败：{e}")

    @filter.command("mcdtools")
    async def mcd_tools(self, event: AstrMessageEvent):
        """列出麦当劳 MCP 可用工具（调试用）"""
        if err := self._check_mcd_token(event):
            yield err
            return
        try:
            async with MCDClient(token=self.mcd_token) as client:
                tools = await client.list_tools()
            yield event.plain_result("可用工具：\n" + "\n".join(f"  {t}" for t in tools))
        except Exception as e:
            yield event.plain_result(f"获取工具列表失败：{e}")

    @filter.command("mcdstore")
    async def mcd_store(self, event: AstrMessageEvent):
        """查询附近麦当劳门店，可附城市名：/mcdstore 北京市"""
        if err := self._check_mcd_token(event):
            yield err
            return
        city = _cmd_arg(event.message_str) or self.city
        try:
            async with MCDClient(token=self.mcd_token) as client:
                stores, raw = await client.find_stores(city)
            if stores:
                lines = [f"📍 {city} 附近麦当劳门店：\n"]
                for s in stores:
                    dist = f"  {s.get('distance', '')}" if s.get("distance") else ""
                    lines.append(f"  [{s.get('storeCode', '')}] {s.get('storeName', '')}  {s.get('address', '')}{dist}")
                lines.append("\n使用 /mcdmenu <门店编号> 查看菜单。")
                yield event.plain_result("\n".join(lines))
            else:
                # 解析失败，直接把原始文本返回给用户
                yield event.plain_result(raw or f"未找到 {city} 附近的麦当劳门店。")
        except Exception as e:
            yield event.plain_result(f"查询门店失败：{e}")

    @filter.command("mcdmenu")
    async def mcd_menu(self, event: AstrMessageEvent):
        """获取麦当劳菜单，可附门店编号：/mcdmenu 12345"""
        if err := self._check_mcd_token(event):
            yield err
            return
        store_code = _cmd_arg(event.message_str) or self.mcd_store_code
        if not store_code:
            yield event.plain_result(
                "请提供门店编号（/mcdmenu <门店编号>），或在后台配置默认 mcd_store_code。\n"
                "可用 /mcdstore 查询附近门店编号。"
            )
            return
        try:
            async with MCDClient(token=self.mcd_token) as client:
                raw = await client.get_menu_raw(store_code, self.mcd_order_type)
                menu = client.parse_menu(raw)
            store_name = ""
            _save_menu_cache(store_code, store_name, self.mcd_order_type, menu)
            formatted = _format_menu(store_code, store_name, self.mcd_order_type, menu)
            yield event.plain_result(f"=== 原始响应 ===\n{raw}\n\n=== 解析结果 ===\n{formatted}")
        except Exception as e:
            yield event.plain_result(f"获取菜单失败：{e}")

    @filter.command("mcdfood")
    async def mcd_food(self, event: AstrMessageEvent):
        """按名称或编号查询餐品：/mcdfood 巨无霸 或 /mcdfood 920215"""
        query = _cmd_arg(event.message_str)
        if not query:
            yield event.plain_result("请提供餐品名称或编号，用法：/mcdfood <名称或编号>")
            return

        cache = _load_menu_cache()
        if not cache:
            yield event.plain_result("菜单缓存为空，请先执行 /mcdmenu 获取菜单。")
            return

        meals: dict = cache.get("meals", {})

        # 优先精确匹配编号
        if query in meals:
            meal = meals[query]
            name = meal.get("name", query)
            price = meal.get("currentPrice", "")
            lines = [f"✅ {name}  编号：{query}  ¥{price}"]
            if self.mcd_token:
                store_code = cache.get("store_code", self.mcd_store_code)
                order_type = cache.get("order_type", self.mcd_order_type)
                if store_code:
                    try:
                        async with MCDClient(token=self.mcd_token) as client:
                            detail = await client.get_meal_detail(query, store_code, order_type)
                        detail_text = _format_detail(detail)
                        if detail_text:
                            lines.append("\n套餐组成：")
                            lines.append(detail_text)
                    except Exception:
                        pass
            yield event.plain_result("\n".join(lines))
            return

        # 名称模糊匹配
        matches = [(code, info) for code, info in meals.items() if query in info.get("name", "")]
        if not matches:
            yield event.plain_result(f"未找到包含 '{query}' 的餐品。请先执行 /mcdmenu 更新菜单缓存。")
            return

        if len(matches) == 1:
            code, meal = matches[0]
            name = meal.get("name", code)
            price = meal.get("currentPrice", "")
            lines = [f"✅ {name}  编号：{code}  ¥{price}"]
            if self.mcd_token:
                store_code = cache.get("store_code", self.mcd_store_code)
                order_type = cache.get("order_type", self.mcd_order_type)
                if store_code:
                    try:
                        async with MCDClient(token=self.mcd_token) as client:
                            detail = await client.get_meal_detail(code, store_code, order_type)
                        detail_text = _format_detail(detail)
                        if detail_text:
                            lines.append("\n套餐组成：")
                            lines.append(detail_text)
                    except Exception:
                        pass
            yield event.plain_result("\n".join(lines))
        else:
            lines = [f"🔍 '{query}' 匹配到 {len(matches)} 个餐品：\n"]
            for code, meal in matches[:15]:
                price = meal.get("currentPrice", "")
                price_str = f"  ¥{price}" if price else ""
                lines.append(f"  {meal.get('name', code)} [{code}]{price_str}")
            if len(matches) > 15:
                lines.append(f"  ...（共 {len(matches)} 个，请缩小搜索范围）")
            yield event.plain_result("\n".join(lines))

    # ── 生命周期 ──────────────────────────────────────────────────

    async def terminate(self):
        if self._cron_job:
            await self.context.cron_manager.delete_job(self._cron_job.job_id)
            self._cron_job = None
            logger.info("[疯狂星期四] 定时任务已清理。")
