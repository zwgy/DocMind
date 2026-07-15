import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const { setActivePinia, createPinia } = await import('pinia')
const { listMessages } = await import('../src/apis/chat.ts')
const { useChatStore } = await import('../src/stores/chat.ts')
const messageRefsSource = readFileSync(new URL('../src/components/MessageRefs.vue', import.meta.url), 'utf8')

test('dislike opens and focuses the optional reason field', () => {
  assert.match(messageRefsSource, /@click="openDislike"/)
  assert.match(messageRefsSource, /nextTick\(\(\) => dislikeReasonRef\.value\?\.focus\(\)\)/)
  assert.match(messageRefsSource, /ref="dislikeReasonRef"/)
})

test('history feedback is normalized and a second click does not submit again', async () => {
  let feedbackPosts = 0
  globalThis.fetch = async (url) => {
    if (url === '/api/chat/thread/thread-1/history') {
      return Response.json({
        history: [{ id: '42', type: 'ai', content: 'answer', feedback: { rating: 'dislike', reason: 'not specific' } }]
      })
    }
    if (url === '/api/chat/message/43/feedback') {
      feedbackPosts += 1
      return Response.json({ rating: 'like', reason: null })
    }
    return Response.json({})
  }

  const history = await listMessages('thread-1', 'token-1')
  assert.deepEqual(history[0].feedback, { rating: 'dislike', reason: 'not specific' })

  setActivePinia(createPinia())
  const chat = useChatStore()
  chat.ensureRuntime().messages = [{ id: '43', role: 'assistant', content: 'new answer', status: 'done' }]
  const first = chat.feedback({ messageId: '43', rating: 'like', reason: null }, 'token-1')
  const second = chat.feedback({ messageId: '43', rating: 'like', reason: null }, 'token-1')
  await Promise.all([first, second])

  assert.equal(feedbackPosts, 1)
  assert.deepEqual(chat.messages[0].feedback, { rating: 'like', reason: null })
})
