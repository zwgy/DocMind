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
