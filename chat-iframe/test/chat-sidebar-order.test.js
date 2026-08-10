import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const sidebarSource = readFileSync(
  new URL('../src/components/ChatSidebar.vue', import.meta.url),
  'utf8'
)

test('history sidebar preserves server order and only scrolls to reveal the current thread', () => {
  assert.match(sidebarSource, /v-for="thread in threads"/)
  assert.doesNotMatch(sidebarSource, /displayThreads/)
  assert.doesNotMatch(sidebarSource, /\.sort\(/)
  assert.match(sidebarSource, /querySelector<HTMLElement>\('\.thread-option\.active'\)/)
  assert.match(
    sidebarSource,
    /list\.scrollTop \+= threadRect\.top - listRect\.top - topGap/
  )
})
