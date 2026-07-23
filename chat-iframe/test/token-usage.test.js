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
  assert.equal(view?.sourceLabel, '模型服务实测')
  assert.equal(view?.budgetDelta, -4953)
  assert.equal(view?.correction, 12317)
  assert.deepEqual(
    view?.segments.map((segment) => segment.value),
    [3000, 1000, 2000, 14284]
  )
  assert.ok(view?.segments.every((segment) => segment.label.includes('估算')))
})

test('does not claim provider measurement when only calibrated estimate exists', () => {
  const view = buildTokenUsageView({
    ...usage,
    input_tokens: 22000,
    input_source: 'calibrated_estimate',
    protocol_correction_tokens: null
  })

  assert.equal(view?.sourceLabel, 'usage 校准估算')
  assert.equal(view?.correction, null)
})
