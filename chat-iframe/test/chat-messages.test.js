import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const component = readFileSync(new URL('../src/components/ChatMessages.vue', import.meta.url), 'utf8')

test('context summary renders all extraction items grouped by schema', () => {
  assert.match(component, /contextSummaryItemGroups/, '结构化摘要应按 schema 分组渲染')
  assert.match(component, /<details[\s\S]*class="context-summary-group"/, '分组应支持折叠展示')
  assert.doesNotMatch(component, /contextSummary\.items\.slice\(0,\s*3\)/, '小助手不应只展示前 3 条结构化结果')
})

test('context summary renders supplementary attachments without duplicating their structured results', () => {
  assert.match(component, /supplementaryAttachments/)
  assert.match(component, /class="item-row context-summary-attachment"/)
  assert.match(component, /<strong>摘要<\/strong>/)
  assert.match(component, /if \(!summary\?\.file\.is_main_file\) return \[\]/)
  assert.doesNotMatch(component, /contextSummaryAttachments/)
})

test('SVG artifacts use the existing image preview path', () => {
  assert.match(component, /\(artifact\.name \|\| artifact\.path\)/)
  assert.match(component, /artifactPreview\.kind === 'image'/)
  assert.match(component, /preloadRecentInlineSvgs/)
  assert.match(component, /class="artifact-inline-svg"/)
  assert.match(component, /\(\) => props\.token/)
  assert.match(component, /\[displayItems, \(\) => props\.threadId, \(\) => props\.token\]/)
})

test('streaming auto-scroll observes display item replacement without deep traversal', () => {
  assert.match(
    component,
    /watch\(\[displayItems, showGeneratingStatus, showRunProgress, \(\) => props\.compacting\], scrollToBottom, \{\s*flush: 'post'\s*\}\)/
  )
})
