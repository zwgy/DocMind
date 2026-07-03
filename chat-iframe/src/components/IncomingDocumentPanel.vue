<script setup lang="ts">
import { computed } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
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

const statusText = computed(() => {
  if (props.loading) return '查询中'
  if (props.error) return props.error
  if (!props.file) return '未选择附件'
  if (!props.result) return '等待查询'
  if (props.result.matchStatus !== 'matched') {
    return {
      pending_sync: '待同步入库',
      not_found: '未匹配到来文',
      multiple: '匹配到多个文档'
    }[props.result.matchStatus] || props.result.matchStatus
  }
  return {
    ready: '已生成结构化结果',
    running: '抽取中',
    not_found: '暂无抽取结果',
    failed: '抽取失败'
  }[props.result.extractionStatus] || props.result.extractionStatus
})

const matchedCategories = computed(() => {
  const categories = props.result?.categories || {}
  return Object.entries(categories).filter(([, value]) => value?.matched)
})

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
        <h1>{{ file?.name || '等待附件' }}</h1>
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
        <h2>文档分类</h2>
        <p v-if="!matchedCategories.length" class="muted">未命中业务分类</p>
        <article v-for="[name, category] in matchedCategories" :key="name" class="category-row">
          <strong>{{ name }}</strong>
          <p v-if="category.evidence">{{ category.evidence }}</p>
        </article>
      </section>

      <section class="result-section">
        <h2>结构化明细</h2>
        <p v-if="!items.length" class="muted">暂无结构化明细</p>
        <article v-for="item in items" :key="item.item_id" class="item-row">
          <strong>{{ item.item_type }}</strong>
          <dl>
            <template v-for="[key, value] in Object.entries(item.data || {})" :key="key">
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
