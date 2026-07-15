import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const contextSource = readFileSync(new URL('../src/stores/iframe-context.ts', import.meta.url), 'utf8')
const inputSource = readFileSync(new URL('../src/components/ChatInput.vue', import.meta.url), 'utf8')
const messagesSource = readFileSync(new URL('../src/components/ChatMessages.vue', import.meta.url), 'utf8')

test('refreshExtraction waits for token before querying extraction api', () => {
  assert.match(source, /if \(!context\.config\.token\) \{/)
  assert.match(source, /避免无凭证请求把摘要卡片打成 401/)
  assert.match(source, /void refreshExtraction\(\)/)
})

test('page attachments are synchronized only after the user selects them for a sent question', () => {
  assert.doesNotMatch(contextSource, /normalized\[0\]\.selected/)
  assert.doesNotMatch(inputSource, /if \(!next\.size && props\.selectedPageFileId\)/)
  assert.match(source, /refreshExtraction\(selectedPageFiles, true\)/)
})

test('structured extraction items are folded by default', () => {
  assert.doesNotMatch(messagesSource, /class="context-summary-group"\s+open/)
})
