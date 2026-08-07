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
  assert.match(
    styles,
    /\.timing-count \{[\s\S]*min-width: 19px;[\s\S]*background: var\(--color-error-700\)/
  )
  assert.match(
    drawerSource,
    /\.tab-count \{[\s\S]*min-width: 20px;[\s\S]*background: var\(--color-error-700\)/
  )
})

test('global ticker polls while visible and opens the matching inbox category on click', () => {
  assert.match(appSource, /class="notification-ticker"/)
  assert.match(appSource, /class="ticker-marquee"[\s\S]*currentTickerItem\.content/)
  assert.match(appSource, /aria-hidden="true"[\s\S]*currentTickerItem\.content/)
  assert.doesNotMatch(appSource, /class="ticker-label"/)
  assert.doesNotMatch(appSource, /currentTickerItem\.title/)
  assert.match(appSource, /setInterval\([\s\S]*30000/)
  assert.match(appSource, /setInterval\([\s\S]*4500/)
  assert.match(appSource, /window\.addEventListener\('focus', refreshVisibleInbox\)/)
  assert.match(appSource, /await inboxApi\.markRead\(item\.category, item\.id/)
  assert.match(appSource, /openScheduledCenter\(item\.category\)/)
  assert.match(appSource, /inboxNavigation\.value = \{ key:/)
  assert.match(drawerSource, /inboxCategory\.value = props\.inboxNavigation\.category\s+if \(props\.open\) void refresh\(\)/)
  assert.match(styles, /\.notification-ticker \{[\s\S]*background: var\(--gray-50\)/)
  assert.match(styles, /grid-template-rows: auto minmax\(0, 1fr\)/)
  assert.match(styles, /\.chat-body > \.workbench \{\s*grid-row: 2/)
  assert.match(styles, /\.ticker-track \{[\s\S]*animation: ticker-marquee 14s linear infinite/)
  assert.match(styles, /@keyframes ticker-marquee/)
})

test('scheduled editor uses the date picker component instead of native date and time controls', () => {
  assert.match(drawerSource, /import \{ VueDatePicker \} from '@vuepic\/vue-datepicker'/)
  assert.equal((drawerSource.match(/<VueDatePicker/g) || []).length, 3)
  assert.match(drawerSource, /model-type="yyyy-MM-dd'T'HH:mm"/)
  assert.match(drawerSource, /model-type="HH:mm"/)
  assert.match(drawerSource, /:action-row="datePickerActionRow"/)
  assert.match(drawerSource, /:input-attrs="datePickerInputAttrs"/)
  assert.match(drawerSource, /:time-config="datePickerTimeConfig"/)
  assert.doesNotMatch(drawerSource, /type="datetime-local"/)
  assert.doesNotMatch(drawerSource, /type="time"/)
})

test('cancel confirmation is centered and uses equal modern actions', () => {
  assert.match(drawerSource, /class="dialog-mask confirm-mask"/)
  assert.match(drawerSource, /class="confirm-icon"[\s\S]*TriangleAlert/)
  assert.match(drawerSource, /class="confirm-actions"/)
  assert.match(drawerSource, /\.dialog-mask \{[\s\S]*align-items: center/)
  assert.match(
    drawerSource,
    /\.confirm-dialog \.confirm-actions \{[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/
  )
  assert.match(drawerSource, /\.confirm-dialog \.danger \{[\s\S]*background: var\(--color-error-700\)/)
})

test('ticker state is owned by App rather than a conversation component', () => {
  assert.match(appSource, /const tickerItems = ref<TickerItem\[]>\(\[]\)/)
  assert.doesNotMatch(
    appSource,
    /watch\(\s*\(\) => chat\.currentThreadId[\s\S]*tickerItems\.value = \[]/
  )
  assert.match(appSource, /watch\(\s*\(\) => context\.config\.token,[\s\S]*resetInboxSnapshot\(\)/)
})
