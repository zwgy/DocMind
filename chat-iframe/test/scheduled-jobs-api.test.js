import assert from 'node:assert/strict'
import test from 'node:test'
import { scheduledJobApi } from '../src/apis/scheduled-jobs.ts'

test('scheduled job api keeps personal management requests under the current token', async () => {
  const calls = []
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ input: String(input), init })
    return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 })
  }

  try {
    await scheduledJobApi.list('ongoing', 'iframe-token', 'cursor-1')
    await scheduledJobApi.changeStatus('sj_1', { action: 'pause', version: 3 }, 'iframe-token')
    await scheduledJobApi.update('sj_1', { version: 3, name: '更新后的提醒' }, 'iframe-token')
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(calls[0].input, '/api/scheduled-jobs?view=ongoing&limit=20&cursor=cursor-1')
  assert.equal(calls[0].init.headers.Authorization, 'Bearer iframe-token')
  assert.equal(calls[1].input, '/api/scheduled-jobs/sj_1/status')
  assert.equal(calls[1].init.method, 'POST')
  assert.deepEqual(JSON.parse(calls[1].init.body), { action: 'pause', version: 3 })
  assert.equal(calls[2].input, '/api/scheduled-jobs/sj_1')
  assert.equal(calls[2].init.method, 'PATCH')
  assert.deepEqual(JSON.parse(calls[2].init.body), { version: 3, name: '更新后的提醒' })
})
