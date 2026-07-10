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
  /businessExtractionItems/,
  '详情页结构化结果应展示分类完成后的正式业务抽取结果'
)

assert.match(
  component,
  /摘要阶段关键事实/,
  '摘要阶段 structuredResult 只能作为辅助信息展示'
)
