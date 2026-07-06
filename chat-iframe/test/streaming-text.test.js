import assert from 'node:assert/strict'
import test from 'node:test'

import { splitStreamingText } from '../src/utils/streaming-text.ts'

test('splitStreamingText splits text into small visible chunks', () => {
  assert.deepEqual(splitStreamingText('abcdefghij', 4), ['abcd', 'efgh', 'ij'])
})

test('splitStreamingText keeps short text as one chunk', () => {
  assert.deepEqual(splitStreamingText('abc', 4), ['abc'])
})
