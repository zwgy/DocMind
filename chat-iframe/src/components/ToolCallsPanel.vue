<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Atom, BookOpen, ChevronDown, ChevronRight, FileText, Loader, Search } from 'lucide-vue-next'
import KbResultGroupedList from '@/components/KbResultGroupedList.vue'
import type { ChatToolCall } from '@/types'
import {
  displayToolName,
  formatJson,
  getToolArgs,
  getToolCallLabel,
  getToolKbDescription,
  getToolResult,
  getToolStatusLabel,
  isToolRunning,
  listKbsItems,
  normalizeToolCalls,
  parseQueryKbResult,
  resolveKbDisplayName,
  skillName
} from '@/utils/tool-calls'

const props = withDefaults(defineProps<{ toolCalls?: ChatToolCall[]; isActive?: boolean }>(), {
  toolCalls: () => [],
  isActive: false
})

const expanded = ref(false)
const expandedTools = ref<Record<string, boolean>>({})
const normalizedToolCalls = computed(() => normalizeToolCalls(props.toolCalls))
const hasRunningTool = computed(() => normalizedToolCalls.value.some((tool) => tool.status === 'running'))
const hasFailedTool = computed(() => normalizedToolCalls.value.some((tool) => tool.status === 'error'))

watch(
  [() => props.isActive, hasRunningTool, hasFailedTool],
  ([isActive, running, failed], [wasActive, wasRunning, wasFailed]) => {
    if (isActive || running || failed) {
      expanded.value = true
    } else if (wasActive || wasRunning || wasFailed) {
      // 进行中与失败项保留展开，便于观察进度和排错；仅全部成功完成后才自动收起。
      expanded.value = false
    }
  },
  { immediate: true }
)

function toolKey(tool: ChatToolCall, index: number) {
  return tool.id || `${tool.name}-${index}`
}

function toggleTool(tool: ChatToolCall, index: number) {
  const key = toolKey(tool, index)
  expandedTools.value[key] = !expandedTools.value[key]
}

function basename(path: unknown) {
  const value = String(path || '')
  return value.replace(/\\/g, '/').split('/').filter(Boolean).pop() || ''
}

function headerMeta(tool: ChatToolCall) {
  if (tool.name === 'list_kbs') {
    const items = listKbsItems(tool)
    const names = items.map((item) => item.name).filter(Boolean)
    return {
      icon: BookOpen,
      note: 'list_kbs 列表',
      description: items.length ? `${items.length} 个知识库：${names.slice(0, 2).join('、')}` : ''
    }
  }

  if (tool.name === 'query_kb') {
    const args = getToolArgs(tool)
    const resource = resolveKbDisplayName(tool, normalizedToolCalls.value)
    const query = String(args.query_text || args.query || '')
    return {
      icon: Search,
      note: 'query_kb 搜索',
      description: [resource ? `知识库: ${resource}` : '', query].filter(Boolean).join(' | ')
    }
  }

  if (['get_mindmap', 'search_file', 'find_kb_document', 'open_kb_document'].includes(tool.name)) {
    return {
      icon: tool.name === 'get_mindmap' ? FileText : Search,
      note: displayToolName(tool.name),
      description: getToolKbDescription(tool, normalizedToolCalls.value)
    }
  }

  const skill = skillName(tool)
  if (skill) return { icon: FileText, note: '激活 Skill', description: skill }

  const args = getToolArgs(tool)
  return {
    icon: FileText,
    note: displayToolName(tool.name),
    description: basename(args.file_path)
  }
}

function resultText(tool: ChatToolCall) {
  const result = getToolResult(tool)
  if (result && typeof result === 'object') return JSON.stringify(result, null, 2)
  return String(result ?? '')
}

function resultLines(tool: ChatToolCall) {
  const text = resultText(tool)
  return text ? text.split('\n') : []
}

function hasParams(tool: ChatToolCall) {
  return Object.keys(getToolArgs(tool)).length > 0
}

function queryResult(tool: ChatToolCall) {
  return parseQueryKbResult(getToolResult(tool))
}

function hasGraphData(tool: ChatToolCall) {
  const result = queryResult(tool)
  return Boolean(result.entities.length || result.relationships.length || result.references.length)
}

