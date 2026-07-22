import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

for (const configPath of ['../vite.config.ts', '../../web/vite.config.js']) {
  test(`${configPath} records HMR connection close codes`, () => {
    const source = readFileSync(new URL(configPath, import.meta.url), 'utf8')

    assert.match(source, /server\.ws\.on\('connection'/)
    assert.match(source, /socket\.on\('close'/)
    assert.match(source, /disconnected code=\$\{code\}/)
  })
}
