import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const component = readFileSync(resolve(__dirname, '../IncomingDocumentsView.vue'), 'utf8')

const canRetry = component.match(/function canRetry\(record\) \{[\s\S]*?\n\}/)?.[0] || ''

assert.match(
  canRetry,
  /record\?\.status === 'failed'[\s\S]*record\?\.status === 'ready'/,
  '来文已完成但结构化抽取为空时，也需要暴露重新处理入口'
)

assert.match(
  component,
  /businessExtractionGroups/,
  '详情页结构化结果应按 schema 分组展示正式业务抽取结果'
)

assert.doesNotMatch(
  component,
  /v-for="\([\s\S]*?\) in businessExtractionItems"/,
  '详情页不应直接平铺业务抽取 item'
)

assert.doesNotMatch(component, /摘要阶段关键事实/, '不应展示后端已不再返回的 structuredResult')
assert.match(component, /documentMetadataEntries/, '详情页应展示动态 document_metadata')
assert.match(component, /attachmentMarkdownTruncated/, 'Markdown 预览截断时必须明确提示')
assert.match(component, /detail\.reviewStatus === 'confirmed'/, '详情页应展示并锁定已确认状态')
assert.match(
  component,
  /detail\.additionalClassifications/,
  '详情页应展示附加分类的置信度和原文依据'
)
assert.match(component, /extracting: \{ label: '抽取中'/, '页面应识别当前 extracting 状态')
assert.match(component, /partial: \{ label: '部分入库'/, '页面应识别附件部分入库状态')
assert.match(component, /sourceFileIds: \[\]/, '知识库导入应支持选择附件')
assert.match(component, /record\?\.linkedFileId/, '知识库预览应使用附件级 file ID')
assert.match(component, /批量入库/, '来文级入库入口应明确为批量操作')
assert.match(component, /openImportAttachmentPreview/, '批量入库前应支持预览附件原文')
assert.match(component, /expandedRowRender/, '来文列表应支持默认折叠的附件清单')
assert.match(component, /:show-expand-column="false"/, '来文列表应隐藏表格默认的展开按钮')
assert.match(component, /原文依据：/, '结构化条目证据应使用明确的原文依据标签')
assert.doesNotMatch(
  component,
  /<blockquote v-if="item\.source_quote">/,
  '结构化条目不应重复展示原文依据'
)
assert.match(component, /canDelete\(record\)/, '来文行/详情抽屉需要 canDelete 守卫')
assert.match(component, /'importing', 'partial', 'indexed'/, 'canDelete 必须拦截已入库知识库的来文（importing/partial/indexed）')
assert.match(component, /'parsing', 'extracting'/, 'canDelete 必须拦截处理中的来文（parsing/extracting）')
assert.match(component, /openDeleteConfirm\(record\)/, '列表行的删除按钮应触发 openDeleteConfirm')
assert.match(component, /openDeleteConfirm\(detail\)/, '详情抽屉的删除按钮也应触发 openDeleteConfirm')
assert.match(component, /confirmDelete/, '删除弹窗的确认按钮应调用 confirmDelete')
assert.match(component, /isDeleteConfirmValid/, '删除确认应要求用户输入来源单号后 6 位')
assert.match(component, /incomingDocumentApi\.remove/, '删除逻辑必须通过 incomingDocumentApi.remove 调用后端')
