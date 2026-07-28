<script setup lang="ts">
import { computed, ref, watch } from 'vue'

type Question = {
  id: string
  text: string
  options: { label: string; value: string }[]
  multiple: boolean
}

const props = defineProps<{
  interrupt: { status: string; questions: Record<string, unknown>[] }
  disabled?: boolean
}>()

const emit = defineEmits<{
  submit: [answer: Record<string, string | string[]>]
  cancel: []
}>()

const answers = ref<Record<string, string | string[]>>({})
const questions = computed<Question[]>(() =>
  props.interrupt.questions
    .map((item, index) => {
      const text = String(item.question || '').trim()
      if (!text) return null
      const options = Array.isArray(item.options)
        ? item.options
            .map((option) => {
              const value = typeof option === 'object' && option ? String((option as Record<string, unknown>).value || (option as Record<string, unknown>).label || '') : String(option || '')
              const label = typeof option === 'object' && option ? String((option as Record<string, unknown>).label || value) : value
              return value ? { label, value } : null
            })
            .filter((option): option is { label: string; value: string } => Boolean(option))
        : []
      return {
        id: String(item.question_id || item.questionId || `q-${index + 1}`),
        text,
        options,
        multiple: Boolean(item.multi_select || item.multiSelect)
      }
    })
    .filter((question): question is Question => Boolean(question))
)

watch(
  questions,
  (items) => {
    answers.value = Object.fromEntries(items.map((item) => [item.id, item.multiple ? [] : '']))
  },
  { immediate: true }
)

function toggle(question: Question, value: string) {
  const current = answers.value[question.id]
  const selected: string[] = Array.isArray(current) ? current : []
  answers.value[question.id] = selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]
}

const ready = computed(() =>
  questions.value.length > 0 &&
  questions.value.every((question) => {
    const answer = answers.value[question.id]
    return Array.isArray(answer) ? answer.length > 0 : Boolean(String(answer || '').trim())
  })
)

function submit() {
  if (!ready.value || props.disabled) return
  emit('submit', answers.value)
}
</script>

<template>
  <section class="interrupt-card" :aria-label="interrupt.status === 'human_approval_required' ? '人工审批' : '需要补充信息'">
    <p class="interrupt-title">{{ interrupt.status === 'human_approval_required' ? '需要人工审批' : '需要补充信息' }}</p>
    <div v-for="question in questions" :key="question.id" class="interrupt-question">
      <p>{{ question.text }}</p>
      <template v-if="question.options.length">
        <label v-for="option in question.options" :key="option.value">
          <input
            v-if="question.multiple"
            type="checkbox"
            :checked="Array.isArray(answers[question.id]) && answers[question.id].includes(option.value)"
            :disabled="disabled"
            @change="toggle(question, option.value)"
          />
          <input v-else v-model="answers[question.id]" type="radio" :name="question.id" :value="option.value" :disabled="disabled" />
          {{ option.label }}
        </label>
      </template>
      <textarea v-else v-model.trim="answers[question.id]" rows="2" :disabled="disabled" placeholder="请输入回答" />
    </div>
    <div class="interrupt-actions">
      <button type="button" class="interrupt-cancel" :disabled="disabled" @click="emit('cancel')">拒绝</button>
      <button type="button" class="interrupt-submit" :disabled="disabled || !ready" @click="submit">提交并继续</button>
    </div>
  </section>
</template>

<style scoped>
.interrupt-card { margin: 8px 12px; padding: 12px; border: 1px solid var(--main-200); border-radius: 10px; background: var(--main-50); }
.interrupt-title, .interrupt-question p { margin: 0 0 8px; color: var(--gray-800); font-size: 13px; }
.interrupt-title { font-weight: 600; }
.interrupt-question { margin-top: 10px; }
.interrupt-question label { display: block; margin: 6px 0; color: var(--gray-700); font-size: 13px; }
.interrupt-question textarea { box-sizing: border-box; width: 100%; border: 1px solid var(--gray-300); border-radius: 6px; padding: 7px; resize: vertical; }
.interrupt-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.interrupt-actions button { min-height: 34px; border-radius: 6px; padding: 7px 12px; font-weight: 600; cursor: pointer; }
.interrupt-cancel { border: 1px solid var(--gray-300); color: var(--gray-700); background: var(--gray-0); }
.interrupt-submit { border: 1px solid var(--main-700); color: var(--gray-0); background: var(--main-700); }
.interrupt-cancel:disabled { border-color: var(--gray-200); color: var(--gray-500); background: var(--gray-100); cursor: not-allowed; }
.interrupt-submit:disabled { border-color: var(--main-200); color: var(--main-700); background: var(--main-50); cursor: not-allowed; }
</style>
