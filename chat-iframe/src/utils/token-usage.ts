const TOKEN_COUNT_K_UNIT = 1024

function nonNegativeNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : null
}

function finiteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

export function formatTokenCount(value: unknown) {
  const numeric = finiteNumber(value)
  if (numeric === null) return '-'
  const sign = numeric < 0 ? '-' : ''
  const absolute = Math.abs(numeric)
  if (absolute >= TOKEN_COUNT_K_UNIT) {
    const digits = absolute >= TOKEN_COUNT_K_UNIT * 10 ? 1 : 2
    return `${sign}${(absolute / TOKEN_COUNT_K_UNIT).toFixed(digits).replace(/\.0+$/, '')}k`
  }
  return String(Math.round(numeric))
}

export function buildTokenUsageView(usage: Record<string, unknown> | null | undefined) {
  if (!usage) return null
  const used = nonNegativeNumber(usage.input_tokens)
  if (used === null) return null

  const contextWindow = nonNegativeNumber(usage.context_window)
  const promptBudget = nonNegativeNumber(usage.prompt_budget)
  const budgetDelta = finiteNumber(usage.input_budget_delta)
  const correction = finiteNumber(usage.protocol_correction_tokens)
  const breakdown =
    usage.breakdown_estimate &&
    typeof usage.breakdown_estimate === 'object' &&
    !Array.isArray(usage.breakdown_estimate)
      ? (usage.breakdown_estimate as Record<string, unknown>)
      : {}
  const rawSegments = [
    { key: 'messages', label: '对话消息', value: nonNegativeNumber(breakdown.messages) || 0 },
    {
      key: 'summary',
      label: '历史摘要',
      value: nonNegativeNumber(breakdown.private_summary) || 0
    },
    { key: 'system', label: '系统说明', value: nonNegativeNumber(breakdown.system) || 0 },
    {
      key: 'tools',
      label: `可用工具（${nonNegativeNumber(usage.tool_count) || 0} 个）`,
      value: nonNegativeNumber(breakdown.tools) || 0
    }
  ].filter((segment) => segment.value > 0)
  const compositionTotal = Math.max(
    rawSegments.reduce((total, segment) => total + segment.value, 0),
    1
  )
  // 分项只是本地估算，模型实测总量可能因协议模板和校准而更高或更低；先压缩超出的估算，
  // 再用灰色段补齐不足，才能让彩色段和已用总量共同落在同一条上下文窗口刻度上。
  const visualScale = compositionTotal > used ? used / compositionTotal : 1
  const barSegments = rawSegments.map((segment) => ({
    ...segment,
    visualValue: segment.value * visualScale
  }))
  const estimatedVisualTotal = barSegments.reduce((total, segment) => total + segment.visualValue, 0)
  const overhead = Math.max(used - estimatedVisualTotal, 0)
  if (overhead > 0) {
    barSegments.push({
      key: 'overhead',
      label: '模型协议/模板校正',
      value: overhead,
      visualValue: overhead
    })
  }
  const barCapacity = Math.max(contextWindow || used, 1)
  const sourceLabels: Record<string, string> = {
    provider_usage: '实际用量',
    calibrated_estimate: '校准后的估算',
    fallback_estimate: '首次预估'
  }

  // 与主站保持同一契约：总量可以实测，消息/摘要/系统/工具分项始终只是本地估算。
  return {
    used,
    contextWindow,
    promptBudget,
    budgetDelta,
    correction,
    hasSummary: rawSegments.some((segment) => segment.key === 'summary'),
    percent:
      contextWindow && contextWindow > 0
        ? Math.max(0, Math.min(Math.round((used / contextWindow) * 100), 100))
        : null,
    sourceLabel: sourceLabels[String(usage.input_source || '')] || '本地预估',
    segments: barSegments.map(({ visualValue, ...segment }) => ({
      ...segment,
      percent: `${((visualValue / barCapacity) * 100).toFixed(2)}%`
    }))
  }
}
