import MarkdownIt from 'markdown-it'
import markdownItKatex from '@vscode/markdown-it-katex'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import csharp from 'highlight.js/lib/languages/csharp'
import css from 'highlight.js/lib/languages/css'
import cpp from 'highlight.js/lib/languages/cpp'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import go from 'highlight.js/lib/languages/go'
import javascript from 'highlight.js/lib/languages/javascript'
import java from 'highlight.js/lib/languages/java'
import json from 'highlight.js/lib/languages/json'
import markdownLanguage from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'
import taskLists from 'markdown-it-task-lists'

const katexPlugin =
  (markdownItKatex as unknown as { default?: typeof markdownItKatex }).default || markdownItKatex

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('csharp', csharp)
hljs.registerLanguage('css', css)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('dockerfile', dockerfile)
hljs.registerLanguage('go', go)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('java', java)
hljs.registerLanguage('json', json)
hljs.registerLanguage('markdown', markdownLanguage)
hljs.registerLanguage('python', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('yaml', yaml)

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
  if (value === 'cs') return 'csharp'
  if (value === 'c' || value === 'cc' || value === 'c++' || value === 'hpp') return 'cpp'
  if (value === 'yml') return 'yaml'
  if (value === 'sh' || value === 'shell') return 'bash'
  if (value === 'html' || value === 'vue') return 'xml'
  if (value === 'docker') return 'dockerfile'
  return value
}

const SAFE_SVG_TAGS = new Set([
  'svg',
  'path',
  'rect',
  'circle',
  'ellipse',
  'line',
  'polyline',
  'polygon',
  'text',
  'tspan',
  'defs',
  'lineargradient',
  'radialgradient',
  'stop',
  'clippath',
  'mask',
  'pattern',
  'marker',
  'use',
  'symbol',
  'title',
  'desc'
])

const SAFE_SVG_ATTRIBUTES = new Set([
  'xmlns',
  'viewbox',
  'width',
  'height',
  'x',
  'y',
  'x1',
  'y1',
  'x2',
  'y2',
  'cx',
  'cy',
  'r',
  'rx',
  'ry',
  'd',
  'points',
  'fill',
  'fill-opacity',
  'stroke',
  'stroke-width',
  'stroke-linecap',
  'stroke-linejoin',
  'stroke-opacity',
  'opacity',
  'transform',
  'font-family',
  'font-size',
  'font-weight',
  'text-anchor',
  'dominant-baseline',
  'offset',
  'stop-color',
  'stop-opacity',
  'gradientunits',
  'gradienttransform',
  'spreadmethod',
  'clip-path',
  'mask',
  'markerwidth',
  'markerheight',
  'refx',
  'refy',
  'orient',
  'preserveaspectratio',
  'id',
  'class',
  'role',
  'aria-label',
  'href',
  'xlink:href'
])

function sanitizeSvg(svg: string) {
  if (typeof DOMParser === 'undefined') return ''

  const svgDocument = new DOMParser().parseFromString(svg, 'image/svg+xml')
  const root = svgDocument.documentElement
  if (root.localName !== 'svg' || svgDocument.querySelector('parsererror')) return ''

  for (const element of [root, ...Array.from(root.querySelectorAll('*'))]) {
    if (!SAFE_SVG_TAGS.has(element.localName.toLowerCase())) {
      element.remove()
      continue
    }

    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase()
      const value = attribute.value.trim()
      const hasExternalReference =
        (name === 'href' || name === 'xlink:href') && !value.startsWith('#')
      const hasExternalPaint = value.includes('url(') && !/^url\(\s*#[\w-]+\s*\)$/i.test(value)
      if (!SAFE_SVG_ATTRIBUTES.has(name) || name.startsWith('on') || hasExternalReference || hasExternalPaint) {
        element.removeAttribute(attribute.name)
      }
    }
  }

  return root.outerHTML
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

const defaultFenceRenderer = markdown.renderer.rules.fence!
markdown.renderer.rules.fence = (tokens, index, options, environment, self) => {
  const token = tokens[index]
  if (normalizeLanguage(token.info) !== 'svg') {
    return defaultFenceRenderer(tokens, index, options, environment, self)
  }

  const svg = sanitizeSvg(token.content)
  if (!svg) return defaultFenceRenderer(tokens, index, options, environment, self)

  // SVG 先白名单清洗并作为图片载入，不能让模型输出直接进入 iframe 的 HTML 上下文。
  return `<figure class="svg-inline-render"><img src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}" alt="SVG 图示" /></figure>\n`
}

export async function renderMarkdown(content: string) {
  return markdown.render(String(content || ''))
}
