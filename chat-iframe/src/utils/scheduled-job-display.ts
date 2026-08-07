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

export type CronEditorRule = 'daily' | 'workdays' | 'weekly' | 'custom'

export function parseCronEditor(expression: string | null): {
  rule: CronEditorRule
  time: string
  weekdays: number[]
} {
  const normalized = expression?.trim() || ''
  const match = normalized.match(/^(\d{1,2}) (\d{1,2}) \* \* (\*|1-5|[0-7](?:,[0-7])*)$/)
  if (!match) return { rule: 'custom', time: '09:00', weekdays: [1] }
  const time = clockTime(match[2], match[1])
  if (!time) return { rule: 'custom', time: '09:00', weekdays: [1] }
  if (match[3] === '*') return { rule: 'daily', time, weekdays: [1] }
  if (match[3] === '1-5') return { rule: 'workdays', time, weekdays: [1, 2, 3, 4, 5] }
  const weekdays = [...new Set(match[3].split(',').map((value) => Number(value) || 7))].sort((a, b) => a - b)
  return { rule: 'weekly', time, weekdays }
}

export function buildCronExpression(
  rule: CronEditorRule,
  time: string,
  weekdays: number[],
  originalExpression: string
) {
  if (rule === 'custom') return originalExpression.trim()
  const match = time.match(/^(\d{2}):(\d{2})$/)
  if (!match || !clockTime(match[1], match[2])) throw new Error('请选择有效的重复提醒时间')
  const [, hour, minute] = match
  if (rule === 'daily') return `${minute} ${hour} * * *`
  if (rule === 'workdays') return `${minute} ${hour} * * 1-5`
  const normalizedWeekdays = [...new Set(weekdays)]
    .filter((value) => value >= 1 && value <= 7)
    .sort((a, b) => a - b)
    .map((value) => value === 7 ? 0 : value)
  if (!normalizedWeekdays.length) throw new Error('请至少选择一个星期')
  return `${minute} ${hour} * * ${normalizedWeekdays.join(',')}`
}

/** 后端时间使用 UTC 持久化；编辑器必须按任务声明的时区还原墙钟时间。 */
export function toZonedDateTimeInput(value: string | null, timezone: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23'
    }).formatToParts(date).map((part) => [part.type, part.value])
  )
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}
