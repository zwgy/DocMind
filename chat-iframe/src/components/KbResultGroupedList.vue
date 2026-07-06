<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChevronDown, ChevronRight, Eye, FileText } from 'lucide-vue-next'
import { groupKbChunksByFile } from '@/utils/tool-calls'

const props = withDefaults(defineProps<{ chunks?: unknown[] }>(), { chunks: () => [] })
const expandedFiles = ref<Record<string, boolean>>({})
const selectedChunk = ref<Record<string, unknown> | null>(null)

const groups = computed(() => groupKbChunksByFile(props.chunks))

function toggleFile(filename: string) {
  expandedFiles.value[filename] = !expandedFiles.value[filename]
}

function metadata(chunk: Record<string, unknown>) {
  const data = chunk.metadata
  return data && typeof data === 'object' && !Array.isArray(data) ? (data as Record<string, unknown>) : {}
}

function scoreText(chunk: Record<string, unknown>, key: 'score' | 'rerank_score', label: string) {
  const value = typeof chunk[key] === 'number' ? chunk[key] : metadata(chunk)[key]
  return typeof value === 'number' ? `${label} ${(value * 100).toFixed(0)}%` : ''
}

function previewText(value: unknown) {
  const text = String(value || '')
  return text.length <= 100 ? text : `${text.slice(0, 100)}...`
}
</script>

<template>
  <div class="kb-result-grouped-list">
    <div class="kb-result-summary">找到 {{ chunks.length }} 个相关文档片段，来自 {{ groups.length }} 个文件</div>
    <div v-for="group in groups" :key="group.filename" class="kb-file-group">
      <button type="button" class="kb-file-header" @click="toggleFile(group.filename)">
        <component :is="expandedFiles[group.filename] ? ChevronDown : ChevronRight" :size="14" />
        <FileText :size="14" />
        <span>{{ group.filename }}</span>
        <em>{{ group.chunks.length }} chunks</em>
      </button>

      <div v-if="expandedFiles[group.filename]" class="kb-chunk-list">
        <button
          v-for="(chunk, index) in group.chunks"
          :key="`${group.filename}-${index}`"
          type="button"
          class="kb-chunk-row"
          @click="selectedChunk = chunk"
        >
          <span class="kb-chunk-index">#{{ index + 1 }}</span>
          <span v-if="scoreText(chunk, 'score', '相似度')" class="kb-score">
            {{ scoreText(chunk, 'score', '相似度') }}
          </span>
          <span v-if="scoreText(chunk, 'rerank_score', '重排度')" class="kb-score">
            {{ scoreText(chunk, 'rerank_score', '重排度') }}
          </span>
          <span class="kb-chunk-preview">{{ previewText(chunk.content) }}</span>
          <Eye :size="14" />
        </button>
      </div>
    </div>

    <div v-if="selectedChunk" class="kb-chunk-modal-mask" @click="selectedChunk = null">
      <article class="kb-chunk-modal" @click.stop>
        <header>
          <strong>文档片段</strong>
          <button type="button" @click="selectedChunk = null">关闭</button>
        </header>
        <pre>{{ selectedChunk.content }}</pre>
      </article>
    </div>
  </div>
</template>
