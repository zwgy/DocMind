<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Check, Mail, X } from 'lucide-vue-next'
import { inboxApi } from '@/apis/inbox'

const props = defineProps<{ token?: string; open: boolean }>()
const emit = defineEmits<{ close: []; unreadChanged: [count: number] }>()
const category = ref<'notification' | 'task'>('notification')
const items = ref<any[]>([])
const loading = ref(false)
const error = ref('')
const count = ref(0)
const title = computed(() => category.value === 'task' ? '任务' : '通知')
function itemTitle(item: any) { return category.value === 'task' ? item.job?.name || '定时任务' : item.title }
function itemContent(item: any) { return category.value === 'task' ? item.latest_update?.content || '暂无状态更新' : item.content }
function itemId(item: any) { return category.value === 'task' ? item.job?.id : item.id }
function unread(item: any) { return category.value === 'task' ? item.unread_update_count > 0 : !item.is_read }
async function refresh() {
  if (!props.token) return
  loading.value = true; error.value = ''
  try {
    const [list, counts] = await Promise.all([inboxApi.list(category.value, props.token), inboxApi.unreadCount(props.token)])
    items.value = Array.isArray(list?.items) ? list.items : []
    count.value = Number(counts?.total_unread_count || 0)
    emit('unreadChanged', count.value)
  } catch (value) { error.value = value instanceof Error ? value.message : '加载收件箱失败' } finally { loading.value = false }
}
async function mark(item: any) { if (!unread(item) || !props.token) return; await inboxApi.markRead(category.value, itemId(item), props.token); await refresh() }
watch(() => [props.open, category.value, props.token], ([open]) => { if (open) void refresh() })
onMounted(() => { if (props.open) void refresh() })
</script>

<template><aside v-if="open" class="inbox-drawer"><header><strong>收件箱</strong><button type="button" title="关闭收件箱" @click="emit('close')"><X :size="17" /></button></header><div class="tabs"><button v-for="value in ['notification', 'task']" :key="value" :class="{ active: category === value }" @click="category = value as 'notification' | 'task'">{{ value === 'task' ? '任务' : '通知' }}</button></div><p v-if="loading" class="hint">加载中…</p><p v-else-if="error" class="hint error">{{ error }}</p><p v-else-if="!items.length" class="hint">暂无{{ title }}</p><button v-for="item in items" v-else :key="itemId(item)" type="button" class="inbox-row" :class="{ unread: unread(item) }" @click="mark(item)"><Mail :size="16" /><span><strong>{{ itemTitle(item) }}</strong><small>{{ itemContent(item) }}</small></span><Check v-if="!unread(item)" :size="15" /></button></aside></template>
<style scoped>.inbox-drawer{position:absolute;z-index:6;inset:44px 0 0;background:var(--surface,#fff);border-top:1px solid var(--border,#e5e7eb);overflow:auto}.inbox-drawer header{height:48px;padding:0 14px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border,#e5e7eb)}.inbox-drawer button{border:0;background:transparent;color:inherit;cursor:pointer}.tabs{display:flex;gap:4px;padding:8px 12px;border-bottom:1px solid var(--border,#e5e7eb)}.tabs button{padding:6px 10px;border-radius:4px}.tabs .active{background:var(--primary-50,#e6f7ff);color:var(--primary,#096dd9)}.hint{padding:24px;text-align:center;color:#6b7280}.error{color:#b91c1c}.inbox-row{display:flex!important;width:100%;gap:9px;padding:13px 14px;text-align:left;border-bottom:1px solid var(--border,#e5e7eb)!important}.inbox-row.unread{background:#f0f9ff}.inbox-row span{display:grid;gap:4px;min-width:0;flex:1}.inbox-row strong,.inbox-row small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.inbox-row small{color:#6b7280}</style>
