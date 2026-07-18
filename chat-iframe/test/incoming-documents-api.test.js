import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ingestIncomingDocument,
  queryIncomingDocumentExtractions
} from '../src/apis/incoming-documents.ts'

test('queryIncomingDocumentExtractions posts files with bearer token', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json({ items: [{ incomingFileId: 'f1', matchStatus: 'matched' }] })
  }

  const response = await queryIncomingDocumentExtractions(
    [{ source_file_id: 'S001', name: 'incoming.pdf' }],
    'token-1'
  )

  assert.equal(calls[0].url, '/api/incoming-documents/extractions/query')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    files: [{ source_file_id: 'S001', name: 'incoming.pdf' }]
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
    () => queryIncomingDocumentExtractions([{ source_file_id: 'S001', name: 'incoming.pdf' }]),
    /502/
  )
})

test('ingestIncomingDocument downloads file and posts multipart with bearer token', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url === 'https://oa.example.test/incoming.pdf') {
      return new Response(new Blob(['pdf-content'], { type: 'application/pdf' }))
    }
    return Response.json({ status: 'accepted', taskId: 'task-1' })
  }

  const response = await ingestIncomingDocument(
    [
      {
        name: 'incoming.pdf',
        source_url: 'https://oa.example.test/incoming.pdf',
        source_doc_id: 'DOC001',
        source_function_id: 'incomingDocument',
        source_file_id: 'S001',
        document_metadata: {
          document_number: '来文〔2026〕1号',
          title: '风险整改通知',
          incoming_type: '安全管理',
          source_unit: '安监部',
          incoming_date: '2026-07-09'
        }
      }
    ],
    'token-1',
    { source_system: 'oa' }
  )

  assert.equal(calls[0].url, 'https://oa.example.test/incoming.pdf')
  assert.equal(calls[0].options.cache, 'no-store')
  assert.equal(calls[1].url, '/api/incoming-documents/ingest')
  assert.equal(calls[1].options.method, 'POST')
  assert.equal(calls[1].options.headers.Authorization, 'Bearer token-1')
  const form = calls[1].options.body
  assert.ok(form instanceof FormData)
  assert.equal(form.get('source_doc_id'), 'DOC001')
  assert.equal(form.get('source_function_id'), 'incomingDocument')
  assert.equal(form.get('source_system'), 'oa')
  assert.deepEqual(JSON.parse(form.get('document_metadata')), {
    document_number: '来文〔2026〕1号',
    title: '风险整改通知',
    incoming_type: '安全管理',
    source_unit: '安监部',
    incoming_date: '2026-07-09'
  })
  assert.deepEqual(JSON.parse(form.get('file_metas')), [
    { source_file_id: 'S001', filename: 'incoming.pdf', is_main_file: false }
  ])
  assert.equal(form.get('files').name, 'incoming.pdf')
  assert.equal(response.status, 'accepted')
})

test('ingestIncomingDocument uses source_file_id as the source file key', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    if (url === 'https://oa.example.test/incoming.pdf')
      return new Response(new Blob(['pdf-content']))
    return Response.json({ status: 'accepted' })
  }

  await ingestIncomingDocument([
    {
      name: 'incoming.pdf',
      source_file_id: 'S001',
      source_url: 'https://oa.example.test/incoming.pdf',
      source_function_id: 'incomingDocument',
      source_doc_id: 'DOC001'
    }
  ])

  const form = calls[1].options.body
  assert.equal(calls[0].options.cache, 'no-store')
  assert.equal(form.get('source_doc_id'), 'DOC001')
  assert.equal(form.get('source_function_id'), 'incomingDocument')
  assert.equal(form.get('source_system'), 'production')
  assert.deepEqual(JSON.parse(form.get('file_metas')), [
    { source_file_id: 'S001', filename: 'incoming.pdf', is_main_file: false }
  ])
})

test('queryIncomingDocumentExtractions returns local mock data when enabled by url', async () => {
  globalThis.window = { location: { search: '?mockExtraction=mixed' } }
  globalThis.fetch = async () => {
    throw new Error('mock should not call backend')
  }

  const response = await queryIncomingDocumentExtractions([
    { source_file_id: 'S001', name: 'ready.md' },
    { source_file_id: 'S002', name: 'missing.md' }
  ])

  assert.equal(response.items[0].matchStatus, 'matched')
  assert.equal(response.items[0].extractionStatus, 'ready')
  assert.equal(response.items[1].matchStatus, 'not_found')
  assert.equal(response.items[1].extractionStatus, 'not_found')
  assert.equal(response.items[1].reason, undefined)
  delete globalThis.window
})
