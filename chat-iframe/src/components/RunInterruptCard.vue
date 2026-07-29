<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Send, X } from 'lucide-vue-next'
import {
  buildInterruptAnswers,
  isInterruptQuestionAnswered,
  isOtherSelected,
  normalizeInterruptQuestions,
  type InterruptAnswer,
  type InterruptQuestion,
  type InterruptSelection
} from '@/utils/interrupt-question'

const props = defineProps<{
  interrupt: { status: string; questions: Record<string, unknown>[] }
  disabled?: boolean
}>()

const emit = defineEmits<{
  submit: [answer: Record<string, InterruptAnswer>]
  cancel: []
}>()

const answers = ref<Record<string, InterruptSelection>>({})
const otherTexts = ref<Record<string, string>>({})
const contentEl = ref<HTMLElement | null>(null)
const questions = computed(() => normalizeInterruptQuestions(props.interrupt.questions))
const isApproval = computed(() => props.interrupt.status === 'human_approval_required')

watch(
  questions,
  async (items) => {
    answers.value = Object.fromEntries(items.map((item) => [item.id, item.multiple ? [] : '']))
    otherTexts.value = {}
    await nextTick()
    // 新问题或刷新恢复后必须从第一题开始展示，避免浏览器保留焦点滚动导致用户先看到半截选项。
    contentEl.value?.scrollTo({ top: 0, behavior: 'auto' })
  },
  { immediate: true }
)

function toggle(question: InterruptQuestion, value: string) {
  const current = answers.value[question.id]
  const selected: string[] = Array.isArray(current) ? current : []
  answers.value[question.id] = selected.includes(value)
    ? selected.filter((item) => item !== value)
    : [...selected, value]
}

function isSelected(question: InterruptQuestion, value: string) {
  const answer = answers.value[question.id]
  return Array.isArray(answer) ? answer.includes(value) : answer === value
}

const ready = computed(
  () =>
    questions.value.length > 0 &&
    questions.value.every((question) =>
      isInterruptQuestionAnswered(
        question,
        answers.value[question.id] ?? (question.multiple ? [] : ''),
        otherTexts.value[question.id]
      )
    )
)

function submit() {
  if (!ready.value || props.disabled) return
  emit('submit', buildInterruptAnswers(questions.value, answers.value, otherTexts.value))
}
</script>

<template>
  <section class="interrupt-card" :aria-label="isApproval ? '人工审批' : '需要补充信息'">
    <header class="interrupt-header">
      <p class="interrupt-title">{{ isApproval ? '请确认操作' : '请补充信息' }}</p>
      <p class="interrupt-description">完成回答后，助手将继续处理</p>
    </header>

    <div ref="contentEl" class="interrupt-content">
      <fieldset v-for="question in questions" :key="question.id" class="interrupt-question">
        <legend>
          {{ question.text }}
          <small v-if="question.multiple">可多选</small>
        </legend>
        <div v-if="question.options.length" class="interrupt-options">
          <label
            v-for="option in question.options"
            :key="option.value"
            class="interrupt-option"
            :class="{
              'is-selected': isSelected(question, option.value),
              'is-disabled': disabled
            }"
          >
            <input
              v-if="question.multiple"
              type="checkbox"
              :checked="isSelected(question, option.value)"
              :disabled="disabled"
              @change="toggle(question, option.value)"
            />
            <input
              v-else
              v-model="answers[question.id]"
              type="radio"
              :name="question.id"
              :value="option.value"
              :disabled="disabled"
            />
            <span>{{ option.label }}</span>
          </label>
          <input
            v-if="
              question.allowOther &&
              isOtherSelected(question, answers[question.id] ?? (question.multiple ? [] : ''))
            "
            v-model.trim="otherTexts[question.id]"
            class="interrupt-other-input"
            type="text"
            :disabled="disabled"
            placeholder="请输入其他答案"
            aria-label="其他答案"
          />
        </div>
        <textarea
          v-else
          v-model.trim="answers[question.id]"
          rows="2"
          :disabled="disabled"
          placeholder="请输入回答"
        />
      </fieldset>
    </div>

    <div class="interrupt-actions">
      <button type="button" class="interrupt-cancel" :disabled="disabled" @click="emit('cancel')">
        <X :size="16" />
        <span>暂不回答</span>
      </button>
      <button type="button" class="interrupt-submit" :disabled="disabled || !ready" @click="submit">
        <Send :size="16" />
        <span>提交回答</span>
      </button>
    </div>
  </section>
