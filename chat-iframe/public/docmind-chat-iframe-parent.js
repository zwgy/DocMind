;(function (global) {
  'use strict'

  var DOCUMENT_EXTENSIONS = ['doc', 'docx', 'pdf', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'md', 'csv']
  var CHAT_ICON_HTML = '<span class="docmind-chat-mark" aria-hidden="true">AI</span>'

  function stripText(value) {
    return String(value || '').trim()
  }

  function joinApiUrl(baseUrl, path) {
    var base = stripText(baseUrl).replace(/\/+$/, '')
    return base ? base + path : path
  }

  function originFromUrl(value) {
    var match = stripText(value).match(/^[a-z][a-z\d+.-]*:\/\/[^/]+/i)
    return match ? match[0] : ''
  }

  function defaultIframeSrc() {
    // 父脚本与 iframe 同目录发布时可直接复用脚本地址，接入方无需重复填写部署 URL。
    var script = global.document && global.document.currentScript
    var source = stripText(script && script.src)
    return source ? source.replace(/\/[^/?#]+(?:[?#].*)?$/, '/') : '/chat-iframe/'
  }

  function iframeEntryUrl(iframeSrc) {
    var source = stripText(iframeSrc)
    if (!source) return source
    // 部分嵌入式浏览器会复用固定 URL 的旧子框架文档，即使入口响应声明 no-store；
    // 只刷新入口 HTML，内部带内容哈希的静态资源仍可正常使用长期缓存。
    return source + (source.indexOf('?') === -1 ? '?' : '&') + '_docmind_instance=' + Date.now()
  }

  function resolveTargetOrigin(iframeSrc) {
    return originFromUrl(iframeSrc) || originFromUrl(global.location && global.location.href) || '*'
  }

  function resolveApiBaseUrl(apiBaseUrl, iframeSrc) {
    // 跨域嵌入默认回到 iframe 所在 DocMind 域；同源相对地址继续交给浏览器解析。
    return stripText(apiBaseUrl) || originFromUrl(iframeSrc)
  }

  function isDocumentFile(name) {
    var ext = stripText(name).split('.').pop().toLowerCase()
    return DOCUMENT_EXTENSIONS.indexOf(ext) !== -1
  }

  function normalizeFiles(files, options) {
    options = options || {}
    // 同一来文的元数据只传一份；附件不再复制业务字段。
    var documentMetadata = options.document_metadata || {}
    var documentSourceDocId = stripText(documentMetadata.source_doc_id)
    var normalized = (files || [])
      .filter(function (file) {
        return file && isDocumentFile(file.name || file.source_url)
      })
      .map(function (file) {
        var sourceUrl = file.source_url || ''
        if (sourceUrl) sourceUrl = new global.URL(sourceUrl, global.location.href).toString()
        var sourceFileId = stripText(file.source_file_id)
        if (!sourceFileId) throw new Error('附件缺少 source_file_id')
        var normalizedFile = {
          name: file.name,
          source_url: sourceUrl,
          source_file_id: sourceFileId,
          selected: Boolean(file.selected)
        }
        if (file.source_doc_id) normalizedFile.source_doc_id = file.source_doc_id
        if (file.source_system) normalizedFile.source_system = file.source_system
        if (!normalizedFile.source_doc_id && documentSourceDocId)
          normalizedFile.source_doc_id = documentSourceDocId
        // business_id 仅兼容“一页一来文”的旧接入作为最终兜底，不能替代来文元数据中的 source_doc_id。
        if (!normalizedFile.source_doc_id && options.business_id)
          normalizedFile.source_doc_id = options.business_id
        if (file.source_function_id) normalizedFile.source_function_id = file.source_function_id
        if (!normalizedFile.source_function_id && options.function_id)
          normalizedFile.source_function_id = options.function_id
        if (!normalizedFile.source_system && options.source_system)
          normalizedFile.source_system = options.source_system
        normalizedFile.document_metadata = file.document_metadata || documentMetadata
        if (file.is_main_file) normalizedFile.is_main_file = true
        if (file.size_text) normalizedFile.size_text = file.size_text
        if (file.size_bytes) normalizedFile.size_bytes = file.size_bytes
        if (file.onclick) normalizedFile.onclick = file.onclick
        return normalizedFile
      })
    if (
      normalized.length &&
      !normalized.some(function (file) {
        return file.selected
      })
    ) {
      // 多附件场景必须给 iframe 一个稳定默认项，否则初始化查询会无目标。
      normalized[0].selected = true
    }
    return normalized
  }

  function DocMindChatIframe(options) {
    this.options = Object.assign(
      {
        iframeSrc: defaultIframeSrc(),
        apiBaseUrl: null,
        agentId: null,
        tokenExchangeUrl: null,
        source_system: '',
        function_id: '',
        business_id: '',
        // 同一来文的可选业务字段，统一作为 document_metadata 传递。
        document_metadata: null,
        external_user_id: '',
        external_user_name: '',
        position: 'bottom-right',
        width: 460,
        height: 680,
        offsetX: 24,
        offsetY: 24,
        initialState: 'minimized',
        buttonHtml: null
      },
      options || {}
    )
    this.targetOrigin = resolveTargetOrigin(this.options.iframeSrc)
    this.apiBaseUrl = resolveApiBaseUrl(this.options.apiBaseUrl, this.options.iframeSrc)
    this.windowState = this.options.initialState
    this.pageContent = null
    this.pageFiles = []
    this.eventListeners = {}
    this.container = null
    this.iframe = null
    this.messageHandler = null
    this.tokenPromise = null
    this.resolvedToken = null
    this.pointerMoveHandler = null
    this.pointerUpHandler = null
    this.pointerCancelHandler = null
    this.windowBlurHandler = null
    this.drag = null
    this.dragMoved = false
    this.init()
  }

  DocMindChatIframe.prototype.init = function () {
    if (this.container) return this
    this.container = document.createElement('div')
    this.container.className =
      'docmind-chat-iframe ' + this.windowState + ' ' + this.options.position
    this.container.innerHTML = this._html()
    document.body.appendChild(this.container)
    this.iframe = this.container.querySelector('iframe')
    this.iframe.src = iframeEntryUrl(this.options.iframeSrc)
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

  DocMindChatIframe.prototype.setPageContext = function (context) {
    context = context || {}
    var sourceSystem = stripText(context.source_system)
    var sourceFunctionId = stripText(context.source_function_id || context.function_id)
    var businessId = stripText(context.business_id)
    if (!sourceSystem || !sourceFunctionId || !businessId) {
      throw new Error('页面上下文缺少 source_system、source_function_id 或 business_id')
    }

    var sourceChanged = sourceSystem !== stripText(this.options.source_system)
    this.options.source_system = sourceSystem
    this.options.function_id = sourceFunctionId
    this.options.business_id = businessId
    this.options.document_metadata = context.document_metadata || null
    this.pageContent = null
    this.pageFiles = []
    if (sourceChanged) {
      // source_system 参与外部用户身份，跨系统切换后不能继续复用原换票结果。
      this.tokenPromise = null
      this.resolvedToken = null
    }
    // 重载 iframe 能原子清空会话、附件摘要、轮询和瞬时 UI 状态；随后仍由 setPageContent/setFiles 注入新页面数据。
    if (this.iframe) this.iframe.src = iframeEntryUrl(this.options.iframeSrc)
    return this
  }

  DocMindChatIframe.prototype.setFiles = function (files) {
    this.pageFiles = normalizeFiles(files, this.options)
    if (this.container) this._sendToIframe('PAGE_FILES_UPDATED', this.pageFiles)
    return this
  }

  DocMindChatIframe.prototype.addFile = function (file) {
    this.setFiles(this.pageFiles.concat([file]))
    return this
  }

  DocMindChatIframe.prototype.open = function () {
    return this.restore()
  }
  DocMindChatIframe.prototype.minimize = function () {
    return this._setWindowState('minimized')
  }
  DocMindChatIframe.prototype.maximize = function () {
    return this._setWindowState('maximized')
  }
  DocMindChatIframe.prototype.restore = function () {
    return this._setWindowState('normal')
  }
  DocMindChatIframe.prototype.close = function () {
    return this._setWindowState('closed')
  }

  DocMindChatIframe.prototype.destroy = function () {
    if (this.messageHandler) window.removeEventListener('message', this.messageHandler)
    if (this.pointerMoveHandler)
      document.removeEventListener('pointermove', this.pointerMoveHandler)
    if (this.pointerUpHandler) document.removeEventListener('pointerup', this.pointerUpHandler)
    if (this.pointerCancelHandler)
      document.removeEventListener('pointercancel', this.pointerCancelHandler)
    if (this.windowBlurHandler) window.removeEventListener('blur', this.windowBlurHandler)
    this._endDrag()
    if (this.container && this.container.parentNode)
      this.container.parentNode.removeChild(this.container)
    this.container = null
    this.iframe = null
    this.messageHandler = null
    this.pointerMoveHandler = null
    this.pointerUpHandler = null
    this.pointerCancelHandler = null
    this.windowBlurHandler = null
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
    var availableWidth = this.options.offsetX * 2
    var availableHeight = this.options.offsetY * 2
    // 悬浮脚本经常跨系统静态部署，内联 HTML 比额外图片路径更不容易被部署目录或跨域策略破坏。
    var restoreButtonHtml = this.options.buttonHtml || CHAT_ICON_HTML
    return (
      '<style>' +
      '.docmind-chat-iframe{position:fixed;z-index:999999;font-family:Arial,sans-serif}' +
      '.docmind-chat-iframe.bottom-right{right:' +
      this.options.offsetX +
      'px;bottom:' +
      this.options.offsetY +
      'px}' +
      '.docmind-chat-iframe.bottom-left{left:' +
      this.options.offsetX +
      'px;bottom:' +
      this.options.offsetY +
      'px}' +
      '.docmind-chat-iframe.top-right{right:' +
      this.options.offsetX +
      'px;top:' +
      this.options.offsetY +
      'px}' +
      '.docmind-chat-iframe.top-left{left:' +
      this.options.offsetX +
      'px;top:' +
      this.options.offsetY +
      'px}' +
      '.docmind-chat-iframe.normal{width:min(' +
      width +
      'px,calc(100vw - ' +
      availableWidth +
      'px));height:min(' +
      height +
      'px,calc(100vh - ' +
      availableHeight +
      'px))}' +
      '.docmind-chat-iframe.minimized{width:60px;height:60px}' +
      '.docmind-chat-iframe.closed{width:60px;height:60px}' +
      '.docmind-chat-iframe.maximized{inset:0!important;width:100vw;height:100vh}' +
      '.docmind-chat-shell{height:100%;background:#fff;border-radius:8px;box-shadow:0 18px 45px rgba(0,0,0,.24);overflow:hidden}' +
      '.docmind-chat-iframe.maximized .docmind-chat-shell{border-radius:0}' +
      '.docmind-chat-frame{width:100%;height:100%;border:0;display:block}' +
      '.docmind-chat-restore{position:relative;width:60px;height:60px;border:0;border-radius:50%;background:radial-gradient(circle at 30% 20%,#fff 0%,#f1fbff 38%,#dcf7ff 72%,#c9eef8 100%);color:#0ea5e9;box-shadow:0 14px 28px rgba(15,23,42,.1),inset 0 0 0 1px rgba(148,163,184,.1),inset 0 1px 0 rgba(255,255,255,.82);cursor:pointer;display:flex;align-items:center;justify-content:center;outline:none;overflow:hidden;appearance:none;-webkit-appearance:none;-webkit-tap-highlight-color:transparent;transition:transform .18s ease,box-shadow .18s ease}' +
      '.docmind-chat-restore:hover,.docmind-chat-restore:active,.docmind-chat-restore:focus{background:radial-gradient(circle at 30% 20%,#fff 0%,#f1fbff 38%,#dcf7ff 72%,#c9eef8 100%);box-shadow:0 14px 28px rgba(15,23,42,.1),inset 0 0 0 1px rgba(148,163,184,.1),inset 0 1px 0 rgba(255,255,255,.86)}' +
      '.docmind-chat-restore:hover{transform:translateY(-2px) scale(1.12)}' +
      '.docmind-chat-restore:active{transform:translateY(0) scale(.96)}' +
      '.docmind-chat-restore:focus-visible{box-shadow:0 14px 28px rgba(15,23,42,.1),inset 0 0 0 1px rgba(148,163,184,.14),inset 0 1px 0 rgba(255,255,255,.88)}' +
      '.docmind-chat-restore[data-unread="true"]:after{content:"";position:absolute;right:5px;top:5px;width:9px;height:9px;border-radius:50%;background:#ff4d4f;border:2px solid #fff;z-index:2}' +
      '.docmind-chat-restore:before{content:"";position:absolute;inset:0;border-radius:50%;background:rgba(255,255,255,.12);pointer-events:none}' +
      '.docmind-chat-mark{position:relative;font:900 25px/1 Arial,sans-serif;letter-spacing:0;background:linear-gradient(135deg,#2563eb 0%,#06b6d4 56%,#14b8a6 100%);-webkit-background-clip:text;background-clip:text;color:transparent;text-shadow:0 8px 18px rgba(37,99,235,.18)}' +
      '.docmind-chat-iframe.minimized .docmind-chat-shell,.docmind-chat-iframe.closed .docmind-chat-shell{display:none}' +
      '.docmind-chat-iframe:not(.minimized):not(.closed) .docmind-chat-restore{display:none}' +
      '</style>' +
      '<button class="docmind-chat-restore" title="打开助手">' +
      restoreButtonHtml +
      '</button>' +
      '<div class="docmind-chat-shell"><iframe class="docmind-chat-frame" allow="clipboard-write"></iframe></div>'
    )
  }

  DocMindChatIframe.prototype._bindEvents = function () {
    var self = this
    var restoreButton = this.container.querySelector('.docmind-chat-restore')
    restoreButton.addEventListener('pointerdown', function (event) {
      self._startDrag(event)
    })
    restoreButton.addEventListener('click', function (event) {
      if (self.dragMoved) {
        event.preventDefault()
        self.dragMoved = false
        return
      }
      self.restore()
    })
    this.pointerMoveHandler = function (event) {
      self._moveDrag(event)
    }
    document.addEventListener('pointermove', this.pointerMoveHandler)
    this.pointerUpHandler = function (event) {
      self._endDrag(event)
    }
    document.addEventListener('pointerup', this.pointerUpHandler)
    this.pointerCancelHandler = function (event) {
      self._endDrag(event)
    }
    document.addEventListener('pointercancel', this.pointerCancelHandler)
    this.windowBlurHandler = function () {
      self._endDrag()
    }
    window.addEventListener('blur', this.windowBlurHandler)
    this.messageHandler = function (event) {
      self._handleMessage(event)
    }
    window.addEventListener('message', this.messageHandler)
  }

  DocMindChatIframe.prototype._startDrag = function (event) {
    this._startDragAt(event)
    if (event.currentTarget && event.currentTarget.setPointerCapture && event.pointerId != null) {
      try {
        event.currentTarget.setPointerCapture(event.pointerId)
      } catch {
        // 某些宿主页面会拦截 pointer capture；document 级事件仍会兜住大多数浏览器。
      }
    }
    event.preventDefault()
  }

  DocMindChatIframe.prototype._startIframeDrag = function (payload) {
    if (!this.container || !payload) return
    this._startDragAt(this._pointFromIframePayload(payload), { disableIframe: false })
  }

  DocMindChatIframe.prototype._pointFromIframePayload = function (payload) {
    if (typeof payload.screenX === 'number' && typeof payload.screenY === 'number') {
      return { screenX: payload.screenX, screenY: payload.screenY, pointerId: payload.pointerId }
    }
    var rect = this.container.getBoundingClientRect()
    return {
      clientX: rect.left + (payload.clientX || 0),
      clientY: rect.top + (payload.clientY || 0),
      pointerId: payload.pointerId
    }
  }

  DocMindChatIframe.prototype._startDragAt = function (point, options) {
    if (!this.container || this.windowState === 'maximized') return
    // iframe 会截获鼠标释放事件；拖动时临时禁用它，避免窗口一直粘着鼠标。
    var disableIframe = !options || options.disableIframe !== false
    if (disableIframe && this.iframe) this.iframe.style.pointerEvents = 'none'
    this.dragMoved = false
    this.drag = {
      x: typeof point.screenX === 'number' ? point.screenX : point.clientX,
      y: typeof point.screenY === 'number' ? point.screenY : point.clientY,
      left: this.container.offsetLeft,
      top: this.container.offsetTop,
      pointerId: point.pointerId,
      disableIframe: disableIframe
    }
  }

  DocMindChatIframe.prototype._moveDrag = function (event) {
    if (!this.drag || !this.container) return
    var x = typeof event.screenX === 'number' ? event.screenX : event.clientX
    var y = typeof event.screenY === 'number' ? event.screenY : event.clientY
    var dx = x - this.drag.x
    var dy = y - this.drag.y
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) this.dragMoved = true
    this.container.style.left = this.drag.left + dx + 'px'
    this.container.style.top = this.drag.top + dy + 'px'
    this.container.style.right = 'auto'
    this.container.style.bottom = 'auto'
  }

  DocMindChatIframe.prototype._endDrag = function (event) {
    if (!this.drag) return
    if (
      event &&
      event.currentTarget &&
      event.currentTarget.releasePointerCapture &&
      this.drag.pointerId != null
    ) {
      try {
        event.currentTarget.releasePointerCapture(this.drag.pointerId)
      } catch {
        // 释放失败说明 capture 已被浏览器回收，状态清理仍然必须继续。
      }
    }
    if (this.drag.disableIframe && this.iframe) this.iframe.style.pointerEvents = ''
    this.drag = null
  }

  DocMindChatIframe.prototype._handleMessage = function (event) {
    var message = event.data || {}
    if (!message.type) return
    // WindowProxy 在 iframe 导航后仍可能不变，必须同时锁定来源窗口和初始 iframe origin。
    if (
      !this.iframe ||
      event.source !== this.iframe.contentWindow ||
      event.origin !== this.targetOrigin
    ) {
      return
    }
    switch (message.type) {
      case 'IFRAME_READY':
        this._sendInitialPayload()
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
      case 'WINDOW_DRAG_START':
        this._startIframeDrag(message.payload)
        break
      case 'WINDOW_DRAG_MOVE':
        this._moveDrag(this._pointFromIframePayload(message.payload || {}))
        break
      case 'WINDOW_DRAG_END':
        this._endDrag()
        break
      case 'CONVERSATION_CREATED':
        this._emit('conversationCreated', message.payload)
        break
      case 'MESSAGE_SENT':
        this._emit('messageSent', message.payload)
        break
      case 'UNREAD_COUNT_CHANGED':
        this._setUnreadCount(message.payload && message.payload.total_unread_count)
        break
      default:
        break
    }
  }

  DocMindChatIframe.prototype._setUnreadCount = function (value) {
    var count = Number(value) || 0
    var button = this.container && this.container.querySelector('.docmind-chat-restore')
    if (button) button.setAttribute('data-unread', count > 0 ? 'true' : 'false')
  }

  DocMindChatIframe.prototype._pageContentFromDocument = function () {
    return { title: document.title, url: location.href, html: document.documentElement.outerHTML }
  }

  DocMindChatIframe.prototype._requiredExternalPayload = function () {
    var payload = {
      source_system: stripText(this.options.source_system),
      external_user_id: stripText(this.options.external_user_id),
      external_user_name: stripText(this.options.external_user_name)
    }
    var missing = []
    ;[
      'source_system',
      'function_id',
      'business_id',
      'external_user_id',
      'external_user_name'
    ].forEach(function (key) {
      if (!stripText(this.options[key])) missing.push(key)
    }, this)
    if (missing.length) throw new Error('缺少 chat-iframe 初始化参数：' + missing.join(', '))
    return payload
  }

  DocMindChatIframe.prototype._conversationScopeKey = function () {
    return [this.options.source_system, this.options.function_id, this.options.business_id]
      .map(stripText)
      .join(':')
  }

  DocMindChatIframe.prototype._fetchTokenJson = function (url, payload, trustedBackendMode) {
    var fetchImpl = global.fetch || (typeof fetch !== 'undefined' ? fetch : null)
    if (!fetchImpl)
      return Promise.reject(new Error('当前浏览器不支持 fetch，无法获取 DocMind token'))
    return fetchImpl(url, {
      method: 'POST',
      credentials: trustedBackendMode ? 'include' : 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {}
        })
        .then(function (data) {
          if (!response.ok)
            throw new Error(data.detail || data.message || '获取 DocMind token 失败')
          if (!data.access_token) throw new Error('获取 DocMind token 失败：响应缺少 access_token')
          return data.access_token
        })
    })
  }

  DocMindChatIframe.prototype._resolveToken = function () {
    if (this.resolvedToken) return Promise.resolve(this.resolvedToken)
    if (this.tokenPromise) return this.tokenPromise
    var self = this
    this.tokenPromise = Promise.resolve()
      .then(function () {
        var payload = self._requiredExternalPayload()
        var trustedUrl = stripText(self.options.tokenExchangeUrl)
        var url = trustedUrl || joinApiUrl(self.apiBaseUrl, '/api/chat-iframe/token')
        return self._fetchTokenJson(url, payload, Boolean(trustedUrl))
      })
      .then(function (token) {
        self.resolvedToken = token
        return token
      })
      .catch(function (error) {
        self.tokenPromise = null
        throw error
      })
    return this.tokenPromise
  }

  DocMindChatIframe.prototype._configPayload = function (token, authError) {
    var payload = {
      token: token || null,
      apiBaseUrl: this.apiBaseUrl,
      agentId: this.options.agentId,
      conversationScopeKey: this._conversationScopeKey()
    }
    if (authError) payload.authError = authError
    return payload
  }

  DocMindChatIframe.prototype._sendConfig = function () {
    var self = this
    return this._resolveToken()
      .then(function (token) {
        self._sendToIframe('INIT_CONFIG', self._configPayload(token))
      })
      .catch(function (error) {
        self._sendToIframe(
          'INIT_CONFIG',
          self._configPayload(
            null,
            error && error.message ? error.message : '获取 DocMind token 失败'
          )
        )
      })
  }

  DocMindChatIframe.prototype._sendInitialPayload = function () {
    var self = this
    return this._sendConfig().then(function () {
      self._sendToIframe('WINDOW_STATE', { state: self.windowState })
      self._sendToIframe('PAGE_CONTENT', self.pageContent || self._pageContentFromDocument())
      self._sendToIframe('PAGE_FILES_UPDATED', self.pageFiles)
    })
  }

  DocMindChatIframe.prototype._sendToIframe = function (type, payload) {
    if (!this.iframe || !this.iframe.contentWindow) return
    this.iframe.contentWindow.postMessage(
      { type: type, payload: payload, timestamp: Date.now() },
      this.targetOrigin
    )
  }

  DocMindChatIframe.prototype._setWindowState = function (state, notify) {
    this.windowState = state
    this._endDrag()
    this.container.className = 'docmind-chat-iframe ' + state + ' ' + this.options.position
    if (state === 'normal') this._ensureNormalWindowVisible()
    if (state === 'minimized' || state === 'closed') this._resetFloatingPosition()
    if (notify !== false) this._sendToIframe('WINDOW_STATE', { state: state })
    this._emit('stateChange', { state: state })
    return this
  }

  DocMindChatIframe.prototype._resetFloatingPosition = function () {
    if (!this.container) return
    // 从 normal 回到悬浮态时清掉 normal 写入的 left/top，否则定位类会被 inline 样式覆盖，图标会跑到窗口左上。
    this.container.style.left = ''
    this.container.style.top = ''
    this.container.style.right = ''
    this.container.style.bottom = ''
  }

  DocMindChatIframe.prototype._ensureNormalWindowVisible = function () {
    if (!this.container || typeof window === 'undefined') return
    var margin = 12
    var viewportWidth =
      window.innerWidth || document.documentElement.clientWidth || this.options.width
    var viewportHeight =
      window.innerHeight || document.documentElement.clientHeight || this.options.height
    var width = Math.min(this.options.width, viewportWidth - margin * 2)
    var height = Math.min(this.options.height, viewportHeight - margin * 2)
    var left = this.container.offsetLeft
    var top = this.container.offsetTop
    var fits =
      left >= margin &&
      top >= margin &&
      left + width <= viewportWidth - margin &&
      top + height <= viewportHeight - margin
    if (fits) return
    // 从拖动后的悬浮入口展开时，优先保证完整可见；放不下就回到当前视口右下角。
    this.container.style.left =
      Math.max(margin, viewportWidth - width - this.options.offsetX) + 'px'
    this.container.style.top =
      Math.max(margin, viewportHeight - height - this.options.offsetY) + 'px'
    this.container.style.right = 'auto'
    this.container.style.bottom = 'auto'
  }

  global.DocMindChatIframe = DocMindChatIframe
})(typeof window !== 'undefined' ? window : this)
