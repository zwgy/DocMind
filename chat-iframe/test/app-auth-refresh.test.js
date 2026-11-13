import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import test from 'node:test'
import { runInNewContext } from 'node:vm'

const source = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const appCssSource = readFileSync(new URL('../src/assets/css/app.css', import.meta.url), 'utf8')
const contextSource = readFileSync(
  new URL('../src/stores/iframe-context.ts', import.meta.url),
  'utf8'
)
const inputSource = readFileSync(
  new URL('../src/components/ChatInput.vue', import.meta.url),
  'utf8'
)
const messagesSource = readFileSync(
  new URL('../src/components/ChatMessages.vue', import.meta.url),
  'utf8'
)

test('refreshExtraction waits for token before querying extraction api', () => {
  assert.match(source, /if \(!attachmentPreparationEnabled\.value\) \{/)
  assert.match(source, /if \(!context\.config\.token\) \{/)
  assert.match(source, /避免无凭证请求把摘要卡片打成 401/)
  assert.match(source, /void refreshExtraction\(\)/)
})

test('concurrent extraction refreshes for the same document reuse one request', () => {
  assert.match(source, /const extractionRefreshPromises = new Map<string, Promise<boolean>>\(\)/)
  assert.match(source, /const existing = extractionRefreshPromises\.get\(key\)/)
  assert.match(source, /if \(existing\) return existing/)
  assert.match(source, /const operation = refreshExtractionOnce\(queryFiles, syncPending\)/)
  assert.match(source, /extractionRefreshPromises\.set\(key, tracked\)/)
  assert.match(source, /extractionRefreshPromises\.clear\(\)/)
})

test('ready extraction summaries are reused without querying again', () => {
  assert.match(
    source,
    /result\?\.matchStatus === 'matched' && result\.extractionStatus === 'ready'/
  )
  assert.match(source, /摘要一旦 ready 就是当前页面可复用的终态/)
})

test('page attachments continue preparing independently of submitted questions', () => {
  assert.doesNotMatch(contextSource, /normalized\[0\]\.selected/)
  assert.doesNotMatch(inputSource, /if \(!next\.size && props\.selectedPageSourceFileId\)/)
  assert.match(
    source,
    /context\.files\.filter\(\(file\) => selectedKeys\.has\(documentKey\(file\)\)\)/
  )
  assert.match(source, /await ingestIncomingDocument\(files, context\.config\.token\)/)
  assert.match(source, /attachmentPreparationPromises\.set\(key, tracked\)/)
  assert.match(
    source,
    /queryFiles: IncomingPageFile\[\] = selectedFile\.value\s*\? filesForSelectedDocuments\(\[selectedFile\.value\]\)/
  )
  assert.match(source, /同一来文可以整组同步到后端/)
  assert.match(source, /selectedPageFiles,\s*\n\s*extractionResults:/)
  assert.doesNotMatch(source, /selectedPageFiles: selectedDocumentFiles/)
  assert.match(source, /class="attachment-preparation-status"/)
  assert.match(source, /showAttachmentPreparation\('ready', related\)/)
  assert.doesNotMatch(source, /ingestIncomingDocument\([\s\S]*?\.catch\(\(\) => null\)/)
  assert.match(source, /以下附件缺少下载地址，无法形成完整来文/)
})

test('questions are submitted with the current attachment state without waiting for extraction', async () => {
  // 执行真实提交函数，只替换网络边界，确保未完成附件不会吞掉本轮问题。
  const sendSource = source.slice(
    source.indexOf('async function sendChat('),
    source.indexOf('\nfunction windowDragPayload')
  )
  for (const status of ['pending', 'parsing', 'extracting', 'ready']) {
    const file = { name: '测试来文.txt', source_file_id: 'pending-file' }
    const selectedFiles = [file]
    const extraction = {
      matchStatus: 'matched',
      extractionStatus: status,
      hasParsedMarkdown: status === 'extracting' || status === 'ready'
    }
    const sent = []
    const restored = []
    const notifications = []
    const context = {
      pageContent: { text: '测试页面正文' },
      config: { token: 'test-token', agentId: 'test-agent', conversationScopeKey: 'test-scope' }
    }
    const send = runInNewContext(`(${stripTypeScriptTypes(sendSource)})`, {
      context,
      results: { value: { 'pending-file': extraction } },
      hasPageContent: { value: true },
      filesForSelectedDocuments: (files) => files,
      refreshExtraction: async () => false,
      attachmentPreparationNotice: { value: { message: '附件处理中' } },
      error: { value: '' },
      chat: {
        send: async (options, ...identity) => {
          sent.push({ options, identity })
          return { threadId: 'test-thread', messageId: 'test-message' }
        }
      },
      notifyMessageSent: (message) => notifications.push(message)
    })
    await send({
      text: '请介绍一下安全培训的组织方法',
      files: [],
      selectedPageFiles: selectedFiles,
      restoreUploadDraft: (retry) => restored.push(retry)
    })
    assert.equal(sent.length, 1, `${status} 状态仍应提交问题`)
    assert.equal(sent[0].options.text, '请介绍一下安全培训的组织方法')
    assert.equal(sent[0].options.selectedPageFiles, selectedFiles)
    assert.equal(sent[0].options.extractionResult, extraction)
    assert.equal(sent[0].options.pageContent, context.pageContent)
    assert.equal(sent[0].identity.join(','), 'test-token,test-agent,test-scope')
    assert.equal(restored.length, 0)
    assert.equal(notifications[0].conversationId, 'test-thread')
  }
})

test('attachment status stays scoped to the current business page and can reattach after switching back', () => {
  assert.match(source, /function filesAreOnCurrentPage\(files: IncomingPageFile\[\]\)/)
  assert.match(source, /if \(!filesAreOnCurrentPage\(files\)\) return/)
  assert.match(source, /context\.config\.conversationScopeKey/)
  assert.match(source, /\(pageContextKey, previousPageContextKey\) =>/)
  assert.match(source, /pageContextKey === previousPageContextKey/)
  assert.match(source, /results\.value = \{\}/)
  assert.match(source, /selectedPageFiles\.value = \[\]/)
  assert.match(source, /attachmentPreparationPromises\.get\(key\) === tracked/)
  assert.match(
    source,
    /function resumeVisiblePage\(\) \{[\s\S]*refreshVisibleAttachment\(\)[\s\S]*\}/
  )
  assert.match(
    source,
    /state !== 'normal' && state !== 'maximized'[\s\S]*refreshVisibleAttachment\(\)/
  )
})

test('an uncertain backend submission is reconciled before showing failure', () => {
  assert.match(source, /let transferAttempted = false/)
  assert.match(source, /if \(transferAttempted && filesAreOnCurrentPage\(queryFiles\)\)/)
  assert.match(source, /后端提交响应可能因刷新或网络中断丢失/)
  assert.match(source, /result\?\.matchStatus === 'matched'/)
})

test('attachment status uses the flexible workbench row below an optional notification ticker', () => {
  assert.match(source, /<section v-if="tickerItems\.length" class="notification-ticker"/)
  assert.match(source, /<section class="workbench">\s*<section class="conversation-stage">/)
  assert.match(appCssSource, /\.chat-body \{[\s\S]*grid-template-rows: auto minmax\(0, 1fr\)/)
  assert.match(
    appCssSource,
    /\.conversation-stage \{[\s\S]*grid-template-rows: auto minmax\(0, 1fr\)/
  )
  assert.match(
    appCssSource,
    /\.attachment-preparation-status \{[\s\S]*justify-content: center;[\s\S]*text-align: center;/
  )
})

test('embedded attachment preparation starts only after the assistant first becomes visible', () => {
  assert.match(contextSource, /windowStateInitialized: boolean/)
  assert.match(contextSource, /windowStateInitialized: false/)
  assert.match(contextSource, /this\.windowStateInitialized = true/)
  assert.match(source, /const attachmentPreparationEnabled = ref\(false\)/)
  assert.match(
    source,
    /\[\(\) => context\.windowStateInitialized, \(\) => context\.windowState\] as const/
  )
  assert.match(
    source,
    /if \(state !== 'normal' && state !== 'maximized'\) return[\s\S]*attachmentPreparationEnabled\.value = true[\s\S]*refreshVisibleAttachment\(\)/
  )
  assert.match(
    source,
    /if \(!context\.isEmbedded\) \{\s*attachmentPreparationEnabled\.value = true\s*void refreshExtraction\(\)/
  )
})

test('structured extraction items are folded by default', () => {
  assert.doesNotMatch(messagesSource, /class="context-summary-group"\s+open/)
})

test('conversation selection requests bottom positioning after history has loaded', () => {
  assert.match(source, /await chat\.selectThread\(threadId, context\.config\.token\)/)
  assert.match(source, /historyScrollRequest\.value \+= 1/)
  assert.match(source, /:history-scroll-request="historyScrollRequest"/)
})

test('reopening the conversation sidebar preserves loaded pagination', () => {
  assert.match(
    source,
    /function openSidebar\(\) \{\s*showSidebar\.value = true\s*\/\/[^\n]*\n\s*if \(chat\.threads\.length\) return\s*void chat\.refreshThreads\(/
  )
  assert.match(source, /@refresh="\s*chat\.refreshThreads\(/)
})

test('restoring a hidden assistant requests bottom positioning after layout becomes visible', () => {
  assert.match(
    source,
    /watch\(\s*\[\(\) => context\.windowStateInitialized, \(\) => context\.windowState\][\s\S]*state !== 'normal' && state !== 'maximized'/
  )
  assert.match(
    source,
    /previousWindowStateInitialized[\s\S]*previousState !== 'minimized'[\s\S]*previousState !== 'closed'/
  )
  assert.match(
    source,
    /requestAnimationFrame\(\(\) => \{\s*historyScrollRequest\.value \+= 1\s*\}\)/
  )
})
