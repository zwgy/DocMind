import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildCronExpression,
  describeCron,
  describeInterval,
  parseCronEditor,
  toZonedDateTimeInput
} from '../src/utils/scheduled-job-display.ts'

test('scheduled job display translates common cron expressions', () => {
  assert.equal(describeCron('0 17 * * 5'), '每周五 17:00')
  assert.equal(describeCron('0 9 * * 1-5'), '工作日 09:00')
  assert.equal(describeCron('30 8 * * *'), '每天 08:30')
  assert.equal(describeCron('0 10 1 * *'), '每月 1 日 10:00')
  assert.equal(describeCron('*/15 * * * *'), '每 15 分钟')
})

test('scheduled job display keeps complex cron expressions explicit', () => {
  assert.equal(describeCron('0 9 L * *'), '自定义周期 · 0 9 L * *')
})

test('scheduled job display uses an exact interval unit', () => {
  assert.equal(describeInterval(3600), '每 1 小时')
  assert.equal(describeInterval(172800), '每 2 天')
  assert.equal(describeInterval(90), '每 90 秒')
})

test('scheduled job editor restores UTC timestamps in the declared timezone', () => {
  assert.equal(
    toZonedDateTimeInput('2026-08-08T01:30:00+00:00', 'Asia/Shanghai'),
    '2026-08-08T09:30'
  )
  assert.equal(
    toZonedDateTimeInput('2026-08-08T09:30:00+08:00', 'Asia/Shanghai'),
    '2026-08-08T09:30'
  )
})

test('scheduled job editor translates common recurring rules without exposing cron', () => {
  assert.deepEqual(parseCronEditor('35 13 * * 1-5'), {
    rule: 'workdays',
    time: '13:35',
    weekdays: [1, 2, 3, 4, 5]
  })
  assert.deepEqual(parseCronEditor('0 9 L * *'), {
    rule: 'custom',
    time: '09:00',
    weekdays: [1]
  })
  assert.equal(buildCronExpression('weekly', '09:30', [1, 5, 7], ''), '30 09 * * 1,5,0')
})
