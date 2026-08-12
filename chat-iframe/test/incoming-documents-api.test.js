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

test('ingestIncomingDocument asks DocMind to download source urls', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json({ status: 'accepted', taskId: 'task-1' })
  }

  const response = await ingestIncomingDocument(
    [
      {
        name: 'incoming.pdf',
        source_url: 'https://oa.example.test/incoming.pdf',
        source_doc_id: 'DOC001',
        source_file_id: 'S001',
        document_metadata: {
          source_doc_id: 'DOC001',
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

  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, '/api/incoming-documents/ingest')
  assert.equal(calls[0].options.method, 'POST')
  assert.equal(calls[0].options.headers.Authorization, 'Bearer token-1')
  assert.equal(calls[0].options.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    source_system: 'oa',
    document_metadata: {
      source_doc_id: 'DOC001',
      document_number: '来文〔2026〕1号',
      title: '风险整改通知',
      incoming_type: '安全管理',
      source_unit: '安监部',
      incoming_date: '2026-07-09'
    },
    files: [
      {
        source_file_id: 'S001',
        filename: 'incoming.pdf',
        source_url: 'https://oa.example.test/incoming.pdf',
        is_main_file: false
      }
    ]
  })
  assert.equal(response.status, 'accepted')
})

test('ingestIncomingDocument uses source_file_id as the source file key', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return Response.json({ status: 'accepted' })
  }

  await ingestIncomingDocument([
    {
      name: 'incoming.pdf',
      source_file_id: 'S001',
      source_url: 'https://oa.example.test/incoming.pdf',
      source_doc_id: 'DOC001'
    }
  ])

  const body = JSON.parse(calls[0].options.body)
  assert.equal(calls.length, 1)
  assert.equal(body.source_doc_id, undefined)
  assert.equal(body.document_metadata.source_doc_id, 'DOC001')
  assert.equal(body.source_function_id, undefined)
  assert.equal(body.source_system, 'production')
  assert.deepEqual(body.files, [
    {
      source_file_id: 'S001',
      filename: 'incoming.pdf',
      source_url: 'https://oa.example.test/incoming.pdf',
      is_main_file: false
    }
  ])
})

test('ingestIncomingDocument rejects files from different source systems', async () => {
  await assert.rejects(
    () =>
      ingestIncomingDocument([
        {
          name: 'oa.pdf',
          source_system: 'oa',
          source_doc_id: 'DOC001',
          source_file_id: 'S001',
          source_url: 'https://oa.example.test/oa.pdf'
        },
        {
          name: 'erp.pdf',
          source_system: 'erp',
          source_doc_id: 'DOC001',
          source_file_id: 'S002',
          source_url: 'https://erp.example.test/erp.pdf'
        }
      ]),
    /同一份来文/
  )
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
