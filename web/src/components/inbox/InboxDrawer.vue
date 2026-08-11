<script setup>
import { computed, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  CheckCheck,
  Clock3,
  Eye,
  Mail,
  MessageSquareText,
  Paperclip,
  RefreshCw,
  Trash2
} from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { useInboxStore } from '@/stores/inbox'
import { useChatThreadsStore } from '@/stores/chatThreads'

const store = useInboxStore()
const chatThreadsStore = useChatThreadsStore()
const router = useRouter()
const page = computed(() => store.currentPage)
const tabs = [
  { value: 'notification', label: '通知' },
  { value: 'task', label: '任务' }
]
const hasReadItems = computed(() =>
  page.value.items.some((item) => (store.category === 'task' ? !unread(item) : item.is_read))
)
function formatTime(value) {
  return value
    ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(
        new Date(value)
      )
    : '-'
}
function title(item) {
  return store.category === 'task' ? item.job?.name || '定时任务' : item.title
}
function content(item) {
  return store.category === 'task'
    ? item.latest_update?.content || '暂无任务状态更新'
    : item.content
}
function unread(item) {
  return store.category === 'task' ? item.unread_update_count > 0 : !item.is_read
}
function itemId(item) {
  return store.category === 'task' ? item.job?.id : item.id
}
function taskRun(item) {
  return item.latest_unread_run || item.latest_run || null
}
function runStatus(status) {
  return (
    {
      queued: '排队中',
      running: '执行中',
      succeeded: '已完成',
      failed: '失败',
      cancelled: '已取消'
    }[status] || status
  )
}
function canViewResult(item) {
  const run = taskRun(item)
  return run?.conversation_thread_id && ['succeeded', 'failed', 'cancelled'].includes(run.status)
}
async function openResult(item) {
  const run = taskRun(item)
  if (!run?.conversation_thread_id) return
  try {
    await chatThreadsStore.locateThread(run.conversation_thread_id)
    store.setOpen(false)
    await router.push({
      name: 'AgentCompWithThreadId',
      params: { thread_id: run.conversation_thread_id },
      query: { scheduled_job_id: item.job.id, scheduled_run_id: run.id }
    })
  } catch (error) {
    message.error(error?.message || '加载任务结果失败')
  }
}
function sourceThreadId(item) {
  return item.job?.source_snapshot?.thread_id?.trim() || ''
}
async function openSource(item) {
  const threadId = sourceThreadId(item)
  if (!threadId) return
  try {
    await chatThreadsStore.locateThread(threadId)
    store.setOpen(false)
    await router.push({ name: 'AgentCompWithThreadId', params: { thread_id: threadId } })
  } catch (error) {
    message.error(error?.message || '创建任务的会话已不存在')
  }
}
function handleItem(item) {
  if (store.category === 'notification' && !item.is_read)
    void store.markRead('notification', item.id)
}
async function removeItem(item) {
  try {
    await store.remove(store.category, itemId(item))
    message.success('记录已删除')
  } catch (error) {
    message.error(error?.message || '删除失败')
  }
}
async function clearRead() {
  try {
    await store.clearRead()
    message.success('已读记录已删除')
  } catch (error) {
    message.error(error?.message || '删除已读失败')
  }
}
watch(
  () => store.open,
  (open) => {
    if (open) void store.refresh()
  }
)
</script>

