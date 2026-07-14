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

test('renderMarkdown recognizes common language aliases and keeps SVG as safe output', async () => {
  const html = await renderMarkdown(
    [
      '```sql',
      'SELECT * FROM documents',
      '```',
      '',
      '```yml',
      'enabled: true',
      '```',
      '',
      '```c++',
      'int main() { return 0; }',
      '```',
      '',
      '```svg',
      '<svg onload="alert(1)"><script>alert(1)</script><rect width="10" height="10" /></svg>',
      '```'
    ].join('\n')
  )

  assert.match(html, /language-sql|hljs/)
  assert.match(html, /language-yml|hljs/)
  assert.match(html, /language-c\+\+|hljs/)
  assert.doesNotMatch(html, /<svg/)
  assert.doesNotMatch(html, /<script>/)
  assert.doesNotMatch(html, /<svg[^>]*onload=/)
})
