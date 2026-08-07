import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const drawerSource = readFileSync(
  new URL('../src/components/ScheduledCenterDrawer.vue', import.meta.url),
  'utf8'
)
const styles = readFileSync(new URL('../src/assets/css/app.css', import.meta.url), 'utf8')

test('scheduled editor restores wall-clock time and submits local values with the task timezone', () => {
  assert.match(drawerSource, /toZonedDateTimeInput\(job\.run_at, job\.timezone\)/)
  assert.match(drawerSource, /run_at: form\.runAt/)
  assert.match(drawerSource, /anchor_at: form\.anchorAt/)
  assert.doesNotMatch(drawerSource, /new Date\(form\.(?:runAt|anchorAt)\)\.toISOString\(\)/)
})

test('iframe recurring editor uses human terms and does not expose cron expressions', () => {
  assert.match(drawerSource, />按日\/周重复</)
  assert.match(drawerSource, />每天</)
  assert.match(drawerSource, />工作日</)
  assert.match(drawerSource, />每周</)
  assert.doesNotMatch(drawerSource, />Cron</)
  assert.doesNotMatch(drawerSource, /Cron 表达式/)
  assert.doesNotMatch(drawerSource, /\{\{\s*editForm\.cronExpression\s*\}\}/)
})

test('unread state is layered across the clock entry and inbox tabs', () => {
  assert.match(appSource, /notification_unread_count: 0/)
  assert.match(appSource, /task_unread_count: 0/)
  assert.match(appSource, /class="timing-count"/)
  assert.match(drawerSource, /unreadCounts\?\.total_unread_count/)
  assert.match(drawerSource, /unreadCounts\?\.notification_unread_count/)
  assert.match(drawerSource, /unreadCounts\?\.task_unread_count/)
})

test('global ticker polls while visible and opens the matching inbox category on click', () => {
  assert.match(appSource, /class="notification-ticker"/)
  assert.match(appSource, /setInterval\([\s\S]*30000/)
  assert.match(appSource, /setInterval\([\s\S]*4500/)
  assert.match(appSource, /window\.addEventListener\('focus', refreshVisibleInbox\)/)
  assert.match(appSource, /await inboxApi\.markRead\(item\.category, item\.id/)
  assert.match(appSource, /openScheduledCenter\(item\.category\)/)
  assert.match(appSource, /inboxNavigation\.value = \{ key:/)
  assert.match(styles, /\.notification-ticker \{[\s\S]*background: var\(--gray-50\)/)
  assert.match(styles, /grid-template-rows: auto minmax\(0, 1fr\)/)
  assert.match(styles, /\.chat-body > \.workbench \{\s*grid-row: 2/)
})

test('ticker state is owned by App rather than a conversation component', () => {
  assert.match(appSource, /const tickerItems = ref<TickerItem\[]>\(\[]\)/)
  assert.doesNotMatch(
    appSource,
    /watch\(\s*\(\) => chat\.currentThreadId[\s\S]*tickerItems\.value = \[]/
  )
  assert.match(appSource, /watch\(\s*\(\) => context\.config\.token,[\s\S]*resetInboxSnapshot\(\)/)
})
