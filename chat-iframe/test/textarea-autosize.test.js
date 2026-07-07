import assert from 'node:assert/strict'
import test from 'node:test'

import { autosizeTextarea } from '../src/utils/textarea-autosize.ts'

test('autosizeTextarea grows until max height then enables scrolling', () => {
  const textarea = {
    scrollHeight: 240,
    style: { height: '48px', overflowY: 'hidden' }
  }

  autosizeTextarea(textarea, 180)

  assert.equal(textarea.style.height, '180px')
  assert.equal(textarea.style.overflowY, 'auto')
})

test('autosizeTextarea keeps short content scroll-free', () => {
  const textarea = {
    scrollHeight: 72,
    style: { height: '48px', overflowY: 'auto' }
  }

  autosizeTextarea(textarea, 180)

  assert.equal(textarea.style.height, '72px')
  assert.equal(textarea.style.overflowY, 'hidden')
})
