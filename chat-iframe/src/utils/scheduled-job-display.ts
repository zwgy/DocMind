const WEEKDAY_LABELS: Record<string, string> = {
  '0': '日',
  '1': '一',
  '2': '二',
  '3': '三',
  '4': '四',
  '5': '五',
  '6': '六',
  '7': '日'
}

function customCron(expression: string) {
  return expression ? `自定义周期 · ${expression}` : '自定义周期'
}

function clockTime(hour: string, minute: string) {
  if (!/^\d{1,2}$/.test(hour) || !/^\d{1,2}$/.test(minute)) return null
  const hourValue = Number(hour)
  const minuteValue = Number(minute)
  if (hourValue > 23 || minuteValue > 59) return null
  return `${String(hourValue).padStart(2, '0')}:${String(minuteValue).padStart(2, '0')}`
}

function weekdaySummary(value: string) {
  if (value === '1-5') return '工作日'
  const range = value.match(/^([0-7])-([0-7])$/)
  if (range) return `每周${WEEKDAY_LABELS[range[1]]}至周${WEEKDAY_LABELS[range[2]]}`
  if (!/^[0-7](,[0-7])*$/.test(value)) return null
  return `每周${value.split(',').map((item) => WEEKDAY_LABELS[item]).join('、')}`
}

/** 只翻译语义确定的常见 Cron；复杂表达式保留原值，避免前端给出错误解释。 */
export function describeCron(expression: string | null) {
  const normalized = expression?.trim() || ''
  const fields = normalized.split(/\s+/)
  if (fields.length !== 5) return customCron(normalized)

  const [minute, hour, day, month, weekday] = fields
  const minuteStep = minute.match(/^\*\/(\d+)$/)
  if (minuteStep && hour === '*' && day === '*' && month === '*' && weekday === '*') {
    return `每 ${Number(minuteStep[1])} 分钟`
  }
  const hourStep = hour.match(/^\*\/(\d+)$/)
  if (minute === '0' && hourStep && day === '*' && month === '*' && weekday === '*') {
    return `每 ${Number(hourStep[1])} 小时`
  }

  const time = clockTime(hour, minute)
  if (!time) return customCron(normalized)
  if (day === '*' && month === '*' && weekday === '*') return `每天 ${time}`
  if (day === '*' && month === '*') {
    const weekdayText = weekdaySummary(weekday)
    if (weekdayText) return `${weekdayText} ${time}`
  }
  if (/^\d{1,2}$/.test(day) && month === '*' && weekday === '*') {
    return `每月 ${Number(day)} 日 ${time}`
  }
  if (/^\d{1,2}$/.test(day) && /^\d{1,2}$/.test(month) && weekday === '*') {
    return `每年 ${Number(month)} 月 ${Number(day)} 日 ${time}`
  }
  return customCron(normalized)
}

export function describeInterval(seconds: number | null) {
  const value = Number(seconds || 0)
  if (value > 0 && value % 86400 === 0) return `每 ${value / 86400} 天`
  if (value > 0 && value % 3600 === 0) return `每 ${value / 3600} 小时`
  if (value > 0 && value % 60 === 0) return `每 ${value / 60} 分钟`
  return value > 0 ? `每 ${value} 秒` : '固定间隔'
}
