<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Bell, Bot, CalendarClock, CheckCheck, Pause, Play, RefreshCw, Save, Trash2, X } from 'lucide-vue-next'
import { inboxApi } from '@/apis/inbox'
import type { InboxItem, TaskInboxItem } from '@/apis/inbox'
import { scheduledJobApi } from '@/apis/scheduled-jobs'
import type { ScheduledJob, ScheduledJobView } from '@/apis/scheduled-jobs'
import { describeCron, describeInterval } from '@/utils/scheduled-job-display'

const props = defineProps<{ token?: string; open: boolean }>()
const emit = defineEmits<{ close: []; unreadChanged: [count: number] }>()

const section = ref<'scheduled' | 'inbox'>('scheduled')
const scheduleView = ref<ScheduledJobView>('ongoing')
const inboxCategory = ref<'notification' | 'task'>('notification')
const scheduledPages = ref<Record<ScheduledJobView, { items: ScheduledJob[]; cursor: string | null }>>({
  ongoing: { items: [], cursor: null },
  paused: { items: [], cursor: null },
  history: { items: [], cursor: null }
})
const inboxItems = ref<InboxItem[]>([])
const inboxCursor = ref<string | null>(null)
const loading = ref(false)
const loadingMore = ref(false)
const actingJobId = ref<string | null>(null)
const cancellingJob = ref<ScheduledJob | null>(null)
const editingJob = ref<ScheduledJob | null>(null)
const editForm = ref(createEditForm())
const saving = ref(false)
const error = ref('')

const currentScheduledPage = computed(() => scheduledPages.value[scheduleView.value])
const currentItems = computed(() => section.value === 'scheduled' ? currentScheduledPage.value.items : inboxItems.value)
const currentTitle = computed(() => section.value === 'scheduled' ? '定时任务' : inboxCategory.value === 'notification' ? '通知' : '任务')

function createEditForm() {
  return {
    name: '',
    title: '',
    content: '',
    instruction: '',
    timeoutSeconds: 900,
    scheduleKind: 'at' as ScheduledJob['schedule_kind'],
    runAt: '',
    anchorAt: '',
    intervalSeconds: 3600,
    cronExpression: ''
  }
}

function toLocalInput(value: string | null) {
  return value ? value.slice(0, 16) : ''
}

function formatTime(value: string | null | undefined) {
  return value
    ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
    : '-'
}

function scheduleSummary(job: ScheduledJob) {
  if (job.schedule_kind === 'at') return `单次 · ${formatTime(job.run_at)}`
  if (job.schedule_kind === 'interval') return describeInterval(job.interval_seconds)
  return describeCron(job.cron_expression)
}

function jobContent(job: ScheduledJob) {
  const content = job.action_type === 'notification'
    ? job.action_data?.content
    : job.action_data?.instruction
  return typeof content === 'string' && content.trim() ? content.trim() : '未填写正文'
}

function jobTrigger(job: ScheduledJob) {
  if (job.next_run_at) return `下一次触发：${formatTime(job.next_run_at)}`
  const lastRunAt = job.last_run_at || (job.status === 'completed' && job.schedule_kind === 'at' ? job.run_at : null)
  if (lastRunAt) return `最近触发：${formatTime(lastRunAt)}`
  return job.status === 'cancelled' ? '尚未触发' : '暂无触发记录'
}

function statusText(status: string) {
  return { active: '进行中', paused: '已暂停', completed: '已完成', cancelled: '已取消' }[status] || status
}

function notificationType(item: InboxItem) {
  if (isTaskItem(item)) return 'Agent 执行状态'
  if (item.item_type === 'notification_delivered') return '定时通知'
  if (item.item_type === 'run_partial') return '通知部分送达'
  if (item.item_type === 'run_skipped') return '通知未送达'
  return '通知异常'
}

function isTaskItem(item: InboxItem): item is TaskInboxItem {
  return 'job' in item
}

function itemId(item: InboxItem) {
  return isTaskItem(item) ? item.job.id : item.id
}

function unread(item: InboxItem) {
  return isTaskItem(item) ? item.unread_update_count > 0 : !item.is_read
}

function taskTrigger(item: TaskInboxItem) {
  return item.job.next_run_at
    ? `下一次触发：${formatTime(item.job.next_run_at)}`
    : `最近状态：${formatTime(item.latest_update?.created_at || item.sort_at)}`
}

function taskAction(item: TaskInboxItem) {
  return item.job.agent_slug ? `执行 Agent · ${item.job.agent_slug}` : '执行 Agent'
}

