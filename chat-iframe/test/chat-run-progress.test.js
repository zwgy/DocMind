import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/components/ChatMessages.vue', import.meta.url), 'utf8')
const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const inputSource = readFileSync(
  new URL('../src/components/ChatInput.vue', import.meta.url),
  'utf8'
)
const tokenUsageSource = readFileSync(
  new URL('../src/utils/token-usage.ts', import.meta.url),
  'utf8'
)
const chatStoreSource = readFileSync(new URL('../src/stores/chat.ts', import.meta.url), 'utf8')
const styles = readFileSync(new URL('../src/assets/css/app.css', import.meta.url), 'utf8')

test('active runs render Todo progress in the message list instead of a separate panel', () => {
  assert.match(appSource, /:agent-state="chat\.agentState"/)
  assert.match(appSource, /:show-run-progress="chat\.showRunTodos"/)
  assert.match(
    source,
    /const showRunProgress = computed\(\(\) => props\.streaming && props\.showRunProgress && runTodos\.value\.length > 0\)/
  )
  assert.match(source, /<section v-if="showRunProgress" class="run-progress-card"/)
  assert.match(source, /已完成 \{\{ completedTodoCount \}\}\/\{\{ runTodos\.length \}\}/)
  assert.match(styles, /\.run-progress-card \{/)
})

test('iframe polls agent state during streaming and hides unchanged persisted todos', () => {
  assert.match(chatStoreSource, /runtime\.showRunTodos = false/)
  assert.match(chatStoreSource, /const initialTodoSignature = todoSignature\(runtime\.agentState\)/)
  assert.match(chatStoreSource, /stateRefreshTimer = globalThis\.setInterval\(/)
  assert.match(chatStoreSource, /void getThreadState\(threadId, token\)/)
  assert.match(chatStoreSource, /todoSignature\(state\.agent_state\) !== initialTodoSignature/)
  assert.match(chatStoreSource, /globalThis\.clearInterval\(stateRefreshTimer\)/)
})

test('context usage is an optional input control next to model selection', () => {
  assert.match(appSource, /:token-usage="currentTokenUsage"/)
  assert.match(
    inputSource,
    /v-if="contextUsage" ref="contextUsageRef" class="context-usage-wrapper"/
  )
  assert.match(inputSource, /buildTokenUsageView\(props\.tokenUsage\)/)
  assert.match(inputSource, /本轮上下文用量/)
  assert.match(inputSource, /本轮安全输入上限/)
  assert.doesNotMatch(inputSource, /模型格式等额外开销/)
  assert.match(inputSource, /已自动压缩较早的对话/)
  assert.match(inputSource, /当前对话已收纳/)
  assert.match(tokenUsageSource, /usage\.input_tokens/)
  assert.match(tokenUsageSource, /usage\.tool_results_externalized/)
  assert.match(tokenUsageSource, /usage\.input_source/)
  assert.match(tokenUsageSource, /usage\.breakdown_estimate/)
  assert.match(tokenUsageSource, /usage\.context_window/)
  assert.match(tokenUsageSource, /usage\.prompt_budget/)
  assert.match(tokenUsageSource, /provider_usage: '实际用量'/)
  assert.match(tokenUsageSource, /calibrated_estimate: '校准后的估算'/)
  assert.doesNotMatch(tokenUsageSource, /llm_input_tokens|model_usage|summary_trigger_tokens/)
  assert.match(inputSource, /context-usage-legend/)
  assert.match(styles, /\.context-usage-popover \{/)
  assert.match(
    styles,
    /\.context-usage-legend i\.is-tools \{\s*background: var\(--color-warning-500\)/
  )
})

test('tool call icons use an explicit high-contrast color on white backgrounds', () => {
  assert.match(styles, /\.tool-card-summary > svg:first-child \{\s*color: var\(--main-700\)/)
})

test('artifacts stay attached to the final assistant message and use authenticated retrieval', () => {
  assert.match(source, /item\.message\.artifacts\?\.length/)
  assert.match(source, /fetchThreadArtifact\(props\.threadId, artifact\.path, props\.token/)
  assert.match(source, /artifact-preview-overlay/)
  assert.ok(
    source.indexOf('<section v-if="item.message.artifacts?.length"') <
      source.indexOf('<MessageRefs'),
    '交付物应紧随回答正文显示，不能落在反馈操作之后'
  )
})
