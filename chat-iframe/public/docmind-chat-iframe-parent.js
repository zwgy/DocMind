(function (global) {
  'use strict'

  var DOCUMENT_EXTENSIONS = ['doc', 'docx', 'pdf', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'md', 'csv']
  var CHAT_ICON_SVG =
    '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/>' +
    '<path d="M8 9h8M8 13h5"/>' +
    '</svg>'

  function stripText(value) {
    return String(value || '').trim()
  }

  function isDocumentFile(name) {
    var ext = stripText(name).split('.').pop().toLowerCase()
    return DOCUMENT_EXTENSIONS.indexOf(ext) !== -1
  }

  function cleanSizeText(value) {
    // 生产系统把大小拼在文件名后，例如 "-200.16KB"，这里先抽出来供文件名清洗和展示共用。
    var match = stripText(value).match(/-?\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)/i)
    return match ? match[1] + match[2].toUpperCase() : ''
  }

  function cleanName(text, sizeText) {
    var name = stripText(text)
    // 文件名来自 textContent，会混入大小和外层引号；不清洗会导致扩展名判断失败。
    name = name.replace(/-\s*\d+(?:\.\d+)?\s*(B|KB|MB|GB|TB)/gi, '').trim()
    if (sizeText) {
      name = name.replace(sizeText, '').replace('-' + sizeText, '')
    }
    name = name.replace(/-+$/g, '').trim()
    return name.replace(/^["'“”]+|["'“”]+$/g, '').trim()
  }

  function extractDownloadUrl(onclick, href) {
    var text = stripText(onclick)
    // 目标生产系统的下载地址藏在 onclick 中，href 常是 "###"，所以 onclick 优先。
    var quoted = text.match(/YZSoft\.File\.download\(['"]([^'"]+)['"]\)/i)
    if (quoted) return quoted[1]
    var anyUrl = text.match(/https?:\/\/[^'")\s]+/i)
    if (anyUrl) return anyUrl[0]
    href = stripText(href)
    return href && href !== '###' && href !== '#' ? href : ''
  }

  function sourceKeyFromUrl(url) {
    var text = stripText(url)
    var query = text.split('?')[1]
    if (query) {
      // 旧系统常把附件 ID 直接放在 query 第一段，保留这个降级能减少对 DOM 属性的依赖。
      var first = query.split('&')[0]
      return first.indexOf('=') >= 0 ? first.split('=').pop() : first
    }
    return text.split('/').filter(Boolean).pop() || ''
  }

  function normalizeFiles(files, selectedIds) {
    var selectedMap = {}
    ;(selectedIds || []).forEach(function (id) {
      selectedMap[id] = true
    })
    var normalized = (files || [])
      .filter(function (file) {
        return file && isDocumentFile(file.name || file.url || file.sourceUrl)
      })
      .map(function (file) {
        var sourceUrl = file.sourceUrl || file.url || ''
        var sourceKey = file.sourceKey || file.id || sourceKeyFromUrl(sourceUrl)
        var normalizedFile = {
          id: file.id || sourceKey || file.name,
          name: file.name,
          sourceUrl: sourceUrl,
          url: sourceUrl,
          sourceKey: sourceKey,
          type: file.type || 'document',
          selected: Boolean(file.selected || selectedMap[file.id] || selectedMap[sourceKey])
        }
        if (file.sizeText) normalizedFile.sizeText = file.sizeText
        if (file.sizeBytes) normalizedFile.sizeBytes = file.sizeBytes
        if (file.onclick) normalizedFile.onclick = file.onclick
        return normalizedFile
      })
    if (normalized.length && !normalized.some(function (file) { return file.selected })) {
      // 多附件场景必须给 iframe 一个稳定默认项，否则初始化查询会无目标。
      normalized[0].selected = true
    }
    return normalized
  }

  function extractFilesFromDocument(doc) {
    // 优先走生产系统附件容器，避免全页链接扫描把普通导航误判成来文附件。
    var links = Array.prototype.slice.call(doc.querySelectorAll('.items .item[attachment] a'))
    if (!links.length) {
      links = Array.prototype.slice.call(doc.querySelectorAll('a'))
    }
    var files = links
      .map(function (link) {
        var item = link.closest ? link.closest('.item[attachment]') : null
        var sizeNode = link.querySelector ? link.querySelector('.size') : null
        var sizeText = cleanSizeText(sizeNode ? sizeNode.textContent : link.textContent)
        var onclick = link.getAttribute ? link.getAttribute('onclick') : ''
        var sourceUrl = extractDownloadUrl(onclick, link.href)
        // sourceKey 是后端匹配优先级最高的字段，按生产系统最稳定的来源依次降级。
        var sourceKey =
          (item && item.getAttribute && item.getAttribute('attachment')) ||
          (item && item.id ? item.id.replace(/_BOX$/i, '') : '') ||
          sourceKeyFromUrl(sourceUrl)
        var name = cleanName(link.textContent, sizeText)
        if (!name || !isDocumentFile(name)) return null
        return {
          id: sourceKey || name,
          name: name,
          sourceUrl: sourceUrl,
          url: sourceUrl,
          sourceKey: sourceKey,
          sizeText: sizeText,
          onclick: onclick,
          type: 'document'
        }
      })
      .filter(Boolean)
    return normalizeFiles(files)
  }

  function DocMindChatIframe(options) {
    this.options = Object.assign(
      {
        iframeSrc: '/',
        user: null,
        token: null,
        agentId: null,
        targetOrigin: '*',
        originAllowlist: [],
        position: 'bottom-right',
        width: 460,
        height: 680,
        offsetX: 24,
        offsetY: 24,
        initialState: 'minimized',
        includePageContent: true,
        includeFiles: true,
        selectedFileIds: [],
        buttonHtml: null,
        autoInit: true
      },
      options || {}
    )
    this.windowState = this.options.initialState
    this.pageContent = null
    this.pageFiles = []
    this.eventListeners = {}
    this.container = null
    this.iframe = null
    this.messageHandler = null
    this.pointerMoveHandler = null
    this.pointerUpHandler = null
    this.drag = null
    if (this.options.autoInit) this.init()
  }

  DocMindChatIframe.extractFilesFromDocument = extractFilesFromDocument

  DocMindChatIframe.prototype.init = function () {
    if (this.container) return this
    this.container = document.createElement('div')
    this.container.className = 'docmind-chat-iframe ' + this.windowState + ' ' + this.options.position
    this.container.innerHTML = this._html()
    document.body.appendChild(this.container)
    this.iframe = this.container.querySelector('iframe')
    this.iframe.src = this.options.iframeSrc
    // 显式 setFiles 尚未调用时先扫 DOM，保证只嵌脚本的生产页面也能进入最小闭环。
    if (this.options.includeFiles) this.pageFiles = extractFilesFromDocument(document)
    this._bindEvents()
    this._setWindowState(this.windowState, false)
    return this
  }

  DocMindChatIframe.prototype.setPageContent = function (content) {
    // 允许直接传字符串，是为了兼容已有接入方只准备了页面纯文本的轻量场景。
    this.pageContent = typeof content === 'string' ? { text: content } : content
    if (this.container) this._sendToIframe('PAGE_CONTENT', this.pageContent)
    return this
  }

  DocMindChatIframe.prototype.setFiles = function (files) {
    this.pageFiles = normalizeFiles(files, this.options.selectedFileIds)
    if (this.container) this._sendToIframe('PAGE_FILES_UPDATED', this.pageFiles)
    return this
  }

  DocMindChatIframe.prototype.addFile = function (file) {
    this.setFiles(this.pageFiles.concat([file]))
    return this
  }

  DocMindChatIframe.prototype.setUser = function (user) {
    this.options.user = user
    this._sendConfig()
    return this
  }

  DocMindChatIframe.prototype.open = function () { return this.restore() }
  DocMindChatIframe.prototype.minimize = function () { return this._setWindowState('minimized') }
  DocMindChatIframe.prototype.maximize = function () { return this._setWindowState('maximized') }
  DocMindChatIframe.prototype.restore = function () { return this._setWindowState('normal') }
  DocMindChatIframe.prototype.close = function () { return this._setWindowState('closed') }

  DocMindChatIframe.prototype.destroy = function () {
    if (this.messageHandler) window.removeEventListener('message', this.messageHandler)
    if (this.pointerMoveHandler) document.removeEventListener('pointermove', this.pointerMoveHandler)
    if (this.pointerUpHandler) document.removeEventListener('pointerup', this.pointerUpHandler)
    if (this.container && this.container.parentNode) this.container.parentNode.removeChild(this.container)
    this.container = null
    this.iframe = null
    this.messageHandler = null
    this.pointerMoveHandler = null
    this.pointerUpHandler = null
  }

  DocMindChatIframe.prototype.on = function (name, callback) {
    ;(this.eventListeners[name] || (this.eventListeners[name] = [])).push(callback)
    return this
  }

  DocMindChatIframe.prototype._emit = function (name, payload) {
    ;(this.eventListeners[name] || []).forEach(function (callback) {
      callback(payload)
    })
  }

  DocMindChatIframe.prototype._html = function () {
    var width = this.options.width
    var height = this.options.height
    // 悬浮脚本经常跨系统静态部署，内联 SVG 比额外图片路径更不容易被部署目录或跨域策略破坏。
    var restoreButtonHtml = this.options.buttonHtml || CHAT_ICON_SVG
    return (
      '<style>' +
      '.docmind-chat-iframe{position:fixed;z-index:999999;font-family:Arial,sans-serif}' +
      '.docmind-chat-iframe.bottom-right{right:' + this.options.offsetX + 'px;bottom:' + this.options.offsetY + 'px}' +
      '.docmind-chat-iframe.bottom-left{left:' + this.options.offsetX + 'px;bottom:' + this.options.offsetY + 'px}' +
      '.docmind-chat-iframe.top-right{right:' + this.options.offsetX + 'px;top:' + this.options.offsetY + 'px}' +
      '.docmind-chat-iframe.top-left{left:' + this.options.offsetX + 'px;top:' + this.options.offsetY + 'px}' +
      '.docmind-chat-iframe.normal{width:' + width + 'px;height:' + height + 'px}' +
      '.docmind-chat-iframe.minimized{width:56px;height:56px}' +
      '.docmind-chat-iframe.maximized{inset:0!important;width:100vw;height:100vh}' +
      '.docmind-chat-iframe.closed{display:none}' +
      '.docmind-chat-shell{height:100%;background:#fff;border-radius:8px;box-shadow:0 18px 45px rgba(0,0,0,.24);overflow:hidden}' +
      '.docmind-chat-iframe.maximized .docmind-chat-shell{border-radius:0}' +
      '.docmind-chat-framebar{height:28px;display:flex;align-items:center;padding:0 10px;background:#023944;color:#fff;font-size:12px;cursor:move;user-select:none}' +
      '.docmind-chat-frame{width:100%;height:calc(100% - 28px);border:0;display:block}' +
      '.docmind-chat-restore{width:56px;height:56px;border:0;border-radius:28px;background:#046a82;color:#fff;box-shadow:0 10px 28px rgba(0,0,0,.25);cursor:pointer;display:flex;align-items:center;justify-content:center}' +
      '.docmind-chat-restore svg{width:28px;height:28px;display:block}' +
      '.docmind-chat-iframe.minimized .docmind-chat-shell{display:none}' +
      '.docmind-chat-iframe:not(.minimized) .docmind-chat-restore{display:none}' +
      '</style>' +
      '<button class="docmind-chat-restore" title="打开 docMind 文档助手">' + restoreButtonHtml + '</button>' +
      '<div class="docmind-chat-shell"><div class="docmind-chat-framebar">docMind 文档助手</div><iframe class="docmind-chat-frame" allow="clipboard-write"></iframe></div>'
    )
  }

  DocMindChatIframe.prototype._bindEvents = function () {
    var self = this
    this.container.querySelector('.docmind-chat-restore').addEventListener('click', function () {
      self.restore()
    })
    this.container.querySelector('.docmind-chat-framebar').addEventListener('pointerdown', function (event) {
      if (self.windowState !== 'normal') return
      // 只允许普通窗口拖动，最大化时拖动会和全屏布局互相打架。
      self.drag = {
        x: event.clientX,
        y: event.clientY,
        left: self.container.offsetLeft,
        top: self.container.offsetTop
      }
      event.preventDefault()
    })
    this.pointerMoveHandler = function (event) {
      if (!self.drag) return
      self.container.style.left = self.drag.left + event.clientX - self.drag.x + 'px'
      self.container.style.top = self.drag.top + event.clientY - self.drag.y + 'px'
      self.container.style.right = 'auto'
      self.container.style.bottom = 'auto'
    }
    document.addEventListener('pointermove', this.pointerMoveHandler)
    this.pointerUpHandler = function () {
      self.drag = null
    }
    document.addEventListener('pointerup', this.pointerUpHandler)
    this.messageHandler = function (event) {
      self._handleMessage(event)
    }
    window.addEventListener('message', this.messageHandler)
  }

  DocMindChatIframe.prototype._handleMessage = function (event) {
    var message = event.data || {}
    if (!message.type) return
    // 父页面接收的是 iframe 发来的消息，应按 iframe 的 targetOrigin 校验；originAllowlist 会下发给 iframe 校验父页面来源。
    if (this.options.targetOrigin !== '*' && event.origin !== this.options.targetOrigin) {
      return
    }
    switch (message.type) {
      case 'IFRAME_READY':
        this._sendConfig()
        this._sendToIframe('PAGE_CONTENT', this.pageContent || this._pageContentFromDocument())
        this._sendToIframe('PAGE_FILES_UPDATED', this.pageFiles)
        break
      case 'REQUEST_PAGE_CONTENT':
        this._sendToIframe('PAGE_CONTENT', this.pageContent || this._pageContentFromDocument())
        break
      case 'REQUEST_FILE_LIST':
        this._sendToIframe('FILE_LIST', this.pageFiles)
        break
      case 'MINIMIZE':
        this.minimize()
        break
      case 'MAXIMIZE':
        this.maximize()
        break
      case 'RESTORE':
        this.restore()
        break
      case 'CLOSE':
        this.close()
        break
      case 'CONVERSATION_CREATED':
        this._emit('conversationCreated', message.payload)
        break
      case 'MESSAGE_SENT':
        this._emit('messageSent', message.payload)
        break
      default:
        break
    }
  }

  DocMindChatIframe.prototype._pageContentFromDocument = function () {
    if (!this.options.includePageContent) return null
    return { title: document.title, url: location.href, html: document.documentElement.outerHTML }
  }

  DocMindChatIframe.prototype._sendConfig = function () {
    this._sendToIframe('INIT_CONFIG', {
      user: this.options.user,
      token: this.options.token,
      agentId: this.options.agentId,
      includePageContent: this.options.includePageContent,
      includeFiles: this.options.includeFiles,
      selectedFileIds: this.options.selectedFileIds,
      originAllowlist: this.options.originAllowlist
    })
  }

  DocMindChatIframe.prototype._sendToIframe = function (type, payload) {
    if (!this.iframe || !this.iframe.contentWindow) return
    // 默认 targetOrigin 由接入方配置，避免父页面脚本猜测跨域部署形态。
    this.iframe.contentWindow.postMessage({ type: type, payload: payload, timestamp: Date.now() }, this.options.targetOrigin)
  }

  DocMindChatIframe.prototype._setWindowState = function (state, notify) {
    this.windowState = state
    this.container.className = 'docmind-chat-iframe ' + state + ' ' + this.options.position
    if (notify !== false) this._sendToIframe('WINDOW_STATE', { state: state })
    this._emit('stateChange', { state: state })
    return this
  }

  global.DocMindChatIframe = DocMindChatIframe
})(typeof window !== 'undefined' ? window : this)