async function refresh({ reset = true }: { reset?: boolean } = {}) {
  if (!props.token || loading.value || loadingMore.value) return
  if (reset) loading.value = true
  else loadingMore.value = true
  error.value = ''
  try {
    if (section.value === 'scheduled') {
      const page = currentScheduledPage.value
      const response = await scheduledJobApi.list(scheduleView.value, props.token, reset ? undefined : page.cursor || undefined)
      const items = Array.isArray(response?.items) ? response.items : []
      page.items = reset ? items : [...page.items, ...items]
      page.cursor = response?.next_cursor || null
    } else {
      const [response, counts] = await Promise.all([
        inboxApi.list(inboxCategory.value, props.token, reset ? undefined : inboxCursor.value || undefined),
        inboxApi.unreadCount(props.token)
      ])
      const items = Array.isArray(response?.items) ? response.items : []
      inboxItems.value = reset ? items : [...inboxItems.value, ...items]
      inboxCursor.value = response?.next_cursor || null
      emit('unreadChanged', Number(counts?.total_unread_count || 0))
    }
  } catch (value) {
    error.value = value instanceof Error ? value.message : '加载定时中心失败'
  } finally {
    loading.value = false
    loadingMore.value = false
  }
}

async function refreshScheduledPages() {
  if (!props.token) return
  const views: ScheduledJobView[] = ['ongoing', 'paused', 'history']
  const responses = await Promise.all(views.map((view) => scheduledJobApi.list(view, props.token)))
  for (const [index, view] of views.entries()) {
    scheduledPages.value[view] = {
      items: Array.isArray(responses[index]?.items) ? responses[index].items : [],
      cursor: responses[index]?.next_cursor || null
    }
  }
}

async function changeStatus(job: ScheduledJob, action: 'pause' | 'resume' | 'cancel') {
  if (!props.token) return
  actingJobId.value = job.id
  try {
    await scheduledJobApi.changeStatus(job.id, { action, version: job.version }, props.token)
    cancellingJob.value = null
    await refreshScheduledPages()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '更新任务状态失败'
  } finally {
    actingJobId.value = null
  }
}

function openEditor(job: ScheduledJob) {
  editingJob.value = job
  editForm.value = {
    name: job.name,
    title: String(job.action_data?.title || ''),
    content: String(job.action_data?.content || ''),
    instruction: String(job.action_data?.instruction || ''),
    timeoutSeconds: Number(job.action_data?.timeout_seconds || 900),
    scheduleKind: job.schedule_kind,
    runAt: toLocalInput(job.run_at),
    anchorAt: toLocalInput(job.anchor_at),
    intervalSeconds: Number(job.interval_seconds || 3600),
    cronExpression: job.cron_expression || ''
  }
}

function currentSchedule() {
  const form = editForm.value
  if (form.scheduleKind === 'at') return { kind: 'at', run_at: new Date(form.runAt).toISOString() }
  if (form.scheduleKind === 'interval') {
    return { kind: 'interval', interval_seconds: Number(form.intervalSeconds), anchor_at: new Date(form.anchorAt).toISOString() }
  }
  return { kind: 'cron', cron_expression: form.cronExpression }
}

async function saveEditor() {
  const job = editingJob.value
  if (!job || !props.token) return
  saving.value = true
  error.value = ''
  try {
    const form = editForm.value
    const action = job.action_type === 'agent'
      ? {
          type: 'agent',
          agent_slug: String(job.action_data?.agent_slug || ''),
          instruction: form.instruction,
          timeout_seconds: Number(form.timeoutSeconds)
        }
      : { type: 'notification', title: form.title, content: form.content }
    await scheduledJobApi.update(job.id, {
      version: job.version,
      name: form.name,
      timezone: job.timezone,
      schedule: currentSchedule(),
      action
    }, props.token)
    editingJob.value = null
    await refreshScheduledPages()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '更新任务失败'
  } finally {
    saving.value = false
  }
}

async function mark(item: InboxItem) {
  if (!unread(item) || !props.token) return
  try {
    await inboxApi.markRead(inboxCategory.value, itemId(item), props.token)
    await refresh()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '标记已读失败'
  }
}

async function markAll() {
  if (!props.token || !inboxItems.value.some(unread)) return
  try {
    await inboxApi.markAllRead(inboxCategory.value, props.token)
    await refresh()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '标记已读失败'
  }
}

watch(() => [props.open, props.token, section.value, scheduleView.value, inboxCategory.value], ([open]) => {
  if (open) void refresh()
})
onMounted(() => { if (props.open) void refresh() })
</script>

