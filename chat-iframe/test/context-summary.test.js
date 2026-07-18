import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildContextSummaryMessage,
  displayExtractionDataEntries,
  extractionClassificationText,
  extractionItemTypeText,
  extractionStatusText,
  matchedExtractionCategories
} from '../src/utils/context-summary.ts'

const file = { name: '来文.docx', source_file_id: 'S001' }
const chatMessagesSource = readFileSync(
  new URL('../src/components/ChatMessages.vue', import.meta.url),
  'utf8'
)

test('context summary top area shows document metadata and fills missing values', () => {
  assert.match(chatMessagesSource, /contextSummary\.file\.name/)
  assert.doesNotMatch(
    chatMessagesSource,
    /contextSummary\.file\.title \|\| item\.message\.contextSummary\.file\.name/
  )
  assert.match(chatMessagesSource, /class="context-summary-meta"/)
  assert.match(chatMessagesSource, /\['incoming-type', '来文类型', file\.incoming_type \|\| '无'\]/)
  assert.match(chatMessagesSource, /\['source-unit', '发文单位', file\.source_unit \|\| '无'\]/)
  assert.match(chatMessagesSource, /\['incoming-date', '时间', file\.incoming_date \|\| '无'\]/)
  assert.doesNotMatch(chatMessagesSource, /\['document-number', '文号'/)
  assert.doesNotMatch(chatMessagesSource, /\['source-system', '来源'/)
})

test('buildContextSummaryMessage uses metadata returned by the matched incoming document', () => {
  const message = buildContextSummaryMessage({
    file,
    result: {
      matchStatus: 'matched',
      extractionStatus: 'ready',
      source_system: 'oa',
      document_number: '上铁辆〔2020〕316号',
      title: '路用客车检修运用管理办法',
      incoming_type: '集团公司文件',
      source_unit: '安全科',
      incoming_date: '2020-10-20'
    }
  })

  assert.deepEqual(message?.contextSummary?.file, {
    ...file,
    name: '路用客车检修运用管理办法',
    source_system: 'oa',
    document_number: '上铁辆〔2020〕316号',
    title: '路用客车检修运用管理办法',
    incoming_type: '集团公司文件',
    source_unit: '安全科',
    incoming_date: '2020-10-20'
  })
})

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
  assert.deepEqual(message?.contextSummary?.matchedCategories, [
    { name: 'risk', evidence: '风险依据' }
  ])
  assert.match(message?.content || '', /来文：来文\.docx/)
  assert.match(message?.content || '', /现场作业监护需加强/)
})

test('buildContextSummaryMessage updates with switched file', () => {
  const message = buildContextSummaryMessage({
    file: { name: '补充材料.md', source_file_id: 'S002' },
    result: null
  })

  assert.equal(message?.id, 'context-summary')
  assert.equal(message?.contextSummary?.file.source_file_id, 'S002')
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
  assert.equal(
    extractionStatusText({
      file,
      result: { matchStatus: 'pending_sync', extractionStatus: 'not_found' }
    }),
    '待同步入库'
  )
  assert.equal(
    extractionStatusText({ file, result: { matchStatus: 'matched', extractionStatus: 'failed' } }),
    '抽取失败'
  )
})

test('matchedExtractionCategories renders labels from backend display metadata', () => {
  assert.deepEqual(
    matchedExtractionCategories({
      matchStatus: 'matched',
      extractionStatus: 'ready',
      display: { categoryLabels: { regulation: '规章制度类' } },
      categories: {
        regulation: { matched: true, evidence: '摘要阶段分类：规章制度类' },
        risk_management: { matched: false }
      }
    }),
    [{ name: '规章制度类', evidence: '摘要阶段分类：规章制度类' }]
  )
})

test('extractionClassificationText uses backend classification display label', () => {
  assert.equal(
    extractionClassificationText({
      matchStatus: 'matched',
      extractionStatus: 'ready',
      classification: 'regulation',
      display: { classificationLabel: '规章制度类' }
    }),
    '规章制度类'
  )
})

test('extraction item labels use backend display metadata and hide duplicated source_quote data field', () => {
  const result = {
    matchStatus: 'matched',
    extractionStatus: 'ready',
    display: {
      schemaLabels: { management_requirement_item: '管理要求' },
      fieldLabels: {
        management_requirement_item: {
          department: '涉及部门',
          period_type: '要求类型',
          requirement: '管理要求',
          source_quote: '原文依据'
        }
      }
    }
  }

  assert.equal(extractionItemTypeText('management_requirement_item', result), '管理要求')
  assert.deepEqual(
    displayExtractionDataEntries(
      {
        role: null,
        department: '集团公司车辆部',
        period_type: '长期性',
        requirement: '制定集团公司路用客车检修运用管理办法。',
        source_quote: '原文依据'
      },
      'management_requirement_item',
      result
    ),
    [
      ['涉及部门', '集团公司车辆部'],
      ['要求类型', '长期性'],
      ['管理要求', '制定集团公司路用客车检修运用管理办法。']
    ]
  )
})
