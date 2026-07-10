import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

test('refreshExtraction waits for token before querying extraction api', () => {
  assert.match(source, /if \(!context\.config\.token\) \{/)
  assert.match(source, /避免无凭证请求把摘要卡片打成 401/)
  assert.match(source, /void refreshExtraction\(\)/)
})
