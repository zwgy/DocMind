import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { modelIcons } from '../../../../web/src/utils/modelIcon.js'
import { brandIcons } from '../../../../web/src/utils/kb_utils.js'

const publicDirectory = new URL('../../../../web/public/', import.meta.url)

test('模型厂商与知识库图标均使用随网页交付的有效 SVG', () => {
  const urls = new Set([
    ...Object.values(modelIcons),
    ...Object.values(brandIcons).map((icon) => icon({}).props.src)
  ])
  for (const url of urls) {
    assert.match(url, /^\/icons\/brands\/[\w-]+\.svg$/, `图标不能依赖外网：${url}`)
    const svg = readFileSync(fileURLToPath(new URL(url.slice(1), publicDirectory)), 'utf8')
    assert.match(svg, /<svg\b/, `图标内容必须是 SVG：${url}`)
    assert.doesNotMatch(svg, /<(?:script|foreignObject|image)\b|(?:href|src)=["']https?:/i)
  }
})
