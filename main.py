import httpx

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register

from .kfc_scraper import KFCMenuFetcher

_CRAZY_COPY_URL = "https://v50.deno.dev/"


async def _fetch_crazy_copy() -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_CRAZY_COPY_URL)
            resp.raise_for_status()
            return resp.text.strip()
    except Exception as e:
        logger.warning(f"[疯狂星期四] 获取疯四文案失败：{e}")
        return ""


@register(
    "astrbot_plugin_crazy_thursday_notice",
    "NeroUMU",
    "每到周四自动向 QQ 群推送疯狂星期四提醒、疯四文案及 KFC 菜单",
    "1.0.0",
)
class CrazyThursdayPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context: Context = context
        self.config: dict = config or {}
        self._cron_job = None

    async def initialize(self):
        self.group_ids: list[str] = self.config.get("group_ids", [])
        self.push_days: list[str] = self.config.get("push_days", ["周四"])
        self.push_hours: list[str] = self.config.get("push_hours", ["12"])
        self.push_minutes: list[str] = self.config.get("push_minutes", ["0"])
        self.reminder_text: str = self.config.get(
            "reminder_text", "今天是肯德基疯狂星期四！V我50！"
        )
        self.enable_menu: bool = self.config.get("enable_menu", True)
        self.enable_crazy_copy: bool = self.config.get("enable_crazy_copy", True)
        self.platform_id: str = self._resolve_platform_id(
            self.config.get("platform_id", "")
        )
        self.city: str = self.config.get("city", "上海市")

        if not self.group_ids:
            logger.warning(
                "[疯狂星期四] 未配置群号，定时推送不会执行。请在插件配置中填写 group_ids。"
            )
            return

        _day_map = {
            "周日": "sun",
            "周一": "mon",
            "周二": "tue",
            "周三": "wed",
            "周四": "thu",
            "周五": "fri",
            "周六": "sat",
        }
        days_str = (
            ",".join(_day_map[d] for d in self.push_days if d in _day_map) or "thu"
        )
        hours_str = ",".join(h.lstrip("0") or "0" for h in self.push_hours) or "12"
        minutes_str = ",".join(m.lstrip("0") or "0" for m in self.push_minutes) or "0"
        cron_expression = f"{minutes_str} {hours_str} * * {days_str}"

        self._cron_job = await self.context.cron_manager.add_basic_job(
            name="crazy_thursday_notice",
            cron_expression=cron_expression,
            handler=self._push_notice,
            description=f"KFC 菜单推送（{', '.join(self.push_days)} {', '.join(self.push_hours)}:{', '.join(self.push_minutes)}）",
            timezone="Asia/Shanghai",
        )
        logger.info(
            f"[疯狂星期四] 定时任务已注册，将向 {len(self.group_ids)} 个群推送。"
        )

    def _resolve_platform_id(self, configured: str) -> str:
        if configured:
            return configured
        for platform in self.context.platform_manager.platform_insts:
            if platform.meta().name == "aiocqhttp":
                return platform.meta().id
        return "aiocqhttp"

    # ── KFC ───────────────────────────────────────────────────────

    async def _build_kfc_messages(self) -> list[str]:
        parts: list[str] = []

        if self.enable_crazy_copy:
            copy_text = await _fetch_crazy_copy()
            if copy_text:
                parts.append(copy_text)

        parts.append(self.reminder_text)

        if self.enable_menu:
            try:
                async with KFCMenuFetcher(city=self.city) as fetcher:
                    menu_text = await fetcher.get_menu_text()
                parts.append(f"📋 今日菜单：\n{menu_text}")
            except Exception as e:
                logger.warning(f"[疯狂星期四] 获取菜单失败：{e}")

        return parts

    async def _push_notice(self):
        messages = await self._build_kfc_messages()
        for group_id in self.group_ids:
            session = f"{self.platform_id}:GroupMessage:{group_id}"
            for content in messages:
                try:
                    success = await self.context.send_message(
                        session, MessageChain([Plain(content)])
                    )
                    if success:
                        logger.info(f"[疯狂星期四] 已向群 {group_id} 推送消息。")
                    else:
                        logger.warning(
                            f"[疯狂星期四] 向群 {group_id} 发送失败：未找到平台 {self.platform_id}。"
                        )
                except Exception as e:
                    logger.error(f"[疯狂星期四] 向群 {group_id} 发送出错：{e}")

    @filter.command("kfcpush")
    async def kfc_push(self, event: AstrMessageEvent):
        """手动触发一次 KFC 疯狂星期四推送"""
        await self._push_notice()
        yield event.plain_result("推送已触发。")

    @filter.command("kfcmenu")
    async def kfc_menu(self, event: AstrMessageEvent):
        """获取当前 KFC 菜单"""
        try:
            async with KFCMenuFetcher(city=self.city) as fetcher:
                menu_text = await fetcher.get_menu_text()
            yield event.plain_result(menu_text)
        except Exception as e:
            yield event.plain_result(f"获取菜单失败：{e}")

    @filter.command("crazycopy")
    async def crazy_copy(self, event: AstrMessageEvent):
        """获取一条随机疯四文案"""
        copy_text = await _fetch_crazy_copy()
        if copy_text:
            yield event.plain_result(copy_text)
        else:
            yield event.plain_result("获取疯四文案失败，请稍后再试。")

    # ── 生命周期 ──────────────────────────────────────────────────

    async def terminate(self):
        if self._cron_job:
            await self.context.cron_manager.delete_job(self._cron_job.job_id)
            self._cron_job = None
            logger.info("[疯狂星期四] 定时任务已清理。")
