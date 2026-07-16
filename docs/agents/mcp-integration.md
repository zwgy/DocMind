# MCP 集成

MCP（Model Context Protocol）是扩展智能体能力的重要方式。系统支持通过管理界面动态配置 MCP 服务器，无需修改代码。

内置 MCP 服务器以代码为事实源：系统启动时会自动补齐缺失项，并用代码中的最新连接与展示字段覆盖数据库定义；是否“已添加”以及工具级禁用列表仍保留数据库状态。

## 支持的传输协议

| 协议 | 说明 | 适用场景 |
|------|------|----------|
| Streamable HTTP | 流式 HTTP 连接 | 远程 MCP 服务 |
| SSE | Server-Sent Events | 标准 HTTP 长连接 |
| Stdio | 标准输入输出 | 本地进程 |

## 配置示例

### 远程 MCP 服务

```json
{
    "name": "custom-remote-mcp",
    "transport": "streamable_http",
    "url": "https://example.com/mcp"
}
```

### 本地 Python 进程

```json
{
    "name": "mysql-mcp-server",
    "transport": "stdio",
    "command": "uvx",
    "args": ["mysql_mcp_server"],
    "env": {
        "MYSQL_HOST": "localhost",
        "MYSQL_DATABASE": "your_database"
    }
}
```

## 服务器管理

管理界面使用“添加 / 移除”语义管理 MCP 服务器：

- 已添加：`enabled=true`，会加载到运行时缓存并可供 Agent 使用
- 可添加：`enabled=false`，记录保留但不会进入运行时

## 内置文档导出 MCP

源码位于 `backend/package/yuxi/agents/mcp/buildin/document_exporter.py`，提供 `generate_docx`、`generate_pdf`、`generate_xlsx` 三个工具。它以 stdio 运行，由 LangChain `MultiServerMCPClient` 通过 MCP `tools/list` 加载为 LangChain tools。

系统启动时会自动注册该内置 MCP。管理员只需在「扩展管理 → MCP」中添加它，再分配给目标 Agent；不会自动启用给所有 Agent。

MCP 只生成文件并返回临时产物路径。LangChain MCP adapter 的 `ToolCallInterceptor` 会将其复制到当前线程的 `outputs`，再由现有 `present_artifacts` 链路交付给用户；不需要额外 HTTP 路由、下载命令或 Compose 服务。

## 工具管理

MCP 工具支持粒度控制：管理员可以单独启用或禁用某个 MCP 服务器下的特定工具，实现精细化的权限管理。
