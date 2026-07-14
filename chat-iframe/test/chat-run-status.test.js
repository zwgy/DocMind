import assert from 'node:assert/strict'
import test from 'node:test'

const { readRunEventStream } = await import('../src/apis/chat.ts')

test('retryable worker errors are reported as status instead of final message errors', async () => {
  const statuses = []
  const errors = []
  const response = new Response(
    'event: error\ndata: {"payload":{"chunk":{"status":"error","error_type":"retryable_worker_error","retryable":true,"job_try":2}}}\nid: 1-0\n\n'
  )

  await readRunEventStream(response, {
    onStatus: (chunk) => statuses.push(chunk),
    onChunk: (chunk) => errors.push(chunk)
  })

  assert.equal(statuses[0].retryable, true)
  assert.equal(errors.length, 0)
})
