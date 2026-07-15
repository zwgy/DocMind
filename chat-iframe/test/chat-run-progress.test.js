import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/components/ChatMessages.vue', import.meta.url), 'utf8')
const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const inputSource = readFileSync(new URL('../src/components/ChatInput.vue', import.meta.url), 'utf8')
const styles = readFileSync(new URL('../src/assets/css/app.css', import.meta.url), 'utf8')

test('active runs render Todo progress in the message list instead of a separate panel', () => {
  assert.match(appSource, /:agent-state="chat\.agentState"/)
  assert.match(source, /const showRunProgress = computed\(\(\) => props\.streaming && runTodos\.value\.length > 0\)/)
  assert.match(source, /<section v-if="showRunProgress" class="run-progress-card"/)
  assert.match(source, /已完成 \{\{ completedTodoCount \}\}\/\{\{ runTodos\.length \}\}/)
  assert.match(styles, /\.run-progress-card \{/)
})

test('context usage is an optional input control next to model selection', () => {
  assert.match(appSource, /:token-usage="currentTokenUsage"/)
  assert.match(inputSource, /v-if="contextUsage" ref="contextUsageRef" class="context-usage-wrapper"/)
  assert.match(inputSource, /llm_input_tokens/)
  assert.match(inputSource, /context_window/)
  assert.match(styles, /\.context-usage-popover \{/)
})

test('artifacts stay attached to the final assistant message and use authenticated retrieval', () => {
  assert.match(source, /item\.message\.artifacts\?\.length/)
  assert.match(source, /fetchThreadArtifact\(props\.threadId, artifact\.path, props\.token/)
  assert.match(source, /artifact-preview-overlay/)
})
