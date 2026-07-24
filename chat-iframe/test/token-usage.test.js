import assert from 'node:assert/strict'
import test from 'node:test'

import { buildTokenUsageView } from '../src/utils/token-usage.ts'

const usage = {
  input_tokens: 32601,
  input_source: 'provider_usage',
  context_window: 32768,
  prompt_budget: 27648,
  input_budget_delta: -4953,
  protocol_correction_tokens: 12317,
  tool_count: 51,
  breakdown_estimate: {
    messages: 3000,
    private_summary: 1000,
    system: 2000,
    tools: 14284
  }
}

test('uses the same measured total and estimated breakdown contract as the host UI', () => {
  const view = buildTokenUsageView(usage)

  assert.equal(view?.used, 32601)
  assert.equal(view?.percent, 99)
  assert.equal(view?.sourceLabel, '实际用量')
  assert.equal(view?.hasSummary, true)
  assert.equal(view?.budgetDelta, -4953)
  assert.equal(view?.correction, 12317)
  assert.deepEqual(
    view?.segments.map((segment) => segment.value),
    [3000, 1000, 2000, 14284, 12317]
  )
  assert.deepEqual(
    view?.segments.map((segment) => segment.label),
    ['对话消息', '历史摘要', '系统说明', '可用工具（51 个）', '模型协议/模板校正']
  )
  assert.equal(
    Number(view?.segments.reduce((total, segment) => total + Number.parseFloat(segment.percent), 0).toFixed(2)),
    99.49
  )
})

test('does not claim provider measurement when only calibrated estimate exists', () => {
  const view = buildTokenUsageView({
    ...usage,
    input_tokens: 22000,
    input_source: 'calibrated_estimate',
    protocol_correction_tokens: null
  })

  assert.equal(view?.sourceLabel, '校准后的估算')
  assert.equal(view?.correction, null)
})
