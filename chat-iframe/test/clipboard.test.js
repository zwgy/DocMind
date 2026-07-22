import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/utils/clipboard.ts', import.meta.url), 'utf8')

test('clipboard helper falls back to the native copy command and reports its real result', () => {
  assert.match(source, /navigator\.clipboard\?\.writeText/)
  assert.match(source, /await navigator\.clipboard\.writeText\(text\)/)
  assert.match(source, /document\.execCommand\('copy'\)/)
  assert.match(source, /return copied/)
})