<template>
  <a-drawer
    :open="store.open"
    title="收件箱"
    placement="right"
    width="min(480px, 100vw)"
    @close="store.setOpen(false)"
  >
    <template #extra
      ><a-tooltip title="刷新"
        ><a-button class="lucide-icon-btn" type="text" @click="store.refresh()"
          ><RefreshCw :size="18" /></a-button></a-tooltip
    ></template>
    <a-tabs :active-key="store.category" @change="store.setCategory">
      <a-tab-pane v-for="tab in tabs" :key="tab.value">
        <template #tab
          ><span
            >{{ tab.label
            }}<sup v-if="store.counts[`${tab.value}_unread_count`]" class="unread-dot" /></span
        ></template>
      </a-tab-pane>
    </a-tabs>
    <div class="drawer-actions">
      <a-button
        class="mark-all-read-button"
        size="small"
        :disabled="!store.counts[`${store.category}_unread_count`]"
        @click="store.markAllRead()"
        ><CheckCheck :size="15" />全部已读</a-button
      >
      <a-popconfirm
        title="删除当前分类的已读记录？未读记录和结果会话不会受影响。"
        ok-text="删除"
        cancel-text="返回"
        @confirm="clearRead"
      >
        <a-button class="delete-read-button" size="small" :disabled="!hasReadItems"
          ><Trash2 :size="15" />删除已读</a-button
        >
      </a-popconfirm>
    </div>
    <a-alert
      v-if="page.error"
      type="error"
      show-icon
      :message="page.error.message || '加载收件箱失败'"
    />
    <a-skeleton v-if="page.loading && !page.items.length" active :paragraph="{ rows: 5 }" />
    <a-empty
      v-else-if="!page.items.length"
      :description="store.category === 'task' ? '暂无任务' : '暂无通知'"
    />
    <div v-else class="inbox-list">
      <article
        v-for="item in page.items"
        :key="itemId(item)"
        class="inbox-item"
        :class="{ unread: unread(item), clickable: store.category === 'notification' }"
        @click="handleItem(item)"
      >
        <Clock3 v-if="store.category === 'task'" :size="18" /><Mail v-else :size="18" /><span
          class="inbox-copy"
          ><strong>{{ title(item) }}</strong
          ><span v-if="store.category === 'task' && taskRun(item)" class="run-status">{{
            runStatus(taskRun(item).status)
          }}</span
          ><span>{{
            store.category === 'task'
              ? taskRun(item)?.result_preview || content(item)
              : content(item)
          }}</span
          ><span v-if="store.category === 'task' && taskRun(item)" class="run-meta"
            ><time>{{ formatTime(taskRun(item).finished_at || taskRun(item).started_at) }}</time
            ><span><Paperclip :size="13" />{{ taskRun(item).artifact_count || 0 }} 个产物</span
            ><span v-if="item.unread_run_count > 1"
              >另有 {{ item.unread_run_count - 1 }} 次未读运行</span
            ></span
          ><time v-else>{{ formatTime(item.created_at) }}</time
          ><span class="inbox-actions"
            ><a-button
              v-if="store.category === 'task' && canViewResult(item)"
              type="text"
              size="small"
              @click.stop="openResult(item)"
              ><Eye :size="14" />查看结果</a-button
            ><a-button
              v-if="store.category === 'task' && sourceThreadId(item)"
              type="text"
              size="small"
              @click.stop="openSource(item)"
              ><MessageSquareText :size="14" />查看来源</a-button
            ><a-popconfirm
              title="确认删除这条记录？"
              ok-text="删除"
              cancel-text="返回"
              @confirm="removeItem(item)"
              ><a-button type="text" size="small" danger @click.stop
                ><Trash2 :size="14" />删除</a-button
              ></a-popconfirm
            ></span
          ></span
        ><i v-if="unread(item)" class="unread-dot" />
      </article>
    </div>
    <div v-if="page.cursor" class="load-more">
      <a-button :loading="page.moreLoading" @click="store.load()">加载更多</a-button>
    </div>
  </a-drawer>
</template>

<style lang="less" scoped>
.drawer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin: -4px 0 12px;
}
.drawer-actions :deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.drawer-actions :deep(.mark-all-read-button:not(:disabled)) {
  border-color: var(--main-100);
  color: var(--main-700);
  background: var(--main-50);
}
.drawer-actions :deep(.mark-all-read-button:not(:disabled):hover) {
  border-color: var(--main-500);
}
.drawer-actions :deep(.delete-read-button:not(:disabled)) {
  border-color: var(--color-error-100);
  color: var(--color-error-700);
  background: var(--color-error-50);
}
.drawer-actions :deep(.delete-read-button:not(:disabled):hover) {
  border-color: var(--color-error-500);
}
.inbox-list {
  border-top: 1px solid var(--gray-150);
}
.inbox-item {
  display: flex;
  width: 100%;
  gap: 10px;
  padding: 14px 4px;
  text-align: left;
  color: var(--color-text-secondary);
  background: transparent;
  border-bottom: 1px solid var(--gray-100);
}
.inbox-item.clickable {
  cursor: pointer;
}
.inbox-item:hover {
  background: var(--gray-25);
}
.inbox-item.unread {
  color: var(--color-text);
  background: var(--main-10);
}
.inbox-copy {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 4px;
}
.inbox-copy strong,
.inbox-copy > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.inbox-copy span,
time {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.run-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}
.run-meta span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.inbox-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-self: start;
  gap: 4px;
  margin-top: 2px;
  overflow: visible;
  white-space: normal;
}
.inbox-actions :deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.unread-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-left: 5px;
  border-radius: 50%;
  background: var(--color-error-500);
  vertical-align: middle;
}
.inbox-item .unread-dot {
  margin-top: 7px;
}
.load-more {
  display: flex;
  justify-content: center;
  padding: 16px;
}
.run-status {
  justify-self: start;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--gray-100);
  color: var(--gray-700);
}
</style>
