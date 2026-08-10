import assert from 'node:assert/strict'
import test from 'node:test'

const { createPinia, setActivePinia } = await import('pinia')
const { listConversations } = await import('../src/apis/chat.ts')
const { useChatStore } = await import('../src/stores/chat.ts')

test('conversation list includes scheduled runs outside the host scope', async () => {
  let requestedUrl = ''
  globalThis.fetch = async (url) => {
    requestedUrl = String(url)
    return Response.json([])
  }

  await listConversations('token-1', 'host-agent', 'incoming:document:1')

  const query = new URL(requestedUrl, 'http://test').searchParams
  assert.equal(query.get('agent_id'), 'host-agent')
  assert.equal(query.get('conversation_scope_key'), 'incoming:document:1')
  assert.equal(query.get('include_scheduled_runs'), 'true')
})

test('continuing a scheduled conversation uses the conversation agent', async () => {
  setActivePinia(createPinia())
  let runPayload
  globalThis.fetch = async (url, options = {}) => {
    if (url === '/api/agent/runs' && options.method === 'POST') {
      runPayload = JSON.parse(options.body)
      return Response.json({ id: 'run-scheduled-follow-up' })
    }
    if (String(url).startsWith('/api/agent/runs/run-scheduled-follow-up/events')) {
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                `event: end\ndata: ${JSON.stringify({ run_id: 'run-scheduled-follow-up' })}\n\n`
              )
            )
            controller.close()
          }
        }),
        { status: 200, headers: { 'content-type': 'text/event-stream' } }
      )
    }
    return Response.json({})
  }

  const chat = useChatStore()
  chat.threads = [
    {
      id: 'scheduled-thread',
      agent_id: 'scheduled-report-agent',
      thread_kind: 'scheduled_run'
    }
  ]
  chat.currentThreadId = 'scheduled-thread'
  chat.ensureRuntime('scheduled-thread')

  await chat.send({ text: '继续分析', files: [] }, 'token-1', 'host-page-agent', 'host-scope')

  assert.equal(runPayload.agent_id, 'scheduled-report-agent')
  assert.equal(runPayload.thread_id, 'scheduled-thread')
})
