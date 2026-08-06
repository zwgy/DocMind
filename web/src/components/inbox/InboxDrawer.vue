<script setup>
import { computed, watch } from 'vue'
import { CheckCheck, Mail, RefreshCw } from 'lucide-vue-next'
import { useInboxStore } from '@/stores/inbox'

const store = useInboxStore()
const page = computed(() => store.currentPage)
const tabs = [{ value: 'notification', label: '通知' }, { value: 'task', label: '任务' }]
function formatTime(value) { return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '-' }
function title(item) { return store.category === 'task' ? item.job?.name || '定时任务' : item.title }
function content(item) { return store.category === 'task' ? item.latest_update?.content || '暂无任务状态更新' : item.content }
function unread(item) { return store.category === 'task' ? item.unread_update_count > 0 : !item.is_read }
function itemId(item) { return store.category === 'task' ? item.job?.id : item.id }
watch(() => store.open, (open) => { if (open) void store.refresh() })
</script>

<template>
  <a-drawer :open="store.open" title="收件箱" placement="right" width="min(480px, 100vw)" @close="store.setOpen(false)">
    <template #extra><a-tooltip title="刷新"><a-button class="lucide-icon-btn" type="text" @click="store.refresh()"><RefreshCw :size="18" /></a-button></a-tooltip></template>
    <a-tabs :active-key="store.category" @change="store.setCategory">
      <a-tab-pane v-for="tab in tabs" :key="tab.value">
        <template #tab><span>{{ tab.label }}<sup v-if="store.counts[`${tab.value}_unread_count`]" class="unread-dot" /></span></template>
      </a-tab-pane>
    </a-tabs>
    <div class="drawer-actions"><a-button size="small" :disabled="!store.counts[`${store.category}_unread_count`]" @click="store.markAllRead()"><CheckCheck :size="15" />全部已读</a-button></div>
    <a-alert v-if="page.error" type="error" show-icon :message="page.error.message || '加载收件箱失败'" />
    <a-skeleton v-if="page.loading && !page.items.length" active :paragraph="{ rows: 5 }" />
    <a-empty v-else-if="!page.items.length" :description="store.category === 'task' ? '暂无任务' : '暂无通知'" />
    <div v-else class="inbox-list"><button v-for="item in page.items" :key="itemId(item)" type="button" class="inbox-item" :class="{ unread: unread(item) }" @click="unread(item) && store.markRead(store.category, itemId(item))"><Mail :size="18" /><span class="inbox-copy"><strong>{{ title(item) }}</strong><span>{{ content(item) }}</span><time>{{ formatTime(store.category === 'task' ? item.sort_at : item.created_at) }}</time></span><i v-if="unread(item)" class="unread-dot" /></button></div>
    <div v-if="page.cursor" class="load-more"><a-button :loading="page.moreLoading" @click="store.load()">加载更多</a-button></div>
  </a-drawer>
</template>

<style lang="less" scoped>
.drawer-actions { display: flex; justify-content: flex-end; margin: -4px 0 12px; }.drawer-actions :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 5px; }.inbox-list { border-top: 1px solid var(--gray-150); }.inbox-item { display: flex; width: 100%; gap: 10px; padding: 14px 4px; text-align: left; color: var(--color-text-secondary); background: transparent; border: 0; border-bottom: 1px solid var(--gray-100); cursor: pointer; }.inbox-item:hover { background: var(--gray-25); }.inbox-item.unread { color: var(--color-text); background: var(--main-10); }.inbox-copy { display: grid; min-width: 0; flex: 1; gap: 4px; }.inbox-copy strong, .inbox-copy span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.inbox-copy span, time { font-size: 13px; color: var(--color-text-secondary); }.unread-dot { display: inline-block; width: 7px; height: 7px; margin-left: 5px; border-radius: 50%; background: var(--color-error-500); vertical-align: middle; }.inbox-item .unread-dot { margin-top: 7px; }.load-more { display: flex; justify-content: center; padding: 16px; }
</style>
