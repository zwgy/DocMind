export const OTHER_OPTION_VALUE = '__other__'

export type InterruptOption = {
  label: string
  value: string
}

export type InterruptQuestion = {
  id: string
  text: string
  options: InterruptOption[]
  multiple: boolean
  allowOther: boolean
  otherOptionValue: string
}

export type InterruptSelection = string | string[]

export type InterruptOtherAnswer = {
  type: 'other'
  text: string
  selected: string[]
}

export type InterruptAnswer = InterruptSelection | InterruptOtherAnswer

function isOtherOption(option: InterruptOption) {
  const label = option.label.trim().toLowerCase()
  const value = option.value.trim().toLowerCase()
  return (
    value === OTHER_OPTION_VALUE ||
    value === 'other' ||
    label.includes('其他') ||
    label.includes('other')
  )
}

function normalizeOptions(value: unknown): InterruptOption[] {
  if (!Array.isArray(value)) return []

  return value
    .map((option) => {
      if (option && typeof option === 'object') {
        const item = option as Record<string, unknown>
        const value = String(item.value || item.label || '').trim()
        const label = String(item.label || value).trim()
        return value ? { label, value } : null
      }
      const text = String(option || '').trim()
      return text ? { label: text, value: text } : null
    })
    .filter((option): option is InterruptOption => Boolean(option))
}

export function normalizeInterruptQuestions(
  rawQuestions: Record<string, unknown>[]
): InterruptQuestion[] {
  const questions: InterruptQuestion[] = []

  rawQuestions.forEach((item, index) => {
    const text = String(item.question || '').trim()
    if (!text) return

    const allowOther = Boolean(item.allowOther ?? item.allow_other ?? true)
    const options = normalizeOptions(item.options)
    const existingOther = options.find(isOtherOption)

    // ask_user_question 的 Other 是协议能力，不是普通展示项；统一补齐稳定值，
    // 避免本地模型只设置 allow_other 却遗漏显式选项时，iframe 无法收集自定义答案。
    if (allowOther && options.length && !existingOther) {
      options.push({ label: '其他', value: OTHER_OPTION_VALUE })
    }

    questions.push({
      id: String(item.question_id || item.questionId || `q-${index + 1}`),
      text,
      options,
      multiple: Boolean(item.multi_select || item.multiSelect),
      allowOther,
      otherOptionValue: existingOther?.value || OTHER_OPTION_VALUE
    })
  })

  return questions
}

export function isOtherSelected(question: InterruptQuestion, answer: InterruptSelection) {
  return Array.isArray(answer)
    ? answer.includes(question.otherOptionValue)
    : answer === question.otherOptionValue
}

export function isInterruptQuestionAnswered(
  question: InterruptQuestion,
  answer: InterruptSelection,
  otherText = ''
) {
  const hasSelection = Array.isArray(answer)
    ? answer.length > 0
    : Boolean(String(answer || '').trim())
  if (!hasSelection) return false

  return !isOtherSelected(question, answer) || Boolean(otherText.trim())
}

export function buildInterruptAnswers(
  questions: InterruptQuestion[],
  selections: Record<string, InterruptSelection>,
  otherTexts: Record<string, string>
): Record<string, InterruptAnswer> {
  return Object.fromEntries(
    questions.map((question) => {
      const answer = selections[question.id] ?? (question.multiple ? [] : '')
      if (question.allowOther && isOtherSelected(question, answer)) {
        const selected = (Array.isArray(answer) ? answer : [answer]).filter(
          (value) => value !== question.otherOptionValue
        )
        return [
          question.id,
          {
            type: 'other',
            text: String(otherTexts[question.id] || '').trim(),
            selected
          } satisfies InterruptOtherAnswer
        ]
      }

      if (question.multiple) return [question.id, Array.isArray(answer) ? answer : [answer]]
      return [question.id, Array.isArray(answer) ? answer[0] || '' : answer]
    })
  )
}
