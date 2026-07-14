import assert from 'node:assert/strict'
import test from 'node:test'

const { createTrustedParentMessageGuard } = await import('../src/utils/iframe-message.ts')

test('first init locks the parent window and origin', () => {
  const parent = {}
  const other = {}
  const guard = createTrustedParentMessageGuard(parent)

  assert.equal(guard({ source: parent, origin: 'https://host.example.com' }, { type: 'PAGE_CONTENT' }), false)
  assert.equal(guard({ source: other, origin: 'https://host.example.com' }, { type: 'INIT_CONFIG' }), false)
  assert.equal(guard({ source: parent, origin: 'https://host.example.com' }, { type: 'INIT_CONFIG' }), true)
  assert.equal(guard({ source: parent, origin: 'https://host.example.com' }, { type: 'INIT_CONFIG' }), false)
  assert.equal(guard({ source: other, origin: 'https://host.example.com' }, { type: 'PAGE_CONTENT' }), false)
  assert.equal(guard({ source: parent, origin: 'https://evil.example.com' }, { type: 'PAGE_CONTENT' }), false)
  assert.equal(guard({ source: parent, origin: 'https://host.example.com' }, { type: 'PAGE_CONTENT' }), true)
})
