import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/components/ChatMessages.vue', import.meta.url), 'utf8')

test('user message actions keep copy, image preview and attachment state visible', () => {
  assert.match(source, /navigator\.clipboard\?\.writeText\(message\.content\)/)
  assert.match(source, /window\.addEventListener\('keydown', closeImagePreviewOnEscape\)/)
  assert.match(source, /event\.key === 'Escape'/)
  assert.match(source, /role="dialog" aria-modal="true" aria-label="图片预览"/)
  assert.match(source, /formatFileSize\(attachment\.file_size\)/)
  assert.match(source, /attachmentStatus\(attachment\.status\)/)
})
