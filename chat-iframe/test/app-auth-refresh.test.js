import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

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
  assert.match(source, /if \(!context\.config\.token\) \{/)
  assert.match(source, /避免无凭证请求把摘要卡片打成 401/)
  assert.match(source, /void refreshExtraction\(\)/)
})

test('page attachments are prepared by the parent SDK and block questions until parsing is ready', () => {
  assert.doesNotMatch(contextSource, /normalized\[0\]\.selected/)
  assert.doesNotMatch(inputSource, /if \(!next\.size && props\.selectedPageSourceFileId\)/)
  assert.match(source, /refreshExtraction\(filesForSelectedDocuments\(selectedPageFiles\), true\)/)
  assert.match(
    source,
    /context\.files\.filter\(\(file\) => selectedKeys\.has\(documentKey\(file\)\)\)/
  )
  assert.match(source, /context\.config\.parentFileIngest/)
  assert.match(source, /requestFileIngest\(files/)
  assert.match(source, /fileIngestPromises\.set\(key, tracked\)/)
  assert.match(source, /attachmentPreparationNotice\.value\?\.message/)
  assert.match(source, /class="attachment-preparation-status"/)
  assert.match(source, /showAttachmentPreparation\('ready', related\)/)
  assert.doesNotMatch(source, /ingestIncomingDocument\([\s\S]*?\.catch\(\(\) => null\)/)
  assert.match(source, /以下附件缺少下载地址，无法形成完整来文/)
  assert.match(source, /来文附件尚未准备完成，请稍后重试/)
})

test('attachment status stays scoped to the current business page and can reattach after switching back', () => {
  assert.match(source, /function filesAreOnCurrentPage\(files: IncomingPageFile\[\]\)/)
  assert.match(source, /if \(!filesAreOnCurrentPage\(files\)\) return/)
  assert.match(source, /context\.config\.conversationScopeKey/)
  assert.match(source, /\(pageContextKey, previousPageContextKey\) =>/)
  assert.match(source, /pageContextKey === previousPageContextKey/)
  assert.match(source, /results\.value = \{\}/)
  assert.match(source, /selectedPageFiles\.value = \[\]/)
  assert.match(source, /fileIngestPromises\.get\(key\) === tracked/)
  assert.match(
    source,
    /function resumeVisiblePage\(\) \{[\s\S]*refreshVisibleAttachment\(\)[\s\S]*\}/
  )
  assert.match(
    source,
    /state !== 'normal' && state !== 'maximized'[\s\S]*refreshVisibleAttachment\(\)/
  )
})

test('an uncertain upload result is reconciled against backend state before showing failure', () => {
  assert.match(source, /let transferAttempted = false/)
  assert.match(source, /if \(transferAttempted && filesAreOnCurrentPage\(queryFiles\)\)/)
  assert.match(source, /上传响应可能因刷新或网络中断丢失/)
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
    /watch\(\s*\(\) => context\.windowState,[\s\S]*state !== 'normal' && state !== 'maximized'/
  )
  assert.match(source, /previousState !== 'minimized' && previousState !== 'closed'/)
  assert.match(
    source,
    /requestAnimationFrame\(\(\) => \{\s*historyScrollRequest\.value \+= 1\s*\}\)/
  )
})
