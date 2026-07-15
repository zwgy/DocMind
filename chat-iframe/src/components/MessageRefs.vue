<script setup lang="ts">
import { BookOpen, Check, ChevronDown, Copy, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import { computed, nextTick, ref } from 'vue'
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
const feedbackOpen = ref(false)
const feedbackRating = ref<'like' | 'dislike' | null>(null)
const feedbackReason = ref('')
const feedbackReasonRef = ref<HTMLTextAreaElement | null>(null)
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

function openFeedback(rating: 'like' | 'dislike') {
  if (feedbackLocked.value) return
  feedbackRating.value = rating
  feedbackOpen.value = true
  // 反馈表单可能刚好落在消息列表可视区外，聚焦 textarea 会让浏览器滚动到可输入的位置。
  void nextTick(() => feedbackReasonRef.value?.focus())
}

function submitFeedback() {
  if (feedbackLocked.value || !feedbackRating.value) return
  emit('feedback', { messageId: props.message.id, rating: feedbackRating.value, reason: feedbackReason.value.trim() || null })
  feedbackReason.value = ''
  feedbackRating.value = null
  feedbackOpen.value = false
}
</script>

<template>
  <footer class="message-refs">
    <button
      type="button"
      :class="{ selected: message.feedback?.rating === 'like' }"
      :disabled="feedbackLocked"
      :title="message.feedback?.rating === 'like' ? '已点赞' : '点赞并填写反馈'"
      @click="openFeedback('like')"
    >
      <ThumbsUp :size="13" :fill="message.feedback?.rating === 'like' ? 'currentColor' : 'none'" />
    </button>
    <button
      type="button"
      :class="{ selected: message.feedback?.rating === 'dislike' }"
      :disabled="feedbackLocked"
      :aria-expanded="feedbackOpen && feedbackRating === 'dislike'"
      :title="message.feedback?.rating === 'dislike' ? '已点踩' : '点踩并填写反馈'"
      @click="openFeedback('dislike')"
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
    <form v-if="feedbackOpen" class="feedback-reason" @submit.prevent="submitFeedback">
      <label>
        请告诉我们您的反馈（可选）
        <textarea ref="feedbackReasonRef" v-model="feedbackReason" maxlength="500" rows="2" placeholder="您的反馈将帮助我们持续改进" />
      </label>
      <div>
        <button type="submit">提交</button>
        <button type="button" @click="feedbackOpen = false; feedbackRating = null; feedbackReason = ''">取消</button>
      </div>
    </form>
  </footer>
</template>
