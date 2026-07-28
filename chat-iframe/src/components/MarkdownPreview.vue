<script setup lang="ts">
import { nextTick, ref, shallowRef, watch } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import { copyToClipboard } from '@/utils/clipboard'
import 'highlight.js/styles/github.css'
import 'katex/dist/katex.min.css'

const props = withDefaults(defineProps<{ content?: string; compact?: boolean }>(), {
  content: '',
  compact: false
})

const rendered = shallowRef('')
const previewRef = ref<HTMLElement | null>(null)
const copiedTimers = new WeakMap<HTMLButtonElement, number>()

function enhanceCodeBlocks() {
  const root = previewRef.value
  if (!root) return

  root.querySelectorAll('pre').forEach((pre) => {
    if (pre.closest('.markdown-code-block')) return

    const parent = pre.parentNode
    if (!parent) return

    const wrapper = document.createElement('div')
    wrapper.className = 'markdown-code-block'
    parent.insertBefore(wrapper, pre)
    wrapper.appendChild(pre)

    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'markdown-code-copy-btn'
    button.textContent = '复制'
    button.setAttribute('aria-label', '复制代码')
    button.setAttribute('title', '复制代码')
    wrapper.appendChild(button)
  })
}

async function handleMarkdownClick(event: MouseEvent) {
  const target = event.target instanceof Element ? event.target : null
  const button = target?.closest<HTMLButtonElement>('.markdown-code-copy-btn')
  if (!button) return

  const code = button.closest('.markdown-code-block')?.querySelector('pre code, pre')?.textContent
  if (!code || !(await copyToClipboard(code))) return

  const originalText = button.dataset.originalText || button.textContent || '复制'
  button.dataset.originalText = originalText
  button.textContent = '已复制'
  const timer = copiedTimers.get(button)
  if (timer) window.clearTimeout(timer)
  copiedTimers.set(
    button,
    window.setTimeout(() => {
      button.textContent = button.dataset.originalText || originalText
      copiedTimers.delete(button)
    }, 1500)
  )
}

watch(
  () => props.content,
  async (content, _, onCleanup) => {
    let expired = false
    onCleanup(() => {
      expired = true
    })
    const html = await renderMarkdown(content || '')
    if (!expired) {
      rendered.value = html
      await nextTick()
      if (!expired) enhanceCodeBlocks()
    }
  },
  { immediate: true }
)
</script>

<template>
  <div
    ref="previewRef"
    class="markdown-preview"
    :class="{ compact }"
    v-html="rendered"
    @click="handleMarkdownClick"
  ></div>
</template>

<style scoped>
.markdown-preview :deep(.markdown-code-block) {
  position: relative;
}

.markdown-preview :deep(.markdown-code-block > pre) {
  padding-right: 56px;
}

.markdown-preview :deep(.markdown-code-copy-btn) {
  position: absolute;
  top: 8px;
  right: 8px;
  padding: 2px 7px;
  border: 1px solid var(--border-color, #d9d9d9);
  border-radius: 4px;
  background: var(--panel-bg, #fff);
  color: var(--muted-color, #666);
  font-size: 12px;
  cursor: pointer;
}
</style>
