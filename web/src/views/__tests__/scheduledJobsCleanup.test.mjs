import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const scheduledView = readFileSync(new URL('../ScheduledJobsView.vue', import.meta.url), 'utf8')
const inboxDrawer = readFileSync(
  new URL('../../components/inbox/InboxDrawer.vue', import.meta.url),
  'utf8'
)
const scheduledApi = readFileSync(
  new URL('../../apis/scheduled_job_api.js', import.meta.url),
  'utf8'
)

test('web separates personal and incoming management cleanup semantics', () => {
  assert.match(scheduledView, /我的定时/)
  assert.match(scheduledView, /来文任务/)
  assert.match(scheduledView, /结果会话不会被删除/)
  assert.match(scheduledView, /删除后仅对当前账号隐藏/)
  assert.match(scheduledApi, /\/api\/scheduled-jobs\/incoming/)
  assert.match(scheduledApi, /job\.source_type === 'personal'/)
})

test('web inbox exposes confirmed single cleanup and delete-read actions', () => {
  assert.match(inboxDrawer, />删除已读</)
  assert.match(inboxDrawer, /未读记录和结果会话不会受影响/)
  assert.match(inboxDrawer, /@confirm="removeItem\(item\)"/)
  assert.match(inboxDrawer, /@confirm="clearRead"/)
  assert.match(inboxDrawer, /class="drawer-actions"/)
  assert.match(inboxDrawer, /class="mark-all-read-button"/)
  assert.match(inboxDrawer, /<CheckCheck :size="15" \/>全部已读/)
  assert.match(inboxDrawer, /class="delete-read-button"/)
  assert.match(inboxDrawer, /<Trash2 :size="15" \/>删除已读/)
})

test('web task inbox keeps result, source and cleanup actions in one consistent row', () => {
  assert.match(inboxDrawer, /function sourceThreadId\(item\)/)
  assert.match(inboxDrawer, /class="inbox-actions"/)
  assert.match(inboxDrawer, /<MessageSquareText :size="14" \/>查看来源/)
  assert.match(inboxDrawer, /router\.push\(\{ name: 'AgentCompWithThreadId'/)
  assert.match(inboxDrawer, /type="text" size="small" danger/)
})

test('web personal notification history omits body content duplicated in inbox', () => {
  assert.match(
    scheduledView,
    /store\.sourceType === 'personal'[\s\S]*store\.activeView === 'history'[\s\S]*job\.action_type === 'notification'/
  )
})
