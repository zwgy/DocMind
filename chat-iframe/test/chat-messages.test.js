import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const component = readFileSync(new URL('../src/components/ChatMessages.vue', import.meta.url), 'utf8')
const inputComponent = readFileSync(
  new URL('../src/components/ChatInput.vue', import.meta.url),
  'utf8'
)
const appComponent = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const pdfPreview = readFileSync(
  new URL('../src/components/PdfArtifactPreview.vue', import.meta.url),
  'utf8'
)
const styles = readFileSync(new URL('../src/assets/css/app.css', import.meta.url), 'utf8')

test('context summary renders all extraction items grouped by schema', () => {
  assert.match(component, /contextSummaryItemGroups/, '结构化摘要应按 schema 分组渲染')
  assert.match(component, /<details[\s\S]*class="context-summary-group"/, '分组应支持折叠展示')
  assert.doesNotMatch(component, /contextSummary\.items\.slice\(0,\s*3\)/, '小助手不应只展示前 3 条结构化结果')
})

test('context summary renders supplementary attachments without duplicating their structured results', () => {
  assert.match(component, /supplementaryAttachments/)
  assert.match(component, /class="item-row context-summary-attachment"/)
  assert.match(component, /<strong>摘要<\/strong>/)
  assert.match(component, /if \(!summary\?\.file\.is_main_file\) return \[\]/)
  assert.doesNotMatch(component, /contextSummaryAttachments/)
})

test('SVG artifacts use the existing image preview path', () => {
  assert.match(component, /\(artifact\.name \|\| artifact\.path\)/)
  assert.match(component, /artifactPreview\.kind === 'image'/)
  assert.match(component, /preloadInlineSvgs/)
  assert.doesNotMatch(
    component,
    /\.filter\(isInlineSvgArtifact\)\s*\.slice\(-3\)/,
    '所有 SVG 交付物都应直接展示，不能只预加载最近三项'
  )
  assert.match(component, /class="artifact-inline-preview"/)
  assert.match(component, /class="artifact-preview-image-viewport"/)
  assert.match(component, /class="artifact-inline-svg"/)
  assert.match(component, /\(\) => props\.token/)
  assert.match(component, /\[displayItems, \(\) => props\.threadId, \(\) => props\.token\]/)
  assert.match(
    styles,
    /\.artifact-preview-image-viewport img\s*\{[\s\S]*position:\s*absolute;[\s\S]*width:\s*calc\(100% - 24px\);[\s\S]*height:\s*calc\(100% - 24px\);[\s\S]*object-fit:\s*contain;/,
    '完整预览应按可用区域缩放整张静态图，不能只展示原始尺寸的局部'
  )
})

test('Office artifacts use authenticated PDF preview while preserving original download', () => {
  assert.match(component, /OFFICE_ARTIFACT_EXTENSIONS/)
  assert.match(component, /extension === 'pdf' \|\| OFFICE_ARTIFACT_EXTENSIONS\.has\(extension\)/)
  assert.match(
    component,
    /fetchThreadArtifact\(\s*props\.threadId,\s*artifact\.path,\s*props\.token,\s*false,\s*isOfficeArtifact\(artifact\)\s*\)/
  )
  assert.match(component, /fetchThreadArtifact\(props\.threadId, artifact\.path, props\.token, true\)/)
  assert.match(component, /defineAsyncComponent\(\(\) => import\('@\/components\/PdfArtifactPreview\.vue'\)\)/)
  assert.match(component, /<PdfArtifactPreview/)
  assert.doesNotMatch(component, /<iframe[\s\S]*artifactPreview\.kind === 'pdf'/)
  assert.match(pdfPreview, /from 'pdfjs-dist'/)
  assert.match(pdfPreview, /class="pdf-artifact-viewport"/)
  assert.match(pdfPreview, /title="缩小"/)
  assert.match(pdfPreview, /title="放大"/)
  assert.match(pdfPreview, /title="适合窗口"/)
  assert.match(
    pdfPreview,
    /\.pdf-artifact-viewport\s*\{[\s\S]*overflow:\s*auto;/,
    '多页 PDF 预览必须支持横向和纵向滚动'
  )
})

test('artifact download keeps the Blob alive until Chromium consumes the attached link', () => {
  assert.match(component, /document\.body\.appendChild\(link\)[\s\S]*link\.click\(\)[\s\S]*link\.remove\(\)/)
  assert.match(component, /window\.setTimeout\(\(\) => URL\.revokeObjectURL\(url\), 1000\)/)
})

test('streaming auto-scroll observes display item replacement without deep traversal', () => {
  assert.match(
    component,
    /watch\(\s*\[displayItems, showGeneratingStatus, showRunProgress, \(\) => props\.compacting\],[\s\S]*scrollStreamingToBottom,[\s\S]*\{ flush: 'post' \}\s*\)/
  )
  assert.match(component, /if \(props\.streaming\) void scrollToBottom\('smooth'\)/)
})

test('completed conversation history scrolls to its last message without animation', () => {
  assert.match(component, /historyScrollRequest\?: number/)
  assert.match(
    component,
    /watch\(\s*\(\) => props\.historyScrollRequest,[\s\S]*\(\) => void scrollToBottom\('auto'\),[\s\S]*\{ flush: 'post' \}\s*\)/
  )
})

test('empty chat explains available context and fills suggested incoming-document questions', () => {
  assert.match(component, /hasPageContent\?: boolean/)
  assert.match(component, /hasPageFiles\?: boolean/)
  assert.match(component, /可以询问当前页面，也可以直接提出其他问题。/)
  assert.match(component, /按标题、关键词、发文单位或时间查找已收录来文/)
  assert.match(component, /总结当前页面的主要内容/)
  assert.match(component, /提取当前来文的关键信息/)
  assert.match(component, /v-for="suggestion in questionSuggestions"/)
  assert.match(component, /suggestQuestion/)
  assert.match(component, /class="welcome-heading"/)
  assert.match(component, /class="welcome-suggestion-copy"/)
  assert.match(styles, /grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/)
  assert.match(styles, /@media \(max-width: 430px\)/)
  assert.match(styles, /welcome-subtitle[\s\S]*color:\s*var\(--gray-600\)/)
  assert.match(styles, /button:focus-visible[\s\S]*var\(--main-700\)/)
  assert.match(inputComponent, /defineExpose\(\{ setDraft \}\)/)
  assert.match(inputComponent, /textareaRef\.value\?\.focus\(\)/)
  assert.match(appComponent, /@suggest-question="fillSuggestedQuestion"/)
  assert.match(appComponent, /chatInputRef\.value\?\.setDraft\(question\)/)
})
