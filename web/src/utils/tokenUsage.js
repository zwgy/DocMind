const TOKEN_COUNT_K_UNIT = 1024

const nonNegativeNumber = (value) => {
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : null
}

const finiteNumber = (value) => {
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

export const formatTokenCount = (value) => {
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

export const buildTokenUsageView = (usage) => {
  if (!usage || typeof usage !== 'object' || Array.isArray(usage)) return null
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
      ? usage.breakdown_estimate
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
  const sourceLabels = {
    provider_usage: '实际用量',
    calibrated_estimate: '校准后的估算',
    fallback_estimate: '首次预估'
  }

  // 总量采用模型实测/校准值，分项只解释本地估算构成，不能把差额摊回某一项冒充精确值。
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
    sourceLabel: sourceLabels[usage.input_source] || '本地预估',
    segments: rawSegments.map((segment) => ({
      ...segment,
      percent: `${((segment.value / compositionTotal) * 100).toFixed(2)}%`,
      valueLabel: `${formatTokenCount(segment.value)} Token`,
      tone: `is-${segment.key}`
    }))
  }
}
