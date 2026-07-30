import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

const nginxConfig = readFileSync(join(import.meta.dirname, '../nginx.conf'), 'utf8')

test('stable iframe entry files disable browser caching', () => {
  assert.match(
    nginxConfig,
    /location = \/chat-iframe\/index\.html\s*\{[^}]*add_header Cache-Control "no-store";[^}]*\}/
  )
  assert.match(
    nginxConfig,
    /location = \/chat-iframe\/docmind-chat-iframe-parent\.js\s*\{[^}]*add_header Cache-Control "no-store";[^}]*\}/
  )
})

test('PDF.js module worker uses a browser-executable MIME type', () => {
  assert.match(
    nginxConfig,
    /location ~ \\\s*\.mjs\$\s*\{[^}]*default_type application\/javascript;[^}]*try_files \$uri =404;[^}]*\}/
  )
})
