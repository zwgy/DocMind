import assert from 'node:assert/strict'
import test from 'node:test'
import {
  describeCron,
  describeInterval,
  toZonedDateTimeInput
} from '../src/utils/scheduledJobDisplay.js'

test('web scheduled job display matches readable schedule semantics', () => {
  assert.equal(describeCron('0 17 * * 5'), '每周五 17:00')
  assert.equal(describeCron('0 9 * * 1-5'), '工作日 09:00')
  assert.equal(describeCron('0 9 L * *'), '自定义周期 · 0 9 L * *')
  assert.equal(describeInterval(7200), '每 2 小时')
  assert.equal(
    toZonedDateTimeInput('2026-08-08T01:30:00+00:00', 'Asia/Shanghai'),
    '2026-08-08T09:30'
  )
})
