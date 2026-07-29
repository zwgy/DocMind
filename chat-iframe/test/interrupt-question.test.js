import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildInterruptAnswers,
  isInterruptQuestionAnswered,
  normalizeInterruptQuestions,
  OTHER_OPTION_VALUE
} from '../src/utils/interrupt-question.ts'

const componentSource = readFileSync(
  new URL('../src/components/RunInterruptCard.vue', import.meta.url),
  'utf8'
)

test('interrupt questions add one Other option when allow_other is enabled', () => {
  const questions = normalizeInterruptQuestions([
    {
      question_id: 'format',
      question: '输出格式',
      options: [
        { label: 'Markdown', value: 'markdown' },
        { label: 'Excel', value: 'excel' }
      ],
      allow_other: true
    }
  ])

  assert.deepEqual(questions[0].options, [
    { label: 'Markdown', value: 'markdown' },
    { label: 'Excel', value: 'excel' },
    { label: '其他', value: OTHER_OPTION_VALUE }
  ])
})

test('interrupt questions do not add Other when allow_other is disabled', () => {
  const questions = normalizeInterruptQuestions([
    {
      question_id: 'format',
      question: '输出格式',
      options: ['Markdown', 'Excel'],
      allow_other: false
    }
  ])

  assert.deepEqual(questions[0].options, [
    { label: 'Markdown', value: 'Markdown' },
    { label: 'Excel', value: 'Excel' }
  ])
})

test('interrupt answers preserve selected values with custom multi-select text', () => {
  const questions = normalizeInterruptQuestions([
    {
      question_id: 'focus',
      question: '关注重点',
      options: ['检修周期', '安全责任'],
      multi_select: true,
      allow_other: true
    }
  ])
  const selections = { focus: ['安全责任', OTHER_OPTION_VALUE] }
  const otherTexts = { focus: '预算归口' }

  assert.equal(isInterruptQuestionAnswered(questions[0], selections.focus, otherTexts.focus), true)
  assert.deepEqual(buildInterruptAnswers(questions, selections, otherTexts), {
    focus: {
      type: 'other',
      text: '预算归口',
      selected: ['安全责任']
    }
  })
})

test('interrupt Other selection is incomplete until custom text is entered', () => {
  const [question] = normalizeInterruptQuestions([
    {
      question_id: 'format',
      question: '输出格式',
      options: ['Markdown', 'Excel'],
      allow_other: true
    }
  ])

  assert.equal(isInterruptQuestionAnswered(question, OTHER_OPTION_VALUE, ''), false)
  assert.equal(isInterruptQuestionAnswered(question, OTHER_OPTION_VALUE, 'CSV'), true)
  assert.deepEqual(
    buildInterruptAnswers([question], { format: OTHER_OPTION_VALUE }, { format: 'CSV' }),
    {
      format: {
        type: 'other',
        text: 'CSV',
        selected: []
      }
    }
  )
})

test('interrupt card uses stable icon buttons and explicit Other input', () => {
  assert.match(componentSource, /CircleHelp/)
  assert.match(componentSource, /<X :size="16"/)
  assert.match(componentSource, /<Send :size="16"/)
  assert.match(componentSource, /class="interrupt-other-input"/)
  assert.match(componentSource, />暂不回答</)
  assert.match(componentSource, />提交回答</)
})
