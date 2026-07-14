import assert from 'node:assert/strict'
import test from 'node:test'

import { uploadAttachment, uploadImage } from '../src/apis/chat.ts'
import {
  MAX_ATTACHMENT_FILES,
  MAX_ATTACHMENT_SIZE_BYTES,
  MAX_IMAGE_SIZE_BYTES,
  attachmentValidationError,
  imageValidationError
} from '../src/utils/attachment-limits.ts'

test('attachment limits reject oversize and over-count selections before upload', async () => {
  const files = Array.from({ length: MAX_ATTACHMENT_FILES + 1 }, (_, index) => new File(['x'], `file-${index}.txt`))
  assert.match(attachmentValidationError(files), /最多添加/)
  assert.match(attachmentValidationError([new File([new Uint8Array(MAX_ATTACHMENT_SIZE_BYTES + 1)], 'large.pdf')]), /超过 5 MB/)

  let fetchCalled = false
  globalThis.fetch = async () => {
    fetchCalled = true
    return Response.json({})
  }
  await assert.rejects(uploadAttachment(new File([new Uint8Array(MAX_ATTACHMENT_SIZE_BYTES + 1)], 'large.pdf')))
  assert.equal(fetchCalled, false)
})

test('image limits allow supported images only and block invalid uploads before fetch', async () => {
  assert.equal(imageValidationError(new File(['x'], 'ok.webp', { type: 'image/webp' })), '')
  assert.match(imageValidationError(new File(['x'], 'bad.svg', { type: 'image/svg+xml' })), /仅支持/)
  assert.match(imageValidationError(new File([new Uint8Array(MAX_IMAGE_SIZE_BYTES + 1)], 'large.png', { type: 'image/png' })), /超过 10 MB/)

  let fetchCalled = false
  globalThis.fetch = async () => {
    fetchCalled = true
    return Response.json({})
  }
  await assert.rejects(uploadImage(new File(['x'], 'bad.svg', { type: 'image/svg+xml' })))
  assert.equal(fetchCalled, false)
})
