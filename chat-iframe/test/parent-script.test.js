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
  const sentMessages = []
  const listeners = {}
  const container = {
    className: '',
    innerHTML: '',
    offsetLeft: 10,
    offsetTop: 20,
    parentNode: { removeChild() {} },
    querySelector(selector) {
      if (selector === 'iframe') {
        return {
          src: '',
          contentWindow: {
            postMessage(message, targetOrigin) {
              sentMessages.push({ message, targetOrigin })
            }
          }
        }
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
    addEventListener(type, callback) {
      listeners[type] = callback
    },
    removeEventListener() {}
  }
  const DocMindChatIframe = loadScript({ window: win, document: doc })

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
