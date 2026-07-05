<script setup lang="ts">
import { shallowRef, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import 'highlight.js/styles/github.css'
import 'katex/dist/katex.min.css'

const props = withDefaults(defineProps<{ content?: string; compact?: boolean }>(), {
  content: '',
  compact: false
})

const rendered = shallowRef('')

watch(
  () => props.content,
  async (content, _, onCleanup) => {
    let expired = false
    onCleanup(() => {
      expired = true
    })
    const html = await renderMarkdown(content || '')
    if (!expired) rendered.value = html
  },
  { immediate: true }
)
</script>

<template>
  <div class="markdown-preview" :class="{ compact }" v-html="rendered"></div>
</template>
