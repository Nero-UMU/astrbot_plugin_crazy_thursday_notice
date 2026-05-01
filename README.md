# 疯狂星期四通知

每到周四自动向 QQ 群推送疯狂星期四提醒、疯四文案及 KFC 当日菜单。

## 功能

- 定时推送：可自定义推送日期（周几）、小时、分钟
- 疯四文案：从 `v50.deno.dev` 实时拉取随机疯四文案
- KFC 菜单：按城市拉取附近 KFC 门店当日外送菜单
- 支持手动触发推送、查询菜单、获取文案

## 命令

| 命令 | 说明 |
|---|---|
| `/kfcpush` | 手动触发一次推送（等同于定时推送） |
| `/kfcmenu` | 获取当前 KFC 当日菜单 |
| `/crazycopy` | 获取一条随机疯四文案 |

## 配置

| 配置项 | 类型 | 说明 | 默认值 |
|---|---|---|---|
| `group_ids` | list | 要推送的 QQ 群号列表 | `[]` |
| `push_days` | list | 每周推送日期 | `["周四"]` |
| `push_hours` | list | 推送小时（0-23） | `["12"]` |
| `push_minutes` | list | 推送分钟（0-59） | `["0"]` |
| `reminder_text` | string | 疯四提醒文案 | `"今天是肯德基疯狂星期四！V我50！"` |
| `enable_menu` | bool | 是否附带 KFC 当日菜单 | `true` |
| `enable_crazy_copy` | bool | 是否附带疯四文案 | `true` |
| `city` | string | 定位城市 | `"上海市"` |

## 依赖

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) >= v4.5.0

## 作者

[NeroUMU](https://github.com/Nero-UMU)
