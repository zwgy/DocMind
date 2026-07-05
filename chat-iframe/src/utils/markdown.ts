import MarkdownIt from 'markdown-it'
import markdownItKatex from '@vscode/markdown-it-katex'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdownLanguage from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import taskLists from 'markdown-it-task-lists'

const katexPlugin =
  (markdownItKatex as unknown as { default?: typeof markdownItKatex }).default || markdownItKatex

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('css', css)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('markdown', markdownLanguage)
hljs.registerLanguage('python', python)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)

function escapeHtml(value: unknown) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function normalizeLanguage(language = '') {
  const value = language.trim().toLowerCase()
  if (value === 'ts') return 'typescript'
  if (value === 'js') return 'javascript'
  if (value === 'py') return 'python'
  return value
}

function renderSvgFences(content: string) {
  return content.replace(/```svg\s*([\s\S]*?)```/gi, (_, svg) => {
    // iframe 面向外部系统嵌入，Markdown 不能直接放开任意 HTML；SVG 作为源码展示最稳。
    return `<div class="svg-inline-render"><pre><code>${escapeHtml(svg)}</code></pre></div>`
  })
}

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: true,
  highlight(code: string, lang: string) {
    const language = normalizeLanguage(lang)
    if (language && hljs.getLanguage(language)) {
      try {
        return hljs.highlight(code, { language }).value
      } catch {
        // 高亮失败时退回转义文本，避免一段代码拖垮整条消息渲染。
      }
    }
    return escapeHtml(code)
  }
})
  .use(katexPlugin, { throwOnError: false, errorColor: '#cc0000', trust: false })
  .use(taskLists, { enabled: false, label: false, labelAfter: false })

export async function renderMarkdown(content: string) {
  const source = renderSvgFences(String(content || ''))
  return markdown.render(source)
}
