<template>
  <Teleport to="body">
    <div v-if="open" class="file-search-overlay" @mousedown.self="close">
      <section class="file-search-modal" role="dialog" aria-modal="true" aria-label="搜索文件" @keydown.esc="close">
        <div class="file-search-input-row">
          <Search :size="18" />
          <input
            ref="searchInputRef"
            v-model="keyword"
            type="text"
            placeholder="输入文件名搜索（不搜索文件内容）"
            autocomplete="off"
            @keydown.enter.prevent="handleSearch"
          />
          <button type="button" aria-label="关闭" @click="close"><X :size="20" /></button>
        </div>
        <div class="file-search-body">
          <p v-if="!hasSearched" class="file-search-hint">输入文件名后按 Enter 搜索。</p>
          <p v-else-if="loading" class="file-search-hint">正在搜索…</p>
          <p v-else-if="!results.length" class="file-search-hint">未找到匹配的文件</p>
          <template v-else>
            <button v-for="item in results" :key="item.file_id" type="button" class="file-search-result" @click="select(item)">
              <FileText :size="18" />
              <span>
                <strong :title="item.filename">{{ item.filename }}</strong>
                <small>{{ formatFileSize(item.file_size) }} · {{ formatDate(item.updated_at) }}</small>
              </span>
            </button>
            <p v-if="hasMore" class="file-search-hint">仅展示前 {{ results.length }} 条，请细化关键词。</p>
          </template>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<script setup>
import { nextTick, ref, watch } from 'vue'
import { FileText, Search, X } from 'lucide-vue-next'
import { documentApi } from '@/apis/knowledge_api'
import { formatFileSize } from '@/utils/file_utils'
import dayjs, { parseToShanghai } from '@/utils/time'

const props = defineProps({ open: Boolean, kbId: String })
const emit = defineEmits(['update:open', 'select'])
const searchInputRef = ref(null)
const keyword = ref('')
const results = ref([])
const loading = ref(false)
const hasSearched = ref(false)
const hasMore = ref(false)
let searchToken = 0

const close = () => emit('update:open', false)
const select = (file) => {
  emit('select', file)
  close()
}
const formatDate = (value) => {
  const parsed = parseToShanghai(value)
  return parsed ? parsed.format(parsed.year() === dayjs().year() ? 'M月D日 HH:mm' : 'YYYY-MM-DD HH:mm') : ''
}
const handleSearch = async () => {
  const query = keyword.value.trim()
  if (!query || !props.kbId) return
  const token = ++searchToken
  loading.value = true
  hasSearched.value = true
  try {
    const response = await documentApi.searchDocuments(props.kbId, { query, offset: 0, limit: 100 })
    if (token !== searchToken) return
    results.value = response?.files || []
    hasMore.value = Boolean(response?.has_more)
  } catch (error) {
    if (token !== searchToken) return
    console.warn('搜索文件失败:', error)
    results.value = []
    hasMore.value = false
  } finally {
    if (token === searchToken) loading.value = false
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return
    keyword.value = ''
    results.value = []
    hasSearched.value = false
    hasMore.value = false
    nextTick(() => searchInputRef.value?.focus())
  }
)
</script>

<style lang="less" scoped>
.file-search-overlay { position: fixed; inset: 0; z-index: 1200; display: flex; justify-content: center; padding: 18vh 16px; background: color-mix(in srgb, var(--gray-0) 72%, transparent); backdrop-filter: blur(2px); }
.file-search-modal { width: min(680px, calc(100vw - 32px)); max-height: 72vh; overflow: hidden; border: 1px solid var(--gray-150); border-radius: 12px; background: var(--gray-0); box-shadow: 0 24px 60px var(--shadow-1); }
.file-search-input-row { display: flex; align-items: center; gap: 12px; padding: 0 12px 0 18px; border-bottom: 1px solid var(--gray-100); color: var(--gray-500); }
.file-search-input-row input { flex: 1; height: 62px; border: 0; outline: 0; background: transparent; color: var(--gray-1000); font-size: 16px; }
.file-search-input-row button { display: inline-flex; border: 0; background: transparent; color: var(--gray-500); cursor: pointer; }
.file-search-body { min-height: 200px; max-height: calc(72vh - 63px); overflow-y: auto; padding: 8px; }
.file-search-result { display: flex; width: 100%; align-items: center; gap: 12px; padding: 10px 12px; border: 0; border-radius: 8px; background: transparent; color: var(--gray-800); cursor: pointer; text-align: left; }
.file-search-result:hover { background: var(--gray-50); }
.file-search-result > span { min-width: 0; }
.file-search-result strong, .file-search-result small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-search-result strong { color: var(--gray-1000); }
.file-search-result small, .file-search-hint { color: var(--gray-500); font-size: 12px; }
.file-search-hint { padding: 32px 16px; text-align: center; }
</style>
