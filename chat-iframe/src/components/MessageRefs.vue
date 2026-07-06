<script setup lang="ts">
import { Check, Copy, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import { ref } from 'vue'
import type { ChatMessage } from '@/types'

defineProps<{ message: ChatMessage }>()
const emit = defineEmits<{
  retry: []
  feedback: [payload: { messageId: string; rating: 'like' | 'dislike'; reason: string | null }]
}>()

const copied = ref(false)

async function copyText(text: string) {
  await navigator.clipboard?.writeText(text)
  copied.value = true
  window.setTimeout(() => {
    copied.value = false
  }, 1500)
}
</script>

<template>
  <footer class="message-refs">
    <button type="button" title="点赞" @click="emit('feedback', { messageId: message.id, rating: 'like', reason: null })">
      <ThumbsUp :size="13" />
    </button>
    <button type="button" title="点踩" @click="emit('feedback', { messageId: message.id, rating: 'dislike', reason: null })">
      <ThumbsDown :size="13" />
    </button>
    <button type="button" title="复制" @click="copyText(message.content)">
      <Check v-if="copied" :size="13" />
      <Copy v-else :size="13" />
    </button>
  </footer>
</template>
