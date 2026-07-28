---
name: MySQL 报表
slug: mysql-reporter
description: "查询 MySQL 并生成离线报表或数据图表；适用于统计销售数据、分析用户行为、生成业务指标等需求。"
---

# MySQL 报表技能

根据用户的指令，通过终端脚本访问 MySQL 数据库，并结合图表绘制工具构建 SQL 查询报告。

## 操作流程

1. 理解用户的指令，明确报表的需求和目标
2. 通过 terminal 进入技能目录：`cd /home/gem/skills/mysql-reporter`
3. 使用 `python scripts/list_tables.py` 查看可用表；如果脚本提示缺少 MySQL 配置，按“环境变量缺失处理”回复用户
4. 必要时用 `python scripts/describe_table.py --table 表名` 查看表结构
5. 生成正确且高效的只读 SQL。需要图表时使用 `python scripts/query.py --sql "SQL语句" --timeout 60 --output /home/gem/user-data/outputs/.visualization-data/report.csv` 写出聚合 CSV
6. 读取 `data-chart/SKILL.md`，根据 CSV 的列名、行数和一条样本调用 `render_data_chart`
7. 使用 `present_artifacts` 交付 SVG，不嵌入远程图片或 markdown 图片 URL

## 环境变量缺失处理

脚本只读取 Agent 沙盒中的环境变量，不读取后端 `.env` 或 Docker Compose 变量。必填变量包括：

- `MYSQL_HOST`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`

可选变量包括：

- `MYSQL_PORT`：默认 `3306`
- `MYSQL_DATABASE_DESCRIPTION`：数据库业务说明，用于辅助理解表和指标含义

如果执行脚本时出现 `MySQL configuration missing required key`，不要继续猜测连接信息或编造报表。应明确告诉用户：需要在个人设置中的「沙盒环境变量」里配置缺失的 `MYSQL_*` 变量；保存后仅对新建沙盒生效，需要重新发起任务或新建会话后再执行。

## 关键约束

- 生成的 SQL 查询必须正确且高效，避免全表扫描
- MySQL 操作必须通过本技能 `scripts/` 下的 CLI 脚本执行，不要调用平台内置 MySQL tools
- 不要在报表或错误说明中输出 `MYSQL_PASSWORD` 等敏感环境变量的值，只能说明缺少哪些变量名
- 完整查询数据不能回传到模型上下文；只返回 CSV 路径、列名、行数和一条样本
- 只返回报表相关的结论，不要返回原始 SQL 查询语句

## 允许的工具

- terminal：执行 `scripts/list_tables.py`、`scripts/describe_table.py`、`scripts/query.py`
- data-chart：生成离线数据图表
