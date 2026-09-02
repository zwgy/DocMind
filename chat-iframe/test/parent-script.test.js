import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import vm from 'node:vm'

function loadScript(overrides = {}) {
  const source = readFileSync(
    join(import.meta.dirname, '../public/docmind-chat-iframe-parent.js'),
    'utf8'
  )
  const sandboxWindow = overrides.window || {}
  const sandboxDocument = overrides.document || {
    title: 'test page',
    location: { href: 'http://page.local' }
  }
  sandboxWindow.window = sandboxWindow
  sandboxWindow.document = sandboxDocument
  sandboxWindow.location = sandboxWindow.location ||
    sandboxDocument.location || { href: 'http://page.local' }
  const sandbox = {
    window: sandboxWindow,
    document: sandboxDocument,
    location: sandboxWindow.location,
    URL,
    Blob,
    FormData,
    AbortController,
    console
  }
  sandboxWindow.URL = URL
  sandboxWindow.Blob = Blob
  sandboxWindow.FormData = FormData
  sandboxWindow.AbortController = AbortController
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
  if (options.scriptSrc) doc.currentScript = { src: options.scriptSrc }
  const win = {
    document: doc,
    location: { href: 'https://production.example.com/page' },
    innerWidth: 900,
    innerHeight: 700,
    addEventListener(type, callback) {
      listeners[type] = (event) => callback({ source: iframe.contentWindow, ...event })
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

test('derives the message target from iframeSrc', () => {
  const { DocMindChatIframe, listeners, sentMessages } = parentHarness({
    fetch: async () => ({ ok: true, json: async () => ({ access_token: 'test-token' }) })
  })

  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    source_system: 'oa',
    function_id: 'contract',
    business_id: '001',
    external_user_id: '1001',
    external_user_name: '张三'
  })
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'IFRAME_READY' } })

  return tick().then(() => {
    assert.equal(sentMessages[0].message.type, 'INIT_CONFIG')
    assert.equal(sentMessages[0].targetOrigin, 'https://docmind.example.com')
    chat.destroy()
  })
})

test('rejects messages from another window or origin', () => {
  const { container, DocMindChatIframe, listeners } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/'
  })

  listeners.message({
    source: {},
    origin: 'https://docmind.example.com',
    data: { type: 'MAXIMIZE' }
  })
  listeners.message({ origin: 'https://evil.example.com', data: { type: 'MAXIMIZE' } })

  assert.doesNotMatch(container.className, /maximized/)
  chat.destroy()
})

test('uses the iframe origin as the default API base URL', () => {
  const { DocMindChatIframe, sentMessages } = parentHarness()
  const chat = new DocMindChatIframe({ iframeSrc: 'https://docmind.example.com/chat-iframe/' })

  chat.setPageContent('safe content')

  assert.equal(sentMessages.at(-1).targetOrigin, 'https://docmind.example.com')
  assert.equal(chat.apiBaseUrl, 'https://docmind.example.com')
  chat.destroy()
})

test('derives the default iframe URL from the parent script URL', () => {
  const { DocMindChatIframe, iframe } = parentHarness({
    scriptSrc: 'https://docmind.example.com/chat-iframe/docmind-chat-iframe-parent.js'
  })
  const chat = new DocMindChatIframe()

  assert.match(
    iframe.src,
    /^https:\/\/docmind\.example\.com\/chat-iframe\/\?_docmind_instance=\d+$/
  )
  assert.equal(chat.apiBaseUrl, 'https://docmind.example.com')
  assert.equal(chat.targetOrigin, 'https://docmind.example.com')
  chat.destroy()
})

test('only sends page content supplied by the embedding page', () => {
  const { DocMindChatIframe, listeners, sentMessages } = parentHarness()
  const chat = new DocMindChatIframe({ iframeSrc: 'https://docmind.example.com/chat-iframe/' })

  listeners.message({
    origin: 'https://docmind.example.com',
    data: { type: 'REQUEST_PAGE_CONTENT' }
  })
  assert.equal(sentMessages.at(-1).message.payload, null)

  chat.setPageContent()
  assert.deepEqual(JSON.parse(JSON.stringify(sentMessages.at(-1).message.payload)), {
    title: 'production page',
    url: 'https://production.example.com/page',
    html: '<html></html>'
  })

  chat.setPageContent('desensitized page text')
  listeners.message({
    origin: 'https://docmind.example.com',
    data: { type: 'REQUEST_PAGE_CONTENT' }
  })
  assert.deepEqual(JSON.parse(JSON.stringify(sentMessages.at(-1).message.payload)), {
    text: 'desensitized page text'
  })
  chat.destroy()
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
  assert.equal(
    sentMessages[0].message.payload.conversationScopeKey,
    'oa:contractApproval:contract-20260706-001'
  )
  chat.destroy()
})

