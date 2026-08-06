---
name: scheduled-task
slug: scheduled-task
description: "管理当前用户自己的通知或 Agent 执行型定时任务。"
---

# 个人定时任务

仅在用户明确要求创建、查询、暂停、恢复或取消自己的提醒、定时通知或定时 Agent 执行时使用本 Skill。任务所有者和接收者始终是当前用户，不能代他人创建或修改任务。

## 创建

创建前必须获得任务名称、时区，以及一种完整调度规则：

- 单次：`at` 和未来的 `run_at`；
- 周期：`interval`、以秒表示且不少于 60 秒的 `interval_seconds`，以及 `anchor_at`；
- Cron：五段 `cron_expression`。

用户表达的时间、时区、频率或通知内容有歧义时，先调用 `ask_user_question` 澄清。确认完整信息后只调用一次 `create_personal_scheduled_task`；工具重放会按本次调用自动幂等。

通知动作必须有通知标题和正文。Agent 动作必须有目标顶层 Agent 的准确 `agent_slug`、执行指令和 60 到 3600 秒的超时；不得替用户猜测 Agent，也不得在载荷中指定工具、知识库、MCP 或 Skills。目标 Agent 的实际能力始终由管理员保存的当前 Agent 配置决定，创建时和执行时都会校验该 Agent 对当前用户可见且不是 SubAgent。缺少任何必要信息或存在歧义时，使用 `ask_user_question` 补齐后再创建。

### 工具载荷示例

创建工具使用扁平参数。创建单次通知时，直接使用下面的完整形状；`schedule_kind` 必须是 `at`，不要使用 `once`、`time_at` 或嵌套 `schedule` 对象：

```json
{
  "name": "晨会提醒",
  "timezone": "Asia/Shanghai",
  "schedule_kind": "at",
  "run_at": "2026-08-08T09:00:00+08:00",
  "action_type": "notification",
  "title": "晨会开始",
  "content": "请准时参加晨会。"
}
```

周期任务只把调度字段替换为 `"schedule_kind":"interval","interval_seconds":3600,"anchor_at":"2026-08-08T09:00:00+08:00"`，或 `"schedule_kind":"cron","cron_expression":"0 9 * * 1-5"`。创建 Agent 执行型任务时，使用 `"action_type":"agent","agent_slug":"daily-assistant","instruction":"整理今天待办。","timeout_seconds":300`。

信息已经完整时不要试探性地重复调用创建工具；一次调用失败时先阅读错误并只修正错误字段，再决定是否重试。

## 查询和变更

先用 `list_personal_scheduled_tasks` 找到真实 `job_id` 和 `version`，再暂停、恢复或取消。没有真实 ID 时不得猜测；版本冲突时重新查询并说明任务已被更新。

单次任务不能暂停；已经触发的单次任务不能取消。修改名称、时间规则或通知内容暂不由本 Skill 执行，应引导用户在定时任务页面编辑。
