import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveAuthenticatedRedirect } from '../../../../web/src/utils/oidcAutoStart.js'

test('authenticated users without a business redirect enter the agent page', () => {
  assert.equal(resolveAuthenticatedRedirect(undefined), '/agent')
  assert.equal(resolveAuthenticatedRedirect('/'), '/agent')
})

test('authenticated users keep an explicit local business redirect', () => {
  assert.equal(resolveAuthenticatedRedirect('/scheduled-jobs'), '/scheduled-jobs')
})
