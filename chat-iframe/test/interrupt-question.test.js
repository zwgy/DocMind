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
const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')

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

test('interrupt panel replaces the composer with a centered header and symmetric actions', () => {
  assert.doesNotMatch(componentSource, /CircleHelp|interrupt-icon/)
  assert.match(componentSource, /<X :size="16"/)
  assert.match(componentSource, /<Send :size="16"/)
  assert.match(componentSource, /ref="contentEl"/)
  assert.match(componentSource, /contentEl\.value\?\.scrollTo\(\{ top: 0, behavior: 'auto' \}\)/)
  assert.match(componentSource, /class="interrupt-other-input"/)
  assert.match(componentSource, /isApproval \? '请确认操作' : '请补充信息'/)
  assert.match(componentSource, />完成回答后，助手将继续处理</)
  assert.match(componentSource, />暂不回答</)
  assert.match(componentSource, />提交回答</)
  assert.match(
    componentSource,
    /\.interrupt-card \{[\s\S]*grid-template-rows: auto minmax\(0, 1fr\) auto;[\s\S]*width: calc\(100% - 20px\);[\s\S]*max-height: min\(78vh, 540px\);[\s\S]*border: 1px solid var\(--gray-200\);[\s\S]*border-top: 3px solid var\(--main-700\);[\s\S]*border-radius: 8px;[\s\S]*0 -8px 24px rgb\(15 23 42 \/ 12%\),/
  )
  assert.match(
    componentSource,
    /\.interrupt-content \{[\s\S]*overflow-y: auto;[\s\S]*background: var\(--gray-25\);/
  )
  assert.match(
    componentSource,
    /\.interrupt-header \{[\s\S]*border-bottom: 1px solid var\(--gray-150\);[\s\S]*background: var\(--gray-0\);/
  )
  assert.match(componentSource, /\.interrupt-title \{[\s\S]*font-size: 16px;/)
  assert.match(componentSource, /\.interrupt-option \{[\s\S]*font-size: 12px;/)
  assert.match(
    componentSource,
    /\.interrupt-question \{[\s\S]*border-bottom: 1px solid var\(--gray-200\);/
  )
  assert.match(
    componentSource,
    /\.interrupt-option \{[\s\S]*border: 1px solid var\(--gray-200\);[\s\S]*background: var\(--gray-0\);/
  )
  assert.match(
    componentSource,
    /\.interrupt-option:hover \{[\s\S]*border-color: var\(--main-200\);[\s\S]*background: var\(--main-50\);/
  )
  assert.match(
    componentSource,
    /\.interrupt-option input \{[\s\S]*width: 18px;[\s\S]*appearance: none;[\s\S]*outline: none;/
  )
  assert.match(
    componentSource,
    /\.interrupt-actions \{[\s\S]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);[\s\S]*border-top: 1px solid var\(--gray-200\);[\s\S]*background: var\(--gray-0\);[\s\S]*0 -4px 12px rgb\(15 23 42 \/ 4%\);/
  )
  assert.match(componentSource, /\.interrupt-actions button \{[\s\S]*min-height: 42px;/)
  assert.match(
    appSource,
    /<RunInterruptCard[\s\S]*v-if="chat\.pendingInterrupt"[\s\S]*\/>\s*<ChatInput\s+v-else/
  )
})
