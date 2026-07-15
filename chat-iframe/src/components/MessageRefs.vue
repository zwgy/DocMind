<script setup lang="ts">
import { BookOpen, Check, ChevronDown, Copy, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import type { ChatMessage } from '@/types'
import KbResultGroupedList from '@/components/KbResultGroupedList.vue'
import type { ChatSources } from '@/utils/tool-calls'

const props = withDefaults(defineProps<{ message: ChatMessage; sources?: ChatSources }>(), {
  sources: () => ({ knowledgeChunks: [], webSources: [] })
})
const emit = defineEmits<{
  feedback: [payload: { messageId: string; rating: 'like' | 'dislike'; reason: string | null }]
}>()

const copied = ref(false)
const dislikeOpen = ref(false)
const dislikeReason = ref('')
const sourcesOpen = ref(false)
const feedbackLocked = computed(() => Boolean(props.message.feedback || props.message.feedbackSubmitting))
const sourceCount = computed(() => props.sources.knowledgeChunks.length + props.sources.webSources.length)

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
    <span v-if="message.modelName" class="message-model">{{ message.modelName }}</span>
    <button v-if="sourceCount" type="button" class="message-source-toggle" title="查看来源" @click="sourcesOpen = !sourcesOpen">
      <BookOpen :size="13" />
      来源 {{ sourceCount }}
      <ChevronDown :size="13" :class="{ rotated: sourcesOpen }" />
    </button>
    <section v-if="sourcesOpen && sourceCount" class="message-sources">
      <div v-if="sources.knowledgeChunks.length" class="message-source-section">
        <strong>知识库来源（{{ sources.knowledgeChunks.length }}）</strong>
        <KbResultGroupedList :chunks="sources.knowledgeChunks" />
      </div>
      <div v-if="sources.webSources.length" class="message-source-section">
        <strong>网页来源（{{ sources.webSources.length }}）</strong>
        <a
          v-for="source in sources.webSources"
          :key="source.url"
          class="message-web-source"
          :href="source.url"
          target="_blank"
          rel="noopener noreferrer"
        >
          {{ source.title }}
        </a>
      </div>
    </section>
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