</template>

<style scoped>
.interrupt-card {
  box-sizing: border-box;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  width: calc(100% - 20px);
  max-height: min(78vh, 540px);
  min-width: 0;
  margin: 0 10px 10px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  box-shadow: 0 8px 24px rgb(15 23 42 / 12%);
  overflow: hidden;
}

.interrupt-header {
  padding: 16px 16px 14px;
  border-bottom: 1px solid var(--gray-100);
  background: var(--gray-50);
  text-align: center;
}

.interrupt-title,
.interrupt-description {
  margin: 0;
}

.interrupt-title {
  color: var(--gray-900);
  font-size: 16px;
  font-weight: 600;
  line-height: 1.35;
}

.interrupt-description {
  margin-top: 2px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.4;
}

.interrupt-content {
  min-height: 0;
  padding: 0 16px 16px;
  overflow-y: auto;
  background: var(--gray-0);
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.interrupt-question {
  min-width: 0;
  margin: 14px 0 0;
  padding: 0;
  border: 0;
}

.interrupt-question legend {
  width: 100%;
  margin: 0 0 8px;
  padding: 0;
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.5;
}

.interrupt-question legend small {
  margin-left: 6px;
  color: var(--gray-500);
  font-size: 12px;
  font-weight: 400;
}

.interrupt-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}

.interrupt-option {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  padding: 7px 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--gray-700);
  background: var(--gray-25);
  cursor: pointer;
  font-size: 12px;
  line-height: 1.4;
}

.interrupt-option:hover {
  border-color: var(--gray-200);
  background: var(--gray-50);
}

.interrupt-option.is-selected {
  border-color: var(--main-200);
  color: var(--main-900);
  background: var(--main-50);
}

.interrupt-option:focus-within {
  border-color: var(--main-700);
  box-shadow: 0 0 0 2px var(--main-50);
}

.interrupt-option input {
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--main-700);
}

.interrupt-option.is-disabled {
  color: var(--gray-500);
  cursor: not-allowed;
  opacity: 0.72;
}

.interrupt-other-input,
.interrupt-question textarea {
  box-sizing: border-box;
  width: 100%;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  outline: none;
  color: var(--gray-900);
  background: var(--gray-0);
  font: inherit;
  font-size: 13px;
}

.interrupt-other-input {
  grid-column: 1 / -1;
  height: 40px;
  margin-top: 2px;
  padding: 0 10px;
}

.interrupt-question textarea {
  min-height: 72px;
  padding: 9px 10px;
  resize: vertical;
}

.interrupt-other-input:focus,
.interrupt-question textarea:focus {
  border-color: var(--main-700);
  box-shadow: 0 0 0 2px var(--main-50);
}

.interrupt-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  padding: 12px 16px 16px;
  border-top: 1px solid var(--gray-100);
  background: var(--gray-25);
}

.interrupt-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 42px;
  border-radius: 6px;
  padding: 8px 16px;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
}

.interrupt-cancel {
  border: 1px solid var(--gray-200);
  color: var(--gray-700);
  background: var(--gray-0);
}

.interrupt-cancel:hover:not(:disabled) {
  border-color: var(--gray-300);
  color: var(--gray-900);
  background: var(--gray-25);
}

.interrupt-submit {
  border: 1px solid var(--main-700);
  color: var(--gray-0);
  background: var(--main-700);
}

.interrupt-submit:hover:not(:disabled) {
  border-color: var(--main-900);
  background: var(--main-900);
}

.interrupt-actions button:focus-visible {
  outline: 2px solid var(--main-700);
  outline-offset: 2px;
}

.interrupt-actions button:disabled,
.interrupt-other-input:disabled,
.interrupt-question textarea:disabled {
  border-color: var(--gray-200);
  color: var(--gray-500);
  background: var(--gray-100);
  cursor: not-allowed;
}

@media (max-width: 380px) {
  .interrupt-card {
    width: calc(100% - 16px);
    margin: 0 8px 8px;
  }

  .interrupt-options {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
