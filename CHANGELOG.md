# Changelog

## v2.3.0

### 新增
- 疯四文案功能：从 `v50.deno.dev` 实时拉取随机文案
- `/crazycopy` 命令：手动获取一条随机疯四文案
- 配置项 `enable_crazy_copy`：控制推送时是否附带疯四文案
- 配置项 `enable_menu`：控制推送时是否附带 KFC 菜单

### 变更
- `push_times` 拆分为 `push_hours` 和 `push_minutes`，分别提供 24 小时和 60 分钟选项
- `message` 配置项更名为 `reminder_text`
- `/kfctest` 命令更名为 `/kfcpush`
- 推送消息改为逐条发送，不再拼接为单条

### 修复
- 星期映射改用文字缩写（`mon`/`thu` 等），修复 APScheduler 数字映射歧义导致推送日期偏移的问题