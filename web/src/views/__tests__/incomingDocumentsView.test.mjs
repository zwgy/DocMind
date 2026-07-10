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

assert.match(
  component,
  /摘要阶段关键事实/,
  '摘要阶段 structuredResult 只能作为辅助信息展示'
)
