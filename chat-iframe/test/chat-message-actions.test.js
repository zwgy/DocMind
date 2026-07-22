import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/components/ChatMessages.vue', import.meta.url), 'utf8')
const refsSource = readFileSync(new URL('../src/components/MessageRefs.vue', import.meta.url), 'utf8')
const styles = readFileSync(new URL('../src/assets/css/app.css', import.meta.url), 'utf8')

test('user message actions keep copy, image preview and attachment state visible', () => {
  assert.match(source, /copyToClipboard\(message\.content\)/)
  assert.match(refsSource, /copyToClipboard\(text\)/)
  assert.match(source, /window\.addEventListener\('keydown', closeImagePreviewOnEscape\)/)
  assert.match(source, /event\.key === 'Escape'/)
  assert.match(source, /role="dialog" aria-modal="true" aria-label="图片预览"/)
  assert.match(source, /formatFileSize\(attachment\.file_size\)/)
  assert.match(source, /attachmentStatus\(attachment\.status\)/)
})

test('user copy action stays reachable outside the bubble and appears on hover or keyboard focus', () => {
  assert.match(source, /class="user-message-copy"[\s\S]*?<\/button>\s*<div class="message-content">/)
  assert.match(styles, /\.chat-message\.user:hover \.user-message-copy,\s*\.user-message-copy:focus-visible/)
  assert.match(styles, /right: calc\(100% \+ 8px\)/)
  assert.match(styles, /opacity: 0/)
  assert.doesNotMatch(styles, /\.user-message-copy\s*\{[^}]*pointer-events:\s*none/)
  assert.doesNotMatch(source, /重新生成/)
})
