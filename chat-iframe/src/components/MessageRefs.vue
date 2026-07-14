<script setup lang="ts">
import { Check, Copy, RotateCcw, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { ChatMessage } from '@/types'

const props = defineProps<{ message: ChatMessage }>()
const emit = defineEmits<{
  retry: []
  feedback: [payload: { messageId: string; rating: 'like' | 'dislike'; reason: string | null }]
}>()

const copied = ref(false)
const dislikeOpen = ref(false)
const dislikeReason = ref('')
const feedbackLocked = computed(() => Boolean(props.message.feedback || props.message.feedbackSubmitting))

async function copyText(text: string) {
  await navigator.clipboard?.writeText(text)
  copied.value = true
  window.setTimeout(() => {
    copied.value = false
  }, 1500)
}

function submitLike() {
  if (feedbackLocked.value) return
  emit('feedback', { messageId: props.message.id, rating: 'like', reason: null })
}

function submitDislike() {
  if (feedbackLocked.value) return
  emit('feedback', { messageId: props.message.id, rating: 'dislike', reason: dislikeReason.value.trim() || null })
  dislikeReason.value = ''
  dislikeOpen.value = false
}
</script>

<template>
  <footer class="message-refs">
    <button type="button" title="重新生成（追加新一轮）" @click="emit('retry')">
      <RotateCcw :size="13" />
    </button>
    <button
      type="button"
      :class="{ selected: message.feedback?.rating === 'like' }"
      :disabled="feedbackLocked"
      :title="message.feedback?.rating === 'like' ? '已点赞' : '点赞'"
      @click="submitLike"
    >
      <ThumbsUp :size="13" :fill="message.feedback?.rating === 'like' ? 'currentColor' : 'none'" />
    </button>
    <button
      type="button"
      :class="{ selected: message.feedback?.rating === 'dislike' }"
      :disabled="feedbackLocked"
      :title="message.feedback?.rating === 'dislike' ? '已点踩' : '点踩'"
      @click="dislikeOpen = true"
    >
      <ThumbsDown :size="13" :fill="message.feedback?.rating === 'dislike' ? 'currentColor' : 'none'" />
    </button>
    <button type="button" title="复制" @click="copyText(message.content)">
      <Check v-if="copied" :size="13" />
      <Copy v-else :size="13" />
    </button>
    <form v-if="dislikeOpen" class="feedback-reason" @submit.prevent="submitDislike">
      <label>
        点踩原因（可选）
        <textarea v-model="dislikeReason" maxlength="500" rows="2" placeholder="告诉我们哪里需要改进" />
      </label>
      <div>
        <button type="submit">提交</button>
        <button type="button" @click="dislikeOpen = false; dislikeReason = ''">取消</button>
      </div>
    </form>
  </footer>
</template>
