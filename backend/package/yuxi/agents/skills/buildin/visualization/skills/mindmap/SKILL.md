---
name: 思维导图
slug: mindmap
description: 用户明确要求思维导图、脑图或 mind map 时必须先读取此 Skill；只用 render_mindmap 生成 SVG，禁止改用文档生成工具或手写 SVG。
---

# 思维导图

1. 用 `write_file` 写入 `outputs/.visualization-specs/<name>.mindmap.md`。
2. 使用两个空格一级的 Markdown 无序列表；第一项是唯一中心主题。根节点也必须写成 `- 项目治理`，禁止使用 `# 项目治理` 等标题语法。
   ```text
   - 项目治理
     - 计划
       - 里程碑
     - 风险
       - 风险识别
   ```
3. `output_name` 只能使用 1 至 80 个 ASCII 字母、数字、下划线或短横线，不含扩展名；中文标题也要转换为英文文件名，如 `project-kickoff-mindmap`。
4. `outline_path` 必须原样使用 `write_file` 成功返回的完整路径并保留 `.mindmap.md` 扩展名，不能传文件名主体。
5. 调用 `render_mindmap`，默认并优先使用 `horizontal` 双向中心布局；只有用户明确要求环形/径向导图，且节点较少、各分支规模接近时才使用 `radial`。成功后调用 `present_artifacts`。
6. 节点使用短语，不写 HTML、链接、图片或脚本。
