<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChevronDown, ChevronRight, Wrench } from 'lucide-vue-next'
import type { ChatToolCall } from '@/types'

const props = withDefaults(defineProps<{ toolCalls?: ChatToolCall[] }>(), { toolCalls: () => [] })
const expanded = ref(false)
const title = computed(() => {
  if (props.toolCalls.length === 1) return `调用：${props.toolCalls[0].name}`
  return `已调用 ${props.toolCalls.length} 个工具`
})
</script>

<template>
  <section v-if="toolCalls.length" class="tool-calls-panel">
    <button type="button" class="tool-summary" @click="expanded = !expanded">
      <Wrench :size="14" />
      <span>{{ title }}</span>
      <component :is="expanded ? ChevronDown : ChevronRight" :size="14" />
    </button>
    <div v-if="expanded" class="tool-list">
      <article v-for="tool in toolCalls" :key="tool.id" class="tool-card">
        <strong>{{ tool.name }}</strong>
        <span>{{ tool.status === 'done' ? '已完成' : tool.status === 'error' ? '失败' : '进行中' }}</span>
        <pre v-if="tool.args">{{ JSON.stringify(tool.args, null, 2) }}</pre>
        <pre v-if="tool.result">{{ typeof tool.result === 'string' ? tool.result : JSON.stringify(tool.result, null, 2) }}</pre>
      </article>
    </div>
  </section>
</template>
