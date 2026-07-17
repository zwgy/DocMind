<script setup lang="ts">
import { FileText } from 'lucide-vue-next'
import type { IncomingPageFile } from '@/types'

withDefaults(
  defineProps<{
    files?: IncomingPageFile[]
    selectedSourceFileId?: string
  }>(),
  { files: () => [], selectedSourceFileId: '' }
)

defineEmits<{ select: [sourceFileId: string] }>()
</script>

<template>
  <aside class="file-selector">
    <div class="section-title">页面附件</div>
    <div v-if="!files.length" class="empty">未识别到文档附件</div>
    <button
      v-for="file in files"
      :key="file.source_file_id"
      type="button"
      class="file-option"
      :class="{ active: file.source_file_id === selectedSourceFileId }"
      @click="$emit('select', file.source_file_id)"
    >
      <FileText :size="16" />
      <span>{{ file.name }}</span>
      <small>{{ file.size_text || file.source_file_id || '文档' }}</small>
    </button>
  </aside>
</template>
