<script setup lang="ts">
import { computed } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import {
  displayExtractionDataEntries,
  extractionClassificationText,
  extractionItemTypeText,
  extractionStatusText
} from '@/utils/context-summary'
import type { ExtractionResult, IncomingPageFile } from '@/types'

const props = withDefaults(
  defineProps<{
    file: IncomingPageFile | null
    result: ExtractionResult | null
    loading?: boolean
    error?: string
  }>(),
  { file: null, result: null, loading: false, error: '' }
)

defineEmits(['refresh'])

const statusText = computed(() => extractionStatusText(props))

const items = computed(() => props.result?.items || [])

function displayValue(value: unknown) {
  return Array.isArray(value) ? value.join('、') : String(value ?? '')
}
</script>

<template>
  <section class="incoming-panel">
    <div class="panel-toolbar">
      <div>
        <span class="eyebrow">结构化识别</span>
        <h1>
          <span>{{ file?.name || '等待附件' }}</span>
          <span v-if="extractionClassificationText(result)" class="classification-badge">
            {{ extractionClassificationText(result) }}
          </span>
        </h1>
      </div>
      <button type="button" title="刷新" @click="$emit('refresh')">
        <RefreshCw :size="16" />
      </button>
    </div>

    <div class="status-line" :class="{ loading, error: Boolean(error) }">
      {{ statusText }}
    </div>

    <template v-if="result?.matchStatus === 'matched' && result?.extractionStatus === 'ready'">
      <section class="result-section">
        <h2>结构化明细</h2>
        <p v-if="!items.length" class="muted">暂无结构化明细</p>
        <article v-for="(item, index) in items" :key="item.item_id" class="item-row">
          <strong>{{ extractionItemTypeText(item.item_type, result) }} {{ index + 1 }}</strong>
          <dl>
            <template v-for="[key, value] in displayExtractionDataEntries(item.data, item.item_type, result)" :key="key">
              <dt>{{ key }}</dt>
              <dd>{{ displayValue(value) }}</dd>
            </template>
          </dl>
          <blockquote v-if="item.source_quote">{{ item.source_quote }}</blockquote>
        </article>
      </section>
    </template>

    <p v-else-if="result?.reason" class="muted">{{ result.reason }}</p>
  </section>
</template>