const toolNames = computed(() => {
  const names = normalizedToolCalls.value.map(getToolCallLabel).filter(Boolean)
  const uniqueNames = [...new Set(names)]
  return `${uniqueNames.slice(0, 3).join(' · ')}${uniqueNames.length > 3 ? ` +${uniqueNames.length - 3}` : ''}`
})

const statusText = computed(() => {
  const done = normalizedToolCalls.value.filter((tool) => tool.status === 'done').length
  const failed = normalizedToolCalls.value.filter((tool) => tool.status === 'error').length
  const running = normalizedToolCalls.value.length - done - failed
  if (done && done === normalizedToolCalls.value.length) return '已完成'
  return [
    failed ? `${failed} 失败` : '',
    running ? `${running} 进行中` : ''
  ].filter(Boolean).join(' · ')
})

const title = computed(() => {
  if (normalizedToolCalls.value.length === 1) return getToolCallLabel(normalizedToolCalls.value[0])
  return `执行 ${normalizedToolCalls.value.length} 个工具`
})
</script>

<template>
  <section v-if="normalizedToolCalls.length" class="tool-calls-panel">
    <button
      type="button"
      class="tool-summary"
      :class="{ 'is-expanded': expanded }"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <Atom :size="14" />
      <span>{{ title }}</span>
      <small v-if="normalizedToolCalls.length > 1 && toolNames">{{ toolNames }}</small>
      <em v-if="statusText">{{ statusText }}</em>
      <component :is="expanded ? ChevronDown : ChevronRight" :size="14" />
    </button>

    <div v-if="expanded" class="tool-list">
      <article v-for="(tool, index) in normalizedToolCalls" :key="toolKey(tool, index)" class="tool-card">
        <button
          type="button"
          class="tool-card-summary"
          :aria-expanded="Boolean(expandedTools[toolKey(tool, index)])"
          @click="toggleTool(tool, index)"
        >
          <component
            :is="isToolRunning(tool) ? Loader : headerMeta(tool).icon"
            :size="15"
            :class="{ 'tool-card-spinner': isToolRunning(tool) }"
          />
          <span class="tool-note">{{ headerMeta(tool).note }}</span>
          <span v-if="headerMeta(tool).description" class="tool-separator">|</span>
          <strong v-if="headerMeta(tool).description">{{ headerMeta(tool).description }}</strong>
          <em>{{ getToolStatusLabel(tool) }}</em>
          <component :is="expandedTools[toolKey(tool, index)] ? ChevronDown : ChevronRight" :size="14" />
        </button>

        <div v-if="expandedTools[toolKey(tool, index)]" class="tool-card-body">
          <div v-if="hasParams(tool)" class="tool-params">
            <strong>参数:</strong>
            <span>{{ formatJson(tool.args) }}</span>
          </div>

          <div v-if="tool.name === 'list_kbs' && listKbsItems(tool).length" class="tool-kb-list">
            <p>共 {{ listKbsItems(tool).length }} 个知识库</p>
            <article v-for="kb in listKbsItems(tool)" :key="kb.id || kb.name" class="tool-kb-card">
              <strong>{{ kb.name }}</strong>
              <span>{{ kb.description }}</span>
            </article>
          </div>

          <div v-else-if="tool.name === 'query_kb'" class="query-kb-result">
            <KbResultGroupedList v-if="queryResult(tool).chunks.length" :chunks="queryResult(tool).chunks" />
            <div v-if="hasGraphData(tool)" class="graph-result-card">
              图谱检索：实体 {{ queryResult(tool).entities.length }} 个，关系
              {{ queryResult(tool).relationships.length }} 条，引用 {{ queryResult(tool).references.length }} 条
            </div>
            <p v-if="!queryResult(tool).chunks.length && !hasGraphData(tool)" class="tool-empty-result">
              未找到相关知识库内容
            </p>
          </div>

          <div v-else-if="tool.result" class="tool-result-viewer">
            <div v-for="(line, lineIndex) in resultLines(tool)" :key="`${toolKey(tool, index)}-${lineIndex}`" class="tool-result-line">
              <span>{{ lineIndex + 1 }}</span>
              <code>{{ line || ' ' }}</code>
            </div>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>
