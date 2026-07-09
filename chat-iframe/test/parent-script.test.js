import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import vm from 'node:vm'

function loadScript(overrides = {}) {
  const source = readFileSync(join(import.meta.dirname, '../public/docmind-chat-iframe-parent.js'), 'utf8')
  const sandboxWindow = overrides.window || {}
  const sandboxDocument = overrides.document || { title: 'test page', location: { href: 'http://page.local' } }
  sandboxWindow.window = sandboxWindow
  sandboxWindow.document = sandboxDocument
  sandboxWindow.location = sandboxWindow.location || sandboxDocument.location || { href: 'http://page.local' }
  const sandbox = {
    window: sandboxWindow,
    document: sandboxDocument,
    location: sandboxWindow.location,
    console
  }
  vm.runInNewContext(source, sandbox)
  return sandbox.window.DocMindChatIframe
}

function parentHarness(options = {}) {
  const sentMessages = []
  const listeners = {}
  const iframe = {
    src: '',
    style: {},
    contentWindow: {
      postMessage(message, targetOrigin) {
        sentMessages.push({ message, targetOrigin })
      }
    }
  }
  const container = {
    className: '',
    innerHTML: '',
    offsetLeft: 10,
    offsetTop: 20,
    style: {},
    parentNode: { removeChild() {} },
    getBoundingClientRect() {
      return { left: 100, top: 50, width: 460, height: 680 }
    },
    querySelector(selector) {
      if (selector === 'iframe') {
        return iframe
      }
      return { addEventListener() {} }
    }
  }
  const doc = {
    title: 'production page',
    documentElement: { outerHTML: '<html></html>' },
    body: { appendChild() {} },
    createElement() {
      return container
    },
    addEventListener() {},
    removeEventListener() {},
    querySelectorAll() {
      return []
    }
  }
  const win = {
    document: doc,
    location: { href: 'https://production.example.com/page' },
    innerWidth: 900,
    innerHeight: 700,
    addEventListener(type, callback) {
      listeners[type] = callback
    },
    removeEventListener() {}
  }
  if (options.fetch) win.fetch = options.fetch
  const DocMindChatIframe = loadScript({ window: win, document: doc })
  return { container, iframe, DocMindChatIframe, listeners, sentMessages }
}

function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

test('accepts iframe messages by targetOrigin and keeps originAllowlist for iframe side', () => {
  const { DocMindChatIframe, listeners, sentMessages } = parentHarness({
    fetch: async () => ({ ok: true, json: async () => ({ access_token: 'test-token' }) })
  })

  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    originAllowlist: ['https://production.example.com'],
    includeFiles: false,
    source_system: 'oa',
    function_id: 'contract',
    business_id: '001',
    external_user_id: '1001',
    external_user_name: '张三'
  })
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'IFRAME_READY' } })

  return tick().then(() => {
    assert.equal(sentMessages[0].message.type, 'INIT_CONFIG')
    assert.deepEqual(sentMessages[0].message.payload.originAllowlist, ['https://production.example.com'])
    assert.equal(sentMessages[0].targetOrigin, 'https://docmind.example.com')
    chat.destroy()
  })
})

test('fetches docMind iframe token and sends conversation scope to iframe', async () => {
  const requests = []
  const { DocMindChatIframe, listeners, sentMessages } = parentHarness({
    fetch: async (url, options) => {
      requests.push({ url, options })
      return { ok: true, json: async () => ({ access_token: 'iframe-token' }) }
    }
  })

  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    apiBaseUrl: 'https://docmind.example.com',
    includeFiles: false,
    source_system: 'oa',
    function_id: 'contractApproval',
    business_id: 'contract-20260706-001',
    external_user_id: '1001',
    external_user_name: '张三'
  })
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'IFRAME_READY' } })
  await tick()

  assert.equal(requests[0].url, 'https://docmind.example.com/api/chat-iframe/token')
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    source_system: 'oa',
    external_user_id: '1001',
    external_user_name: '张三'
  })
  assert.equal(sentMessages[0].message.payload.token, 'iframe-token')
  assert.equal(sentMessages[0].message.payload.conversationScopeKey, 'oa:contractApproval:contract-20260706-001')
  chat.destroy()
})

test('uses trusted tokenExchangeUrl mode when configured', async () => {
  const requests = []
  const { DocMindChatIframe, listeners, sentMessages } = parentHarness({
    fetch: async (url, options) => {
      requests.push({ url, options })
      return { ok: true, json: async () => ({ access_token: 'backend-token' }) }
    }
  })

  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    tokenExchangeUrl: 'https://oa.example.com/docmind/token',
    includeFiles: false,
    source_system: 'oa',
    function_id: 'contract',
    business_id: '001',
    external_user_id: '1001',
    external_user_name: '张三'
  })
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'IFRAME_READY' } })
  await tick()

  assert.equal(requests[0].url, 'https://oa.example.com/docmind/token')
  assert.equal(requests[0].options.credentials, 'include')
  assert.equal(sentMessages[0].message.payload.token, 'backend-token')
  chat.destroy()
})

