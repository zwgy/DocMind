# chat-iframe 后端附件同步

## 目标

解决 chat-iframe 嵌入其他系统后，浏览器读取附件地址受到跨域限制、且浏览器下载后再上传链路较慢的问题。

## 范围

- chat-iframe 查询到附件为 `pending_sync` 时，使用接入方通过 `setFiles()` 提供的 `source_url` 请求现有 `/api/incoming-documents/ingest`。
- DocMind 后端下载附件，并与 multipart 已上传文件统一交给 `ingest_files()` 保存和投递解析任务。
- iframe 展示后端下载中、解析中、完成和失败状态，并通过抽取查询接口持续对账。
- iframe 在聊天区顶部持续展示附件准备状态；失败时保留错误，成功解析后短暂提示并自动收起。
- 状态条位于可选通知轮播下方的弹性工作区内；没有通知时不预留空白，有通知时自动压缩消息区高度。
- 同一批附件正在准备时复用既有任务，不重复请求后端下载。
- 最小化或关闭悬浮窗不销毁 iframe，任务与状态继续；重新展开或浏览器页面恢复可见时立即向后端对账。宿主 SPA 以 `source_system + source_function_id + business_id` 作为页面身份；切页时调用 `setPageContext()`，父 SDK 会中止并清空旧页任务、重载 iframe 以清空附件摘要和轮询，再接收新页面内容与附件。
- 用户在附件下载或解析完成前提问时，保留输入并提示当前状态，不发送缺少附件上下文的问题。

## 非目标

- 本次不新增自定义 `fileLoader`、签名 URL、特定业务系统适配器或独立 URL 入库接口。
- 不固定附件服务器 host 或路径；接入方提供 DocMind 后端可访问的绝对 HTTP/HTTPS 地址。相对地址由父 SDK 按嵌入页面补全。
- 不改变现有来文持久化和解析流程；multipart 直接上传继续兼容。
- 宿主页面刷新不会中断已经提交到 DocMind 的下载和解析；返回页面后由抽取查询结果恢复状态。尚未成功提交的请求则按查询结果重新发起。
- 嵌入系统通过 `document_metadata.source_doc_id` 标识来文，同一来文的附件分别使用 `source_file_id`；未传来文 ID 时允许使用页面 `business_id` 兼容一页一来文的旧接入。
- 页面会话身份为 `source_system + source_function_id + business_id`；来文解析身份为 `source_system + source_doc_id`，附件身份为 `source_system + source_doc_id + source_file_id`。同一来文出现在不同模块或业务页面时复用解析结果，聊天历史仍按页面隔离。

## 交互选择

附件状态放在聊天区顶部、通知轮播下方。该位置在整个准备过程中稳定可见，不写入会话历史，也不会像瞬时 toast 一样被用户错过。

## 验收标准

- [x] `pending_sync` 附件触发 DocMind 后端按 `source_url` 下载，并复用现有入库接口与解析流程。
- [x] iframe 可见“DocMind 正在下载附件”“附件已下载，正在解析”“附件解析完成”或明确失败原因。
- [x] 同一附件准备期间重复提问不会重复下载或提交。
- [x] 附件未解析完成时不发送文件问答，输入内容保持可重试。
- [x] 请求受理后自动刷新后端状态，解析为 `ready` 后刷新摘要并提示用户。
- [x] 现有 multipart 文件上传方式继续可用；新 iframe 不再要求浏览器读取附件内容。
- [x] 父子窗口消息继续校验既有 window 与 origin 边界。
- [x] 有无顶部通知轮播时状态条均位于消息区上方，且不挤压输入区。
- [x] 悬浮窗关闭/最小化保持状态；SPA 通过 `setPageContext()` 切页时清空父 SDK 和 iframe 旧状态。
