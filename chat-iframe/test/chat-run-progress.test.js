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
  assert.match(inputSource, /model_usage/)
  assert.match(inputSource, /input_tokens/)
  assert.match(inputSource, /llm_message_count/)
  assert.match(inputSource, /summary_trigger_tokens/)
  assert.match(inputSource, /const limit = contextWindow \|\| Math\.max\(used, 1\)/)
  assert.match(inputSource, /自动摘要阈值（估算）/)
  assert.match(inputSource, /模型窗口剩余/)
  assert.doesNotMatch(inputSource, /const limit = summaryTrigger \|\| contextWindow/)
  assert.match(inputSource, /const TOKEN_COUNT_K_UNIT = 1024/)
  assert.match(inputSource, /toFixed\(digits\)\.replace\(\/\\\.0\+\$\//)
  assert.match(inputSource, /label: '系统'/)
  assert.match(inputSource, /label: `工具 \(\$\{tokenNumber\(usage\.tool_count\) \|\| 0\}\)`/)
  assert.match(inputSource, /if \(used === null\) return null/)
  assert.match(inputSource, /context-usage-legend/)
  assert.match(inputSource, /v-if="segment\.messageCount"> \(\{\{ segment\.messageCount \}\}\)<\/template>/)
  assert.match(styles, /\.context-usage-popover \{/)
  assert.match(styles, /\.context-usage-legend i\.is-tools \{\s*background: var\(--color-warning-500\)/)
})

test('tool call icons use an explicit high-contrast color on white backgrounds', () => {
  assert.match(styles, /\.tool-card-summary > svg:first-child \{\s*color: var\(--main-700\)/)
})

test('artifacts stay attached to the final assistant message and use authenticated retrieval', () => {
  assert.match(source, /item\.message\.artifacts\?\.length/)
  assert.match(source, /fetchThreadArtifact\(props\.threadId, artifact\.path, props\.token/)
  assert.match(source, /artifact-preview-overlay/)
  assert.ok(
    source.indexOf('<section v-if="item.message.artifacts?.length"') < source.indexOf('<MessageRefs'),
    '交付物应紧随回答正文显示，不能落在反馈操作之后'
  )
})
