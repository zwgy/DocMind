<script setup lang="ts">
import { computed, ref } from 'vue'
import { Atom, BookOpen, ChevronDown, ChevronRight, FileText } from 'lucide-vue-next'
import type { ChatToolCall } from '@/types'

const props = withDefaults(defineProps<{ toolCalls?: ChatToolCall[] }>(), { toolCalls: () => [] })
const expanded = ref(false)

function parseMaybeJson(value: unknown) {
  if (typeof value !== 'string') return value
  const trimmed = value.trim()
  if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) return value
  try {
    return JSON.parse(trimmed)
  } catch {
    return value
  }
}

function formatJson(value: unknown) {
  const parsed = parseMaybeJson(value)
  if (parsed && typeof parsed === 'object') return JSON.stringify(parsed, null, 2)
  return String(parsed ?? '')
}

function resultData(tool: ChatToolCall) {
  const parsed = parseMaybeJson(tool.result)
  if (parsed && typeof parsed === 'object') {
    const content = (parsed as Record<string, unknown>).content
    return content === undefined ? parsed : parseMaybeJson(content)
  }
  return parsed
}

function argsObject(tool: ChatToolCall) {
  const parsed = parseMaybeJson(tool.args)
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {}
}

function displayLabel(name = 'tool') {
  return name
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function skillName(tool: ChatToolCall) {
  const path = String(argsObject(tool).file_path || '')
  if (!path.endsWith('SKILL.md')) return ''
  const parts = path.replace(/\\/g, '/').split('/')
  return parts.length >= 2 ? parts[parts.length - 2] : ''
}

function headerMeta(tool: ChatToolCall) {
  if (tool.name === 'list_kbs') {
    const items = listKbsItems(tool)
    const names = items.map((item) => item.name).filter(Boolean)
    return {
      note: 'list_kbs 列表',
      description: items.length ? `${items.length}个知识库：${names.slice(0, 2).join('、')}` : ''
    }
  }
  const skill = skillName(tool)
  if (skill) return { note: 'Skill', description: skill }
  return { note: displayLabel(tool.name), description: '' }
}

function resultText(tool: ChatToolCall) {
  const parsed = resultData(tool)
  if (parsed && typeof parsed === 'object') return JSON.stringify(parsed, null, 2)
  return String(parsed ?? '')
}

function hasToolParams(tool: ChatToolCall) {
  if (!tool.args) return false
  const parsed = parseMaybeJson(tool.args)
  if (parsed && typeof parsed === 'object') return Object.keys(parsed).length > 0
  return String(parsed ?? '').trim().length > 0
}

function listKbsItems(tool: ChatToolCall) {
  if (tool.name !== 'list_kbs') return []
  const data = resultData(tool)
  if (!Array.isArray(data)) return []
  return data
    .map((item) => (item && typeof item === 'object' ? (item as Record<string, unknown>) : null))
    .filter(Boolean)
    .map((item) => ({
      id: String(item?.kb_id || item?.id || item?.name || ''),
      name: String(item?.name || '未命名知识库'),
      description: String(item?.description || '暂无描述')
    }))
}

function resultLines(tool: ChatToolCall) {
  const text = resultText(tool)
  return text ? text.split('\n') : []
}

function hasEmbeddedLineNumbers(tool: ChatToolCall) {
  const lines = resultLines(tool).filter((line) => line.trim())
  if (lines.length < 2) return false
  // read_file 结果常由后端带行号，前端再加一列会重复；只在多数行匹配时关闭额外行号。
  const numbered = lines.filter((line) => /^\s*\d+\s+/.test(line)).length
  return numbered / lines.length >= 0.6
}

const toolNames = computed(() => {
  const names = props.toolCalls.map((tool) => displayLabel(tool.name)).filter(Boolean)
  return [...new Set(names)].slice(0, 3).join(' · ')
})
const statusText = computed(() => {
  const done = props.toolCalls.filter((tool) => tool.status === 'done').length
  const failed = props.toolCalls.filter((tool) => tool.status === 'error').length
  const running = props.toolCalls.length - done - failed
  if (props.toolCalls.length && done === props.toolCalls.length) return '已完成'
  const parts = []
  if (failed) parts.push(`${failed} 失败`)
  if (running) parts.push(`${running} 进行中`)
  return parts.join(' · ')
})
const title = computed(() => {
  if (props.toolCalls.length === 1) return `调用: ${displayLabel(props.toolCalls[0].name)}`
  return `已调用 ${props.toolCalls.length} 个工具`
})
</script>

<template>
  <section v-if="toolCalls.length" class="tool-calls-panel">
    <button type="button" class="tool-summary" @click="expanded = !expanded">
      <Atom :size="14" />
      <span>{{ title }}</span>
      <small v-if="toolCalls.length > 1 && toolNames">{{ toolNames }}</small>
      <em v-if="statusText">{{ statusText }}</em>
      <component :is="expanded ? ChevronDown : ChevronRight" :size="14" />
    </button>
    <div v-if="expanded" class="tool-list">
      <article v-for="tool in toolCalls" :key="tool.id" class="tool-card">
        <header class="tool-card-header">
          <BookOpen v-if="tool.name === 'list_kbs'" :size="15" />
          <FileText v-else :size="15" />
          <span class="tool-note">{{ headerMeta(tool).note }}</span>
          <span v-if="headerMeta(tool).description" class="tool-separator">|</span>
          <strong v-if="headerMeta(tool).description">{{ headerMeta(tool).description }}</strong>
          <em>{{ tool.status === 'done' ? '已完成' : tool.status === 'error' ? '失败' : '进行中' }}</em>
        </header>
        <div v-if="hasToolParams(tool)" class="tool-params">
          <strong>参数:</strong>
          <span>{{ formatJson(tool.args) }}</span>
        </div>
        <div v-if="listKbsItems(tool).length" class="tool-kb-list">
          <p>共 {{ listKbsItems(tool).length }} 个知识库</p>
          <article v-for="kb in listKbsItems(tool)" :key="kb.id || kb.name" class="tool-kb-card">
            <strong>{{ kb.name }}</strong>
            <span>{{ kb.description }}</span>
          </article>
        </div>
        <div
          v-else-if="tool.result"
          class="tool-result-viewer"
          :class="{ 'has-embedded-line-numbers': hasEmbeddedLineNumbers(tool) }"
        >
          <div v-for="(line, index) in resultLines(tool)" :key="`${tool.id}-${index}`" class="tool-result-line">
            <span>{{ index + 1 }}</span>
            <code>{{ line || ' ' }}</code>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>