test('setFiles and explicit requests send compatible iframe messages', () => {
  const { DocMindChatIframe, listeners, sentMessages } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    includeFiles: false,
    source_system: 'oa',
    function_id: 'incomingDocument',
    business_id: '37906'
  })

  chat.setPageContent('page text')
  chat.setFiles([{ id: 'source-1', name: 'incoming.docx', source_url: 'https://oa/files/source-1' }])
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'REQUEST_PAGE_CONTENT' } })
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'REQUEST_FILE_LIST' } })

  assert.equal(sentMessages.at(-4).message.type, 'PAGE_CONTENT')
  assert.deepEqual(JSON.parse(JSON.stringify(sentMessages.at(-4).message.payload)), { text: 'page text' })
  assert.equal(sentMessages.at(-3).message.type, 'PAGE_FILES_UPDATED')
  assert.equal(sentMessages.at(-3).message.payload[0].source_url, 'https://oa/files/source-1')
  assert.equal(sentMessages.at(-3).message.payload[0].source_system, 'oa')
  assert.equal(sentMessages.at(-3).message.payload[0].source_function_id, 'incomingDocument')
  assert.equal(sentMessages.at(-3).message.payload[0].source_doc_id, '37906')
  assert.equal(sentMessages.at(-2).message.type, 'PAGE_CONTENT')
  assert.equal(sentMessages.at(-1).message.type, 'FILE_LIST')
  chat.destroy()
})

test('window control messages update state and emit optional chat callbacks', () => {
  const { container, DocMindChatIframe, listeners } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    includeFiles: false
  })
  const events = []
  chat.on('stateChange', (payload) => events.push(['state', payload.state]))
  chat.on('conversationCreated', (payload) => events.push(['conversation', payload.conversationId]))
  chat.on('messageSent', (payload) => events.push(['message', payload.messageId]))

  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'MAXIMIZE' } })
  listeners.message({
    origin: 'https://docmind.example.com',
    data: { type: 'CONVERSATION_CREATED', payload: { conversationId: 'thread-1' } }
  })
  listeners.message({
    origin: 'https://docmind.example.com',
    data: { type: 'MESSAGE_SENT', payload: { messageId: 'message-1' } }
  })

  assert.match(container.className, /maximized/)
  assert.deepEqual(events, [
    ['state', 'maximized'],
    ['conversation', 'thread-1'],
    ['message', 'message-1']
  ])
  chat.destroy()
})

test('closed state keeps restore entry and drag cleanup releases iframe pointer events', () => {
  const { container, iframe, DocMindChatIframe } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    includeFiles: false
  })

  chat.close()
  assert.match(container.className, /closed/)

  chat._startDrag({
    clientX: 10,
    clientY: 20,
    pointerId: 1,
    currentTarget: {},
    preventDefault() {}
  })
  chat._moveDrag({ clientX: 30, clientY: 45 })
  assert.equal(container.style.left, '30px')
  assert.equal(container.style.top, '45px')
  assert.equal(iframe.style.pointerEvents, 'none')

  chat._endDrag()
  assert.equal(chat.drag, null)
  assert.equal(iframe.style.pointerEvents, '')
  chat.destroy()
})

test('parent shell uses viewport-bounded normal size and default AI restore button', () => {
  const { container, DocMindChatIframe } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    includeFiles: false,
    width: 460,
    height: 680,
    offsetX: 24,
    offsetY: 24
  })

  assert.match(container.innerHTML, /width:min\(460px,calc\(100vw - 48px\)\)/)
  assert.match(container.innerHTML, /height:min\(680px,calc\(100vh - 48px\)\)/)
  assert.match(container.innerHTML, /border-radius:50%/)
  assert.match(container.innerHTML, /docmind-chat-mark/)
  assert.match(container.innerHTML, /font:900 25px\/1 Arial/)
  assert.match(container.innerHTML, /linear-gradient\(135deg,#2563eb 0%,#06b6d4 56%,#14b8a6 100%\)/)
  assert.match(container.innerHTML, />AI</)
  chat.destroy()
})

test('local example starts minimized so users open the assistant explicitly', () => {
  const example = readFileSync(join(import.meta.dirname, '../public/example.html'), 'utf8')

  assert.match(example, /initialState:\s*'minimized'/)
})

test('iframe header drag messages move the parent window', () => {
  const { container, iframe, DocMindChatIframe, listeners } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    includeFiles: false
  })

  listeners.message({
    origin: 'https://docmind.example.com',
    data: { type: 'WINDOW_DRAG_START', payload: { clientX: 12, clientY: 8, screenX: 500, screenY: 400, pointerId: 9 } }
  })
  assert.equal(iframe.style.pointerEvents, undefined)

  listeners.message({
    origin: 'https://docmind.example.com',
    data: { type: 'WINDOW_DRAG_MOVE', payload: { clientX: 32, clientY: 38, screenX: 520, screenY: 430, pointerId: 9 } }
  })
  assert.equal(container.style.left, '30px')
  assert.equal(container.style.top, '50px')

  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'WINDOW_DRAG_END' } })
  assert.equal(chat.drag, null)
  assert.equal(iframe.style.pointerEvents, undefined)
  chat.destroy()
})

test('restore keeps normal window inside viewport after floating button drag', () => {
  const { container, DocMindChatIframe } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    includeFiles: false,
    width: 460,
    height: 680
  })

  container.offsetLeft = 760
  container.offsetTop = 620
  chat.restore()

  assert.equal(container.style.left, '416px')
  assert.equal(container.style.top, '12px')
  assert.equal(container.style.right, 'auto')
  assert.equal(container.style.bottom, 'auto')
  chat.destroy()
})

test('closing from normal clears inline placement for floating icon corner', () => {
  const { container, DocMindChatIframe } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    includeFiles: false,
    width: 460,
    height: 680
  })

  chat.restore()
  assert.equal(container.style.left, '416px')
  assert.equal(container.style.top, '12px')

  chat.close()
  assert.equal(container.style.left, '')
  assert.equal(container.style.top, '')
  assert.equal(container.style.right, '')
  assert.equal(container.style.bottom, '')
  assert.match(container.className, /closed/)
  chat.destroy()
})