test('setPageContext clears the previous business page data', () => {
  const { DocMindChatIframe } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    source_system: 'generic-system',
    function_id: 'incomingDocument',
    business_id: 'doc-1'
  })
  chat.setFiles([
    { source_file_id: 'source-1', name: 'incoming.txt', source_url: '/files/source-1' }
  ])
  chat.setPageContent('old page')

  chat.setPageContext({
    source_system: 'generic-system',
    source_function_id: 'incomingDocument',
    business_id: 'page-2'
  })
  assert.equal(chat.options.function_id, 'incomingDocument')
  assert.equal(chat.options.business_id, 'page-2')
  assert.equal(chat.pageFiles.length, 0)
  assert.equal(chat.pageContent, null)
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
    tokenExchangeUrl: 'https://oa.example.com/docmind/token',
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
    source_system: 'oa',
    function_id: 'incomingDocument',
    business_id: '37906',
    document_metadata: {
      source_doc_id: 'incoming-37906',
      document_number: '来文〔2026〕1号',
      title: '风险整改通知',
      incoming_type: '安全管理',
      source_unit: '安监部',
      incoming_date: '2026-07-09'
    }
  })

  chat.setPageContent('page text')
  chat.setFiles([
    {
      source_file_id: 'source-1',
      name: 'incoming.docx',
      source_url: 'https://oa/files/source-1',
      title: '附件标题',
      type: 'image'
    }
  ])
  listeners.message({
    origin: 'https://docmind.example.com',
    data: { type: 'REQUEST_PAGE_CONTENT' }
  })
  listeners.message({ origin: 'https://docmind.example.com', data: { type: 'REQUEST_FILE_LIST' } })

  assert.equal(sentMessages.at(-4).message.type, 'PAGE_CONTENT')
  assert.deepEqual(JSON.parse(JSON.stringify(sentMessages.at(-4).message.payload)), {
    text: 'page text'
  })
  assert.equal(sentMessages.at(-3).message.type, 'PAGE_FILES_UPDATED')
  assert.equal(sentMessages.at(-3).message.payload[0].source_url, 'https://oa/files/source-1')
  assert.equal(sentMessages.at(-3).message.payload[0].source_system, 'oa')
  assert.equal(sentMessages.at(-3).message.payload[0].source_function_id, undefined)
  assert.equal(sentMessages.at(-3).message.payload[0].source_doc_id, 'incoming-37906')
  assert.equal(sentMessages.at(-3).message.payload[0].id, undefined)
  assert.deepEqual(
    JSON.parse(JSON.stringify(sentMessages.at(-3).message.payload[0].document_metadata)),
    {
      source_doc_id: 'incoming-37906',
      document_number: '来文〔2026〕1号',
      title: '风险整改通知',
      incoming_type: '安全管理',
      source_unit: '安监部',
      incoming_date: '2026-07-09'
    }
  )
  assert.equal(sentMessages.at(-3).message.payload[0].title, undefined)
  assert.equal(sentMessages.at(-3).message.payload[0].type, undefined)
  assert.equal(sentMessages.at(-2).message.type, 'PAGE_CONTENT')
  assert.equal(sentMessages.at(-1).message.type, 'FILE_LIST')
  chat.destroy()
})

test('setFiles resolves relative source_url against the embedding page', () => {
  const { DocMindChatIframe } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    source_system: 'oa',
    function_id: 'incomingDocument',
    business_id: '37906'
  })

  chat.setFiles([
    { source_file_id: 'source-1', name: 'incoming.pdf', source_url: '/files/source-1' }
  ])

  assert.equal(chat.pageFiles[0].source_url, 'https://production.example.com/files/source-1')
  chat.destroy()
})

test('setFiles requires source_file_id as the current attachment key', () => {
  const { DocMindChatIframe } = parentHarness()
  const chat = new DocMindChatIframe({ iframeSrc: 'https://docmind.example.com/chat-iframe/' })

  assert.throws(
    () =>
      chat.setFiles([
        { id: 'legacy-id', name: 'incoming.docx', source_url: 'https://oa/files/legacy-id' }
      ]),
    /source_file_id/
  )
  chat.destroy()
})

