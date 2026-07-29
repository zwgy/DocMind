<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, Workflow } from 'lucide-vue-next'
import MarkdownPreview from '@/components/MarkdownPreview.vue'
import ToolCallsPanel from '@/components/ToolCallsPanel.vue'
import type { ChatMessage } from '@/types'
import { executionProcessShouldExpand } from '@/utils/message-display'
import { normalizeToolCalls, skillName } from '@/utils/tool-calls'

const props = withDefaults(
  defineProps<{
    messages?: ChatMessage[]
    isActive?: boolean
    hasFinalAnswer?: boolean
  }>(),
  {
    messages: () => [],
    isActive: false,
    hasFinalAnswer: false
  }
)

const expanded = ref(true)
const openReasoning = ref<Record<string, boolean>>({})
const toolCalls = computed(() =>
  // 摘要必须与工具面板使用同一可见工具口径，避免展示交付物等内部工具导致实时与历史计数不同。
  normalizeToolCalls(props.messages.flatMap((message) => message.toolCalls || []))
)
const skillCount = computed(
  () => new Set(toolCalls.value.map(skillName).filter(Boolean)).size
)
const hasFailure = computed(() =>
  props.messages.some(
    (message) =>
      message.status === 'error' ||
      message.status === 'stopped' ||
      Boolean(message.errorMessage) ||
      message.toolCalls?.some((tool) => tool.status === 'error')
  )
)

watch(
  [() => props.isActive, hasFailure, () => props.hasFinalAnswer],
  ([isActive, failed, hasFinalAnswer]) => {
    // 运行中、失败或尚无最终回答时保持展开；成功产生最终回答后自动收起。
    expanded.value = executionProcessShouldExpand(isActive, failed, hasFinalAnswer)
  },
  { immediate: true }
)

const statusText = computed(() => {
  if (hasFailure.value) return '存在失败'
  if (props.isActive) return '进行中'
  if (!props.hasFinalAnswer) return '等待最终回答'
  return '已完成'
})

const summaryText = computed(() => {
  return [
    skillCount.value ? `${skillCount.value} 个 Skill` : '',
    toolCalls.value.length ? `${toolCalls.value.length} 个工具` : ''
  ]
    .filter(Boolean)
    .join(' · ')
})

function fallbackToolEvents(message: ChatMessage) {
  // 结构化工具调用已包含完整状态；旧事件文本仅在没有结构化数据时兜底展示，避免重复。
  return message.toolCalls?.length ? [] : message.toolEvents || []
}
</script>

<template>
  <section class="execution-process-panel" :class="{ 'has-failure': hasFailure }">
    <button
      type="button"
      class="execution-process-summary"
      :class="{ 'is-expanded': expanded }"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <Workflow :size="15" />
      <span>执行过程</span>
      <small v-if="summaryText">{{ summaryText }}</small>
      <em>{{ statusText }}</em>
      <component :is="expanded ? ChevronDown : ChevronRight" :size="14" />
    </button>

    <div v-if="expanded" class="execution-process-list">
      <template v-for="message in messages" :key="message.id">
        <article
          v-if="message.content || message.reasoningContent || message.errorMessage"
          class="execution-stage-message"
        >
          <details
            v-if="message.reasoningContent"
            class="reasoning-box"
            :open="openReasoning[message.id]"
          >
            <summary
              @click.prevent="openReasoning[message.id] = !openReasoning[message.id]"
            >
              {{ message.status === 'streaming' ? '正在思考...' : '推理过程' }}
            </summary>
            <p>{{ message.reasoningContent }}</p>
          </details>
          <MarkdownPreview v-if="message.content" :content="message.content" />
          <p
            v-if="message.errorMessage && message.errorMessage !== message.content"
            class="error-hint"
          >
            {{ message.errorMessage }}
          </p>
        </article>

        <ToolCallsPanel
          v-if="message.toolCalls?.length"
          :tool-calls="message.toolCalls"
          :is-active="isActive"
        />

        <div
          v-for="(event, eventIndex) in fallbackToolEvents(message)"
          :key="`${message.id}-event-${eventIndex}`"
          class="execution-tool-event"
        >
          {{ event }}
        </div>
      </template>
    </div>
  </section>
</template>
