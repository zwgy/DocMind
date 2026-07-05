import assert from 'node:assert/strict'
import test from 'node:test'

import { queryIncomingDocumentExtractions } from '../src/apis/incoming-documents.ts'

test('queryIncomingDocumentExtractions posts files with bearer token', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json({ items: [{ incomingFileId: 'f1', matchStatus: 'matched' }] })
  }

  const response = await queryIncomingDocumentExtractions([{ id: 'f1', name: 'incoming.pdf' }], 'token-1')

  assert.equal(calls[0].url, '/api/incoming-documents/extractions/query')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    files: [{ id: 'f1', name: 'incoming.pdf' }]
  })
  assert.equal(response.items[0].incomingFileId, 'f1')
})

test('queryIncomingDocumentExtractions reports non-json http status', async () => {
  globalThis.fetch = async () =>
    new Response('bad gateway', {
      status: 502,
      headers: { 'Content-Type': 'text/plain' }
    })

  await assert.rejects(
    () => queryIncomingDocumentExtractions([{ id: 'f1', name: 'incoming.pdf' }]),
    /502/
  )
})
