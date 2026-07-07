import assert from 'node:assert/strict'
import test from 'node:test'

import { ingestIncomingDocument, queryIncomingDocumentExtractions } from '../src/apis/incoming-documents.ts'

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
  delete globalThis.window
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

test('ingestIncomingDocument posts source url with bearer token', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json({ status: 'accepted', taskId: 'task-1' })
  }

  const response = await ingestIncomingDocument(
    { id: 'f1', name: 'incoming.pdf', sourceUrl: 'https://oa.example.test/incoming.pdf', sourceKey: 'S001' },
    'token-1'
  )

  assert.equal(calls[0].url, '/api/incoming-documents/ingest')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    sourceUrl: 'https://oa.example.test/incoming.pdf',
    sourceKey: 'S001',
    filename: 'incoming.pdf'
  })
  assert.equal(response.status, 'accepted')
})

test('queryIncomingDocumentExtractions returns local mock data when enabled by url', async () => {
  globalThis.window = { location: { search: '?mockExtraction=mixed' } }
  globalThis.fetch = async () => {
    throw new Error('mock should not call backend')
  }

  const response = await queryIncomingDocumentExtractions([
    { id: 'f1', name: 'ready.md' },
    { id: 'f2', name: 'missing.md' }
  ])

  assert.equal(response.items[0].matchStatus, 'matched')
  assert.equal(response.items[0].extractionStatus, 'ready')
  assert.equal(response.items[1].matchStatus, 'not_found')
  assert.equal(response.items[1].extractionStatus, 'not_found')
  assert.equal(response.items[1].reason, undefined)
  delete globalThis.window
})
