from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain

from .kfc_scraper import KFCMenuFetcher
from .mcd_scraper import MCDMenuFetcher


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
        self.city: str = self.config.get("city", "上海")
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

    async def _build_message(self) -> str:
        text = self.message_text
        try:
            async with KFCMenuFetcher(city=self.city) as fetcher:
                menu_text = await fetcher.get_menu_text()
            text = f"{text}\n\n📋 今日菜单：\n{menu_text}"
        except Exception as e:
            logger.warning(f"[疯狂星期四] 获取菜单失败，将只发送文案：{e}")
        return text

    async def _push_notice(self):
        content = await self._build_message()
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

    @filter.command("mcdmenu")
    async def mcd_menu(self, event: AstrMessageEvent):
        """获取麦当劳菜单（需在后台配置 mcd_token 和 mcd_store_code）"""
        if not self.mcd_token:
            yield event.plain_result("未配置麦当劳 MCP Token，请在插件配置中填写 mcd_token。")
            return
        if not self.mcd_store_code:
            yield event.plain_result("未配置麦当劳门店编号，请在插件配置中填写 mcd_store_code。")
            return
        try:
            async with MCDMenuFetcher(token=self.mcd_token) as fetcher:
                text = await fetcher.get_menu_text(self.mcd_store_code, self.mcd_order_type)
            yield event.plain_result(text)
        except Exception as e:
            yield event.plain_result(f"获取麦当劳菜单失败：{e}")

    @filter.command("mcddetail")
    async def mcd_detail(self, event: AstrMessageEvent):
        """获取麦当劳餐品详情，用法：/mcddetail <餐品编号>"""
        if not self.mcd_token:
            yield event.plain_result("未配置麦当劳 MCP Token，请在插件配置中填写 mcd_token。")
            return
        if not self.mcd_store_code:
            yield event.plain_result("未配置麦当劳门店编号，请在插件配置中填写 mcd_store_code。")
            return
        args = event.message_str.strip().split(maxsplit=1)
        if len(args) < 2 or not args[1].strip():
            yield event.plain_result("请提供餐品编号，用法：/mcddetail <餐品编号>")
            return
        code = args[1].strip()
        try:
            async with MCDMenuFetcher(token=self.mcd_token) as fetcher:
                text = await fetcher.get_meal_detail_text(code, self.mcd_store_code, self.mcd_order_type)
            yield event.plain_result(text)
        except Exception as e:
            yield event.plain_result(f"获取餐品详情失败：{e}")

    @filter.command("mcdcoupon")
    async def mcd_coupon(self, event: AstrMessageEvent):
        """获取麦当劳优惠券列表（需在后台配置 mcd_token）"""
        if not self.mcd_token:
            yield event.plain_result("未配置麦当劳 MCP Token，请在插件配置中填写 mcd_token。")
            return
        try:
            async with MCDMenuFetcher(token=self.mcd_token) as fetcher:
                text = await fetcher.get_coupons_text()
            yield event.plain_result(text)
        except Exception as e:
            yield event.plain_result(f"获取麦当劳优惠券失败：{e}")

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

    async def terminate(self):
        if self._cron_job:
            await self.context.cron_manager.delete_job(self._cron_job.job_id)
            self._cron_job = None
            logger.info("[疯狂星期四] 定时任务已清理。")
