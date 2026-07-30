---
name: 思维导图
slug: mindmap
description: 用户明确要求思维导图、脑图或 mind map 时必须先读取此 Skill；第一步直接读取系统列出的此 Skill 路径，禁止先列目录；随后只调用 render_mind_map 生成 SVG，禁止改用文档生成工具或手写 SVG。
---

# 思维导图

1. 不调用 `ls`、`write_file`、`edit_file` 或 `execute`；直接把完整 Markdown 大纲放入 `render_mind_map.outline`。
2. `outline` 使用两个空格一级的 Markdown 无序列表；第一项是唯一中心主题。根节点必须写成 `- 项目治理`，禁止使用 `# 项目治理` 等标题语法。
   ```text
   - 项目治理
     - 计划
       - 里程碑
     - 风险
       - 风险识别
   ```
3. `output_name` 使用 1 至 80 个中英文字母、数字、下划线或短横线，不含扩展名；用户明确指定名称时原样使用，不要翻译或改写，如 `项目启动思维导图-0730`。
4. 默认并优先使用 `horizontal` 双向中心布局；只有用户明确要求环形/径向导图，且节点较少、各分支规模接近时才使用 `radial`。
5. 首次调用直接套用以下参数结构，不得猜测 `outline_path`、`file_path` 或 `source_path`：
   ```json
   {
     "outline": "- 项目治理\n  - 计划\n    - 里程碑\n  - 风险\n    - 风险识别",
     "output_name": "项目治理思维导图",
     "layout": "horizontal"
   }
   ```
6. `outline` 不超过 8,000 个字符、100 个节点，单节点不超过 48 个字符；长文先归纳为短语式层级大纲。
7. `render_mind_map` 成功返回后系统会自动展示 SVG，不再调用 `present_artifacts`，也不再次调用渲染工具。最终回答简要说明已生成、节点数、布局和返回的 `artifact_path`。节点不写 HTML、链接、图片或脚本。
