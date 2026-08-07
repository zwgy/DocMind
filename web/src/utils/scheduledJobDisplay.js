const WEEKDAY_LABELS = { 0: '日', 1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '日' }

function customCron(expression) {
  return expression ? `自定义周期 · ${expression}` : '自定义周期'
}

function clockTime(hour, minute) {
  if (!/^\d{1,2}$/.test(hour) || !/^\d{1,2}$/.test(minute)) return null
  const hourValue = Number(hour)
  const minuteValue = Number(minute)
  if (hourValue > 23 || minuteValue > 59) return null
  return `${String(hourValue).padStart(2, '0')}:${String(minuteValue).padStart(2, '0')}`
}

function weekdaySummary(value) {
  if (value === '1-5') return '工作日'
  const range = value.match(/^([0-7])-([0-7])$/)
  if (range) return `每周${WEEKDAY_LABELS[range[1]]}至周${WEEKDAY_LABELS[range[2]]}`
  if (!/^[0-7](,[0-7])*$/.test(value)) return null
  return `每周${value
    .split(',')
    .map((item) => WEEKDAY_LABELS[item])
    .join('、')}`
}

/** Web 与小助手只解释语义确定的常见 Cron，复杂表达式保留原值。 */
export function describeCron(expression) {
  const normalized = expression?.trim() || ''
  const fields = normalized.split(/\s+/)
  if (fields.length !== 5) return customCron(normalized)
  const [minute, hour, day, month, weekday] = fields
  const minuteStep = minute.match(/^\*\/(\d+)$/)
  if (minuteStep && hour === '*' && day === '*' && month === '*' && weekday === '*')
    return `每 ${Number(minuteStep[1])} 分钟`
  const hourStep = hour.match(/^\*\/(\d+)$/)
  if (minute === '0' && hourStep && day === '*' && month === '*' && weekday === '*')
    return `每 ${Number(hourStep[1])} 小时`
  const time = clockTime(hour, minute)
  if (!time) return customCron(normalized)
  if (day === '*' && month === '*' && weekday === '*') return `每天 ${time}`
  if (day === '*' && month === '*') {
    const weekdayText = weekdaySummary(weekday)
    if (weekdayText) return `${weekdayText} ${time}`
  }
  if (/^\d{1,2}$/.test(day) && month === '*' && weekday === '*')
    return `每月 ${Number(day)} 日 ${time}`
  if (/^\d{1,2}$/.test(day) && /^\d{1,2}$/.test(month) && weekday === '*')
    return `每年 ${Number(month)} 月 ${Number(day)} 日 ${time}`
  return customCron(normalized)
}

export function describeInterval(seconds) {
  const value = Number(seconds || 0)
  if (value > 0 && value % 86400 === 0) return `每 ${value / 86400} 天`
  if (value > 0 && value % 3600 === 0) return `每 ${value / 3600} 小时`
  if (value > 0 && value % 60 === 0) return `每 ${value / 60} 分钟`
  return value > 0 ? `每 ${value} 秒` : '固定间隔'
}

/** 后端时间使用 UTC 持久化；编辑器必须按任务声明的时区还原墙钟时间。 */
export function toZonedDateTimeInput(value, timezone) {
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
    })
      .formatToParts(date)
      .map((part) => [part.type, part.value])
  )
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}