test('explicit source_doc_id stays distinct from the page business_id', () => {
  const { DocMindChatIframe } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    source_system: 'oa',
    function_id: 'incomingDocument',
    business_id: 'incomingDetail37908',
    document_metadata: {
      source_doc_id: '37908'
    }
  })

  chat.setFiles([
    {
      source_file_id: '202010200206',
      name: 'incoming.pdf',
      source_url: 'https://oa/files/202010200206'
    }
  ])

  assert.equal(chat.options.business_id, 'incomingDetail37908')
  assert.equal(chat.pageFiles[0].source_doc_id, '37908')
  chat.destroy()
})

test('business_id is used only as a fallback source_doc_id', () => {
  const { DocMindChatIframe } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/',
    source_system: 'oa',
    function_id: 'incomingDocument',
    business_id: 'incomingDetail37908'
  })

  chat.setFiles([
    {
      source_file_id: '202010200206',
      name: 'incoming.pdf',
      source_url: 'https://oa/files/202010200206'
    }
  ])

  assert.equal(chat.pageFiles[0].source_doc_id, 'incomingDetail37908')
  chat.destroy()
})

test('window control messages update state and emit optional chat callbacks', () => {
  const { container, DocMindChatIframe, listeners } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/'
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
    iframeSrc: 'https://docmind.example.com/chat-iframe/'
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

  // DocMindChatIframe 默认 initialState: 'minimized'，示例不应显式打开助手窗口。
  // 改为"反向断言"：只要没有显式开启就认为符合契约。
  assert.doesNotMatch(example, /initialState:\s*'(open|normal|maximized)'/)
  assert.match(
    example,
    /<span class="tk-key">document_metadata<\/span>:\s*incomingDocumentMetadata/
  )
  assert.match(example, /document_metadata:\s*incomingDocumentMetadata/)
  assert.doesNotMatch(example, /incoming_document_metadata/)
  assert.match(example, /\.\.\.businessContext,/)
  assert.doesNotMatch(example, /\.\.\.incomingDocumentMetadata,/)
  assert.match(example, /当前页面中的来文 ID；它与页面级 business_id 语义不同/)
  const metadataBlock = example.match(
    /const incomingDocumentMetadata = \{([\s\S]*?)\n\s*\}/
  )?.[1]
  assert.ok(metadataBlock)
  assert.match(metadataBlock, /source_doc_id:\s*params\.get\('source_doc_id'\) \|\| '37908'/)
  assert.match(example, /source_file_id：来源系统内该附件的稳定唯一 ID/)
  assert.match(
    example,
    /name:\s*'上铁辆〔2020〕316号\.pdf',\s*source_file_id:\s*'202010200206',\s*source_url:/
  )
  assert.match(
    example,
    /<span class="tk-key">source_doc_id<\/span>:\s*<span class="tk-str">'37908'<\/span>/
  )
  assert.match(
    example,
    /<span class="tk-key">source_file_id<\/span>:\s*<span class="tk-str">'202010200206'/
  )
  assert.match(example, /source_file_id:\s*'202010200206'/)
  assert.match(
    example,
    /http:\/\/192\.168\.1\.220:5174\/chat-iframe\/docmind-chat-iframe-parent\.js/
  )
  assert.match(
    example,
    /<span class="tk-key">iframeSrc<\/span>:\s*<span class="tk-str">'http:\/\/192\.168\.1\.220:5174\/chat-iframe\/'/
  )
  assert.doesNotMatch(example, /<span class="tk-key">apiBaseUrl<\/span>:/)
  assert.match(example, /不要填写 apiBaseUrl/)
  assert.match(example, /apiBaseUrl 无需填写：5174 Nginx 已代理 \/api/)
})

test('local example bypasses parent SDK responses cached before no-store', () => {
  const example = readFileSync(join(import.meta.dirname, '../public/example.html'), 'utf8')

  assert.match(example, /docmind-chat-iframe-parent\.js\?v=[\d.]+/)
})

test('iframe header drag messages move the parent window', () => {
  const { container, iframe, DocMindChatIframe, listeners } = parentHarness()
  const chat = new DocMindChatIframe({
    iframeSrc: 'https://docmind.example.com/chat-iframe/'
  })

  listeners.message({
    origin: 'https://docmind.example.com',
    data: {
      type: 'WINDOW_DRAG_START',
      payload: { clientX: 12, clientY: 8, screenX: 500, screenY: 400, pointerId: 9 }
    }
  })
  assert.equal(iframe.style.pointerEvents, undefined)

  listeners.message({
    origin: 'https://docmind.example.com',
    data: {
      type: 'WINDOW_DRAG_MOVE',
      payload: { clientX: 32, clientY: 38, screenX: 520, screenY: 430, pointerId: 9 }
    }
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
