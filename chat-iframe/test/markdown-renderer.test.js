import assert from 'node:assert/strict'
import test from 'node:test'

import { renderMarkdown } from '../src/utils/markdown.ts'

test('renderMarkdown renders tables, code blocks and math safely', async () => {
  const html = await renderMarkdown(
    [
      '| 字段 | 值 |',
      '| --- | --- |',
      '| 风险 | 高 |',
      '',
      '```js',
      'const answer = 42',
      '```',
      '',
      '$$x^2$$',
      '',
      '<script>alert(1)</script>'
    ].join('\n')
  )

  assert.match(html, /<table>/)
  assert.match(html, /language-js|hljs/)
  assert.match(html, /katex/)
  assert.doesNotMatch(html, /<script>/)
})