<template>
  <aside v-if="open" class="timing-center" aria-label="定时中心">
    <header class="center-header">
      <strong>定时中心</strong>
      <div class="header-actions">
        <button type="button" title="刷新" :disabled="loading" @click="refresh()"><RefreshCw :size="16" /></button>
        <button type="button" title="关闭定时中心" @click="emit('close')"><X :size="17" /></button>
      </div>
    </header>

    <nav class="primary-tabs" aria-label="定时中心分区">
      <button type="button" :class="{ active: section === 'scheduled' }" @click="section = 'scheduled'">我的定时</button>
      <button type="button" :class="{ active: section === 'inbox' }" @click="section = 'inbox'">收件箱</button>
    </nav>

    <div v-if="section === 'scheduled'" class="secondary-tabs">
      <div class="secondary-tablist" role="tablist" aria-label="我的定时状态">
        <button v-for="view in [{ value: 'ongoing', label: '进行中' }, { value: 'paused', label: '已暂停' }, { value: 'history', label: '历史' }]" :key="view.value" type="button" :class="{ active: scheduleView === view.value }" @click="scheduleView = view.value as ScheduledJobView">{{ view.label }}</button>
      </div>
    </div>
    <div v-else class="secondary-tabs">
      <div class="secondary-tablist" role="tablist" aria-label="收件箱分类">
        <button type="button" :class="{ active: inboxCategory === 'notification' }" @click="inboxCategory = 'notification'">通知</button>
        <button type="button" :class="{ active: inboxCategory === 'task' }" @click="inboxCategory = 'task'">任务</button>
      </div>
      <button type="button" class="mark-all" :disabled="!inboxItems.some(unread)" @click="markAll"><CheckCheck :size="15" />全部已读</button>
    </div>

    <p v-if="error" class="hint error">{{ error }}</p>
    <p v-else-if="loading && !currentItems.length" class="hint">加载中…</p>
    <p v-else-if="!currentItems.length" class="hint">暂无{{ currentTitle }}</p>

    <section v-else class="center-list">
      <template v-if="section === 'scheduled'">
        <article v-for="job in currentScheduledPage.items" :key="job.id" class="scheduled-card">
          <div class="card-icon" :class="job.action_type"><Bell v-if="job.action_type === 'notification'" :size="18" /><Bot v-else :size="18" /></div>
          <div class="card-body">
            <div class="card-title"><strong>{{ job.name }}</strong><span class="status" :class="job.status">{{ statusText(job.status) }}</span></div>
            <div class="card-meta"><span>{{ job.action_type === 'notification' ? '站内通知' : '执行 Agent' }}</span><span>{{ scheduleSummary(job) }}</span><span>{{ job.timezone }}</span></div>
            <p class="job-content" :title="jobContent(job)">{{ jobContent(job) }}</p>
            <div class="trigger"><CalendarClock :size="14" />{{ jobTrigger(job) }}</div>
            <div v-if="['active', 'paused'].includes(job.status)" class="card-actions">
              <button type="button" title="编辑任务" @click="openEditor(job)">编辑</button>
              <button v-if="job.status === 'active' && job.schedule_kind !== 'at'" type="button" :disabled="actingJobId === job.id" @click="changeStatus(job, 'pause')"><Pause :size="14" />暂停</button>
              <button v-if="job.status === 'paused'" type="button" :disabled="actingJobId === job.id" @click="changeStatus(job, 'resume')"><Play :size="14" />恢复</button>
              <button type="button" class="danger" :disabled="actingJobId === job.id" @click="cancellingJob = job"><Trash2 :size="14" />取消</button>
            </div>
          </div>
        </article>
      </template>
      <template v-else>
        <button v-for="item in inboxItems" :key="itemId(item)" type="button" class="inbox-card" :class="{ unread: unread(item) }" @click="mark(item)">
          <div class="card-icon" :class="isTaskItem(item) ? 'agent' : 'notification'"><Bot v-if="isTaskItem(item)" :size="18" /><Bell v-else :size="18" /></div>
          <div class="card-body">
            <div class="card-title"><strong>{{ isTaskItem(item) ? item.job.name : item.title }}</strong><span class="status" :class="unread(item) ? 'active' : 'read'">{{ unread(item) ? '未读' : '已读' }}</span></div>
            <div class="card-meta"><span>{{ notificationType(item) }}</span><span v-if="isTaskItem(item)">{{ taskAction(item) }}</span><span v-if="isTaskItem(item)">{{ item.job.timezone }}</span></div>
            <p>{{ isTaskItem(item) ? item.latest_update?.content || '暂无状态更新' : item.content }}</p>
            <div class="trigger"><CalendarClock :size="14" />{{ isTaskItem(item) ? taskTrigger(item) : `触发时间：${formatTime(item.created_at)}` }}</div>
          </div>
        </button>
      </template>
    </section>
    <div v-if="(section === 'scheduled' ? currentScheduledPage.cursor : inboxCursor)" class="load-more"><button type="button" :disabled="loadingMore" @click="refresh({ reset: false })">{{ loadingMore ? '加载中…' : '加载更多' }}</button></div>

    <div v-if="cancellingJob" class="dialog-mask">
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-label="取消定时任务">
        <strong>取消“{{ cancellingJob.name }}”吗？</strong>
        <p>取消后不会再触发，历史记录仍会保留。</p>
        <div><button type="button" @click="cancellingJob = null">返回</button><button type="button" class="danger" :disabled="actingJobId === cancellingJob.id" @click="changeStatus(cancellingJob, 'cancel')">确认取消</button></div>
      </section>
    </div>

    <div v-if="editingJob" class="dialog-mask">
      <section class="editor-dialog" role="dialog" aria-modal="true" aria-label="编辑定时任务">
        <header><strong>编辑定时任务</strong><button type="button" title="关闭编辑" @click="editingJob = null"><X :size="17" /></button></header>
        <label>任务名称<input v-model="editForm.name" maxlength="100" /></label>
        <template v-if="editingJob.action_type === 'notification'">
          <label>通知标题<input v-model="editForm.title" maxlength="100" /></label>
          <label>通知正文<textarea v-model="editForm.content" rows="3" maxlength="4000"></textarea></label>
        </template>
        <template v-else>
          <label>执行指令<textarea v-model="editForm.instruction" rows="4" maxlength="8000"></textarea></label>
          <label>超时（秒）<input v-model.number="editForm.timeoutSeconds" type="number" min="60" max="3600" /></label>
        </template>
        <label>调度类型<select v-model="editForm.scheduleKind"><option value="at">单次</option><option value="interval">间隔</option><option value="cron">Cron</option></select></label>
        <label v-if="editForm.scheduleKind === 'at'">触发时间<input v-model="editForm.runAt" type="datetime-local" /></label>
        <template v-else-if="editForm.scheduleKind === 'interval'"><label>间隔（秒）<input v-model.number="editForm.intervalSeconds" type="number" min="60" step="60" /></label><label>首次触发<input v-model="editForm.anchorAt" type="datetime-local" /></label></template>
        <label v-else>Cron 表达式<input v-model="editForm.cronExpression" placeholder="0 9 * * 1-5" /></label>
        <small>时区：{{ editingJob.timezone }}</small>
        <footer><button type="button" @click="editingJob = null">取消</button><button type="button" class="primary" :disabled="saving" @click="saveEditor"><Save :size="15" />{{ saving ? '保存中…' : '保存' }}</button></footer>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.timing-center { position: absolute; z-index: 6; inset: 44px 0 0; display: flex; flex-direction: column; color: var(--gray-800); background: var(--gray-0); border-top: 1px solid var(--gray-200); overflow: hidden; }
