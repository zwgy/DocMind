import test from 'node:test'
import assert from 'node:assert/strict'
import { apiUrl, setApiBaseUrl } from '../src/apis/api-url.ts'

test('apiUrl keeps same-origin paths when no api base is configured', () => {
  setApiBaseUrl('')
  assert.equal(apiUrl('/api/auth/token'), '/api/auth/token')
})

test('apiUrl prefixes api paths with configured api base', () => {
  setApiBaseUrl('http://192.168.1.220:5173/')
  assert.equal(apiUrl('/api/auth/token'), 'http://192.168.1.220:5173/api/auth/token')
})

test('apiUrl leaves absolute urls unchanged', () => {
  setApiBaseUrl('http://192.168.1.220:5173')
  assert.equal(apiUrl('https://example.com/api/auth/token'), 'https://example.com/api/auth/token')
})
