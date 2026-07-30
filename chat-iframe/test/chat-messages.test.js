import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const component = readFileSync(new URL('../src/components/ChatMessages.vue', import.meta.url), 'utf8')
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
  assert.match(component, /preloadRecentInlineSvgs/)
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
