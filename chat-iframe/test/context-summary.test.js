import assert from 'node:assert/strict'
import test from 'node:test'

import { buildContextSummaryMessage, extractionStatusText } from '../src/utils/context-summary.ts'

const file = { id: 'f1', name: '来文.docx', source_file_id: 'S001' }

test('buildContextSummaryMessage creates ready context card payload', () => {
  const message = buildContextSummaryMessage({
    file,
    result: {
      matchStatus: 'matched',
      extractionStatus: 'ready',
      categories: { risk: { matched: true, evidence: '风险依据' } },
      items: [
        {
          item_id: 'i1',
          item_type: 'risk',
          data: { level: '高' },
          source_quote: '现场作业监护需加强'
        }
      ]
    }
  })

  assert.equal(message?.type, 'context_summary')
  assert.equal(message?.role, 'system')
  assert.equal(message?.contextSummary?.file.name, '来文.docx')
  assert.equal(message?.contextSummary?.statusText, '已生成结构化结果')
  assert.deepEqual(message?.contextSummary?.matchedCategories, [{ name: 'risk', evidence: '风险依据' }])
  assert.match(message?.content || '', /现场作业监护需加强/)
})

test('buildContextSummaryMessage updates with switched file', () => {
  const message = buildContextSummaryMessage({
    file: { id: 'f2', name: '补充材料.md' },
    result: null
  })

  assert.equal(message?.id, 'context-summary')
  assert.equal(message?.contextSummary?.file.id, 'f2')
  assert.equal(message?.contextSummary?.statusText, '等待查询')
})

test('buildContextSummaryMessage keeps backend summary when extraction items are empty', () => {
  const message = buildContextSummaryMessage({
    file,
    result: {
      matchStatus: 'matched',
      extractionStatus: 'ready',
      summary: 'Full document summary',
      items: []
    }
  })

  assert.match(message?.content || '', /Full document summary/)
})

test('extractionStatusText renders non-ready states', () => {
  assert.equal(extractionStatusText({ file, result: null, loading: true }), '查询中')
  assert.equal(extractionStatusText({ file, result: null, error: '查询失败' }), '查询失败')
  assert.equal(extractionStatusText({ file, result: { matchStatus: 'pending_sync', extractionStatus: 'not_found' } }), '待同步入库')
  assert.equal(extractionStatusText({ file, result: { matchStatus: 'matched', extractionStatus: 'failed' } }), '抽取失败')
})