.timing-center button { border: 0; color: inherit; background: transparent; cursor: pointer; font: inherit; }
.center-header { display: grid; grid-template-columns: 64px minmax(0, 1fr) 64px; align-items: center; min-height: 48px; padding: 0 12px; border-bottom: 1px solid var(--gray-200); }
.center-header > strong { grid-column: 2; color: var(--gray-1000); font-size: 15px; text-align: center; }
.header-actions, .card-title, .card-meta, .trigger, .card-actions { display: flex; align-items: center; }
.header-actions { gap: 2px; }
.center-header .header-actions { grid-column: 3; justify-self: end; }
.header-actions button { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 6px; }
.header-actions button:hover { color: var(--main-900); background: var(--main-50); }
.primary-tabs, .secondary-tabs { flex: 0 0 auto; padding: 0 14px; border-bottom: 1px solid var(--gray-200); }
.primary-tabs { display: flex; justify-content: center; gap: 28px; }
.primary-tabs button, .secondary-tablist button { position: relative; padding: 12px 2px 10px; color: var(--gray-600); }
.primary-tabs button { min-width: 72px; font-size: 14px; }
.primary-tabs button.active, .secondary-tablist button.active { color: var(--main-700); font-weight: 600; }
.primary-tabs button.active::after, .secondary-tablist button.active::after { position: absolute; right: 0; bottom: -1px; left: 0; height: 2px; background: var(--main-700); content: ''; }
.secondary-tabs { display: flex; align-items: center; justify-content: space-between; min-height: 39px; gap: 12px; background: var(--gray-25); }
.secondary-tablist { display: flex; align-items: center; gap: 20px; }
.secondary-tablist button { padding-top: 10px; padding-bottom: 8px; font-size: 13px; }
.mark-all { display: inline-flex; align-items: center; gap: 4px; color: var(--main-700) !important; font-size: 13px; }
.mark-all:disabled, .card-actions button:disabled { color: var(--gray-400) !important; cursor: not-allowed; }
.hint { margin: auto 0; padding: 24px; color: var(--gray-500); text-align: center; }
.hint.error { color: var(--color-error-700); }
.center-list { display: grid; gap: 8px; padding: 12px; overflow: auto; }
.scheduled-card, .inbox-card { display: flex; gap: 10px; width: 100%; padding: 12px; color: inherit; background: var(--gray-0); border: 1px solid var(--gray-200); border-radius: 8px; text-align: left; }
.inbox-card { cursor: pointer; }
.inbox-card:hover, .scheduled-card:hover { border-color: var(--main-200); background: var(--main-10); }
.inbox-card.unread { border-left: 3px solid var(--main-600); padding-left: 10px; background: var(--main-10); }
.card-icon { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; width: 32px; height: 32px; border-radius: 7px; }
.card-icon.notification { color: var(--color-info-700); background: var(--color-info-50); }
.card-icon.agent { color: var(--color-success-700); background: var(--color-success-50); }
.card-body { display: grid; min-width: 0; flex: 1; gap: 6px; }
.card-title { justify-content: space-between; gap: 8px; color: var(--gray-1000); }
.card-title strong, .inbox-card p { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status { flex: 0 0 auto; padding: 2px 6px; border-radius: 4px; color: var(--gray-600); background: var(--gray-100); font-size: 11px; font-weight: 600; }
.status.active { color: var(--color-info-700); background: var(--color-info-50); }.status.paused { color: var(--color-warning-700); background: var(--color-warning-50); }.status.completed { color: var(--color-success-700); background: var(--color-success-50); }.status.cancelled, .status.read { color: var(--gray-600); background: var(--gray-100); }
.card-meta { flex-wrap: wrap; gap: 0; color: var(--gray-600); font-size: 12px; }.card-meta span + span::before { margin: 0 6px; color: var(--gray-400); content: '·'; }
.inbox-card p, .job-content { margin: 0; color: var(--gray-700); font-size: 13px; }
.job-content { display: -webkit-box; overflow: hidden; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.trigger { gap: 5px; color: var(--gray-500); font-size: 12px; }
.card-actions { flex-wrap: wrap; gap: 8px; margin-top: 2px; }.card-actions button { display: inline-flex; align-items: center; gap: 4px; padding: 3px 0; color: var(--main-700); font-size: 12px; }.card-actions .danger, .danger { color: var(--color-error-700) !important; }
.load-more { display: flex; justify-content: center; padding: 4px 0 16px; }.load-more button { color: var(--main-700); }
.dialog-mask { position: absolute; z-index: 8; inset: 0; display: flex; align-items: flex-end; justify-content: center; padding: 12px; background: rgba(15, 23, 42, 0.28); }
.confirm-dialog, .editor-dialog { width: min(100%, 440px); max-height: calc(100% - 12px); padding: 16px; border: 1px solid var(--gray-200); border-radius: 8px; background: var(--gray-0); box-shadow: var(--shadow-panel); overflow: auto; }
.confirm-dialog p, .editor-dialog small { color: var(--gray-600); font-size: 12px; }.confirm-dialog > div, .editor-dialog footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }.confirm-dialog button, .editor-dialog footer button { padding: 7px 10px; border: 1px solid var(--gray-200); border-radius: 6px; }.editor-dialog { display: grid; gap: 11px; }.editor-dialog header { display: flex; justify-content: space-between; align-items: center; }.editor-dialog header button { display: inline-flex; }.editor-dialog label { display: grid; gap: 5px; color: var(--gray-700); font-size: 12px; }.editor-dialog input, .editor-dialog textarea, .editor-dialog select { width: 100%; padding: 7px 8px; border: 1px solid var(--gray-200); border-radius: 6px; color: var(--gray-800); background: var(--gray-0); font: inherit; }.editor-dialog textarea { resize: vertical; }.editor-dialog .primary { display: inline-flex; align-items: center; gap: 5px; color: var(--gray-0); background: var(--main-700); border-color: var(--main-700); }
@media (max-width: 420px) { .primary-tabs { gap: 18px; }.secondary-tablist { gap: 14px; }.center-list { padding: 10px; }.scheduled-card, .inbox-card { padding: 10px; } }
</style>
