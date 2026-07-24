import assert from 'node:assert/strict'
import test from 'node:test'

import { buildTokenUsageView } from '../src/utils/tokenUsage.js'

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

test('shows provider total separately from estimated component breakdown', () => {
  const view = buildTokenUsageView(usage)

  assert.equal(view.used, 32601)
  assert.equal(view.percent, 99)
  assert.equal(view.sourceLabel, '实际用量')
  assert.equal(view.budgetDelta, -4953)
  assert.equal(view.correction, 12317)
  assert.deepEqual(
    view.segments.map((segment) => segment.value),
    [3000, 1000, 2000, 14284, 12317]
  )
  assert.deepEqual(
    view.segments.map((segment) => segment.key),
    ['messages', 'summary', 'system', 'tools', 'overhead']
  )
  assert.equal(view.segments.at(-1).label, '模型协议/模板校正')
  assert.equal(
    Number(view.segments.reduce((total, segment) => total + Number.parseFloat(segment.percent), 0).toFixed(2)),
    99.49
  )
})

test('labels a request without provider usage as calibrated estimate', () => {
  const view = buildTokenUsageView({
    ...usage,
    input_tokens: 22000,
    input_source: 'calibrated_estimate',
    protocol_correction_tokens: null
  })

  assert.equal(view.sourceLabel, '校准后的估算')
  assert.equal(view.correction, null)
})
