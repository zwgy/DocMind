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

function attachmentElement() {
  const sizeNode = { textContent: '-200.16KB' }
  const link = {
    textContent: '"incoming-2026-162.pdf"-200.16KB',
    href: '###',
    getAttribute(name) {
      return name === 'onclick'
        ? "YZSoft.File.download('http://10.132.235.62:8082/YZSoft/Attachment/dafault.ashx?202606100417')"
        : null
    },
    querySelector(selector) {
      return selector === '.size' ? sizeNode : null
    },
    closest(selector) {
      return selector === '.item[attachment]' ? item : null
    }
  }
  const item = {
    id: '202606100417_BOX',
    getAttribute(name) {
      return name === 'attachment' ? '202606100417' : null
    }
  }
  return link
}

function parentHarness() {
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
  const DocMindChatIframe = loadScript({ window: win, document: doc })
  return { container, iframe, DocMindChatIframe, listeners, sentMessages }
}

test('extracts production YZSoft attachment DOM', () => {
  const DocMindChatIframe = loadScript()
  const files = DocMindChatIframe.extractFilesFromDocument({
    querySelectorAll(selector) {
      return selector === '.items .item[attachment] a' ? [attachmentElement()] : []
    }
  })

  assert.equal(files.length, 1)
  assert.deepEqual(JSON.parse(JSON.stringify(files[0])), {
    id: '202606100417',
    name: 'incoming-2026-162.pdf',
    sourceUrl: 'http://10.132.235.62:8082/YZSoft/Attachment/dafault.ashx?202606100417',
    url: 'http://10.132.235.62:8082/YZSoft/Attachment/dafault.ashx?202606100417',
    sourceKey: '202606100417',
    sizeText: '200.16KB',
    onclick:
      "YZSoft.File.download('http://10.132.235.62:8082/YZSoft/Attachment/dafault.ashx?202606100417')",
    type: 'document',
    selected: true
  })
})

test('accepts iframe messages by targetOrigin and keeps originAllowlist for iframe side', () => {
  const { DocMindChatIframe, listeners, sentMessages } = parentHarness()

  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    originAllowlist: ['https://production.example.com'],
    includeFiles: false
  })
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'IFRAME_READY' } })

  assert.equal(sentMessages[0].message.type, 'INIT_CONFIG')
  assert.deepEqual(sentMessages[0].message.payload.originAllowlist, ['https://production.example.com'])
  assert.equal(sentMessages[0].targetOrigin, 'https://docmind.example.com')
  chat.destroy()
})

test('setFiles and explicit requests send compatible iframe messages', () => {
  const { DocMindChatIframe, listeners, sentMessages } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    targetOrigin: 'https://docmind.example.com',
    includeFiles: false
  })

  chat.setPageContent('page text')
  chat.setFiles([{ id: 'source-1', name: 'incoming.docx', url: 'https://oa/files/source-1' }])
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'REQUEST_PAGE_CONTENT' } })
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'REQUEST_FILE_LIST' } })

  assert.equal(sentMessages.at(-4).message.type, 'PAGE_CONTENT')
  assert.deepEqual(JSON.parse(JSON.stringify(sentMessages.at(-4).message.payload)), { text: 'page text' })
  assert.equal(sentMessages.at(-3).message.type, 'PAGE_FILES_UPDATED')
  assert.equal(sentMessages.at(-3).message.payload[0].sourceUrl, 'https://oa/files/source-1')
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
