from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain


@register("astrbot_plugin_crazy_thursday_notice", "NeroUMU", "每到周四自动向 QQ 群推送疯狂星期四提醒", "1.0.0")
class CrazyThursdayPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.context: Context = context
        self.config: dict = config or {}
        self._cron_job = None

    async def initialize(self):
        self.group_ids: list[str] = self.config.get("group_ids", [])
        self.platform_id: str = self.config.get("platform_id", "aiocqhttp")
        self.cron_expression: str = self.config.get("cron_expression", "0 12 * * 4")
        self.message_text: str = self.config.get("message", "今天是肯德基疯狂星期四！V我50！")

        if not self.group_ids:
            logger.warning("[疯狂星期四] 未配置群号，定时推送不会执行。请在插件配置中填写 group_ids。")
            return

        self._cron_job = await self.context.cron_manager.add_basic_job(
            name="crazy_thursday_notice",
            cron_expression=self.cron_expression,
            handler=self._push_notice,
            description="每周四疯狂星期四提醒",
            timezone="Asia/Shanghai",
        )
        logger.info(f"[疯狂星期四] 定时任务已注册，将向 {len(self.group_ids)} 个群推送。")

    async def _push_notice(self):
        message = MessageChain([Plain(self.message_text)])
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

    @filter.command("kfc")
    async def kfc_test(self, event: AstrMessageEvent):
        """手动触发一次疯狂星期四推送（用于测试）"""
        await self._push_notice()
        yield event.plain_result("疯狂星期四推送已触发。")

    async def terminate(self):
        if self._cron_job:
            await self.context.cron_manager.delete_job(self._cron_job.job_id)
            self._cron_job = None
            logger.info("[疯狂星期四] 定时任务已清理。")
