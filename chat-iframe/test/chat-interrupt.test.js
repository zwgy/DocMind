import assert from 'node:assert/strict'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { useChatStore } = await import('../src/stores/chat.ts')

test('interrupt answer resumes the original parent run', async () => {
  setActivePinia(createPinia())
  let resumePayload
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/agent/runs' && options.method === 'POST') {
      resumePayload = JSON.parse(options.body)
      return Response.json({ id: 'resume-run' })
    }
    if (url === '/api/agent/thread/thread-1/active_run')
      return Response.json({ run: { id: 'resume-run', status: 'running' } })
    if (url === '/api/agent/runs/resume-run') return Response.json({ run: { status: 'running' } })
    if (url.startsWith('/api/agent/runs/resume-run/events')) {
      return new Response('event: end\ndata: {"payload":{"status":"completed"}}\nid: 1-0\n\n')
    }
    if (url === '/api/chat/thread/thread-1/history') return Response.json({ history: [] })
    return Response.json({})
  }

  const chat = useChatStore()
  chat.currentThreadId = 'thread-1'
  const runtime = chat.ensureRuntime('thread-1')
  runtime.activeRunId = 'parent-run'
  runtime.isSending = true
  runtime.isStreaming = true
  chat.consumeRunStatus(runtime, {
    status: 'ask_user_question_required',
    questions: [{ question_id: 'q-1', question: 'Continue?', options: ['yes', 'no'] }]
  })

  assert.equal(runtime.isStreaming, false)
  assert.equal(runtime.pendingInterrupt?.parentRunId, 'parent-run')

  await chat.submitInterrupt('thread-1', { 'q-1': 'yes' }, 'token-1', 'agent-1')

  assert.equal(resumePayload.parent_run_id, 'parent-run')
  assert.deepEqual(resumePayload.resume, { 'q-1': 'yes' })
  assert.equal(runtime.pendingInterrupt, null)
})

test('context compaction stream event only changes the waiting status', () => {
  setActivePinia(createPinia())
  const chat = useChatStore()
  const runtime = chat.ensureRuntime('thread-1')

  chat.consumeRunStatus(runtime, {
    status: 'stream_event',
    event: {
      method: 'custom',
      data: { type: 'context_compaction', status: 'started' }
    }
  })
  assert.equal(runtime.isCompacting, true)

  chat.consumeRunStatus(runtime, {
    status: 'stream_event',
    event: {
      method: 'custom',
      data: { type: 'context_compaction', status: 'finished' }
    }
  })
  assert.equal(runtime.isCompacting, false)
  assert.equal(runtime.messages.length, 0)
})

test('interrupted run restores checkpoint questions instead of replaying its finished stream', async () => {
  setActivePinia(createPinia())
  const calls = []
  globalThis.fetch = async (url) => {
    calls.push(url)
    if (url === '/api/agent/thread/thread-1/active_run') {
      return Response.json({ run: { id: 'parent-run', status: 'interrupted' } })
    }
    if (url === '/api/chat/thread/thread-1/state') {
      return Response.json({
        interrupt: {
          status: 'ask_user_question_required',
          run_id: 'parent-run',
          questions: [{ question_id: 'q-1', question: 'Continue?', options: ['yes', 'no'] }]
        }
      })
    }
    throw new Error(`unexpected request: ${url}`)
  }

  const chat = useChatStore()
  await chat.resumeActiveRun('thread-1', 'token-1')

  const runtime = chat.threadRuntimes['thread-1']
  assert.equal(runtime.pendingInterrupt?.parentRunId, 'parent-run')
  assert.equal(runtime.isStreaming, false)
  assert.equal(calls.some((url) => url.includes('/events')), false)
})
