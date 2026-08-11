<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  Bell,
  Bot,
  CalendarClock,
  CircleCheck,
  CheckCheck,
  Eye,
  Inbox,
  MessageCircleMore,
  Pause,
  Paperclip,
  Play,
  RefreshCw,
  Repeat2,
  Save,
  Trash2,
  TriangleAlert,
  X
} from 'lucide-vue-next'
import { VueDatePicker } from '@vuepic/vue-datepicker'
import '@vuepic/vue-datepicker/dist/main.css'
import { zhCN } from 'date-fns/locale'
import { inboxApi } from '@/apis/inbox'
import type { InboxItem, InboxUnreadCounts, TaskInboxItem } from '@/apis/inbox'
import { scheduledJobApi } from '@/apis/scheduled-jobs'
import type { ScheduledJob, ScheduledJobView } from '@/apis/scheduled-jobs'
import {
  buildCronExpression,
  describeCron,
  describeInterval,
  parseCronEditor,
  toZonedDateTimeInput
} from '@/utils/scheduled-job-display'

const props = defineProps<{
  token?: string
  open: boolean
  unreadCounts?: InboxUnreadCounts
  inboxNavigation?: { key: number; category: 'notification' | 'task' } | null
}>()
const emit = defineEmits<{
  close: []
  unreadChanged: [counts: InboxUnreadCounts]
  openResult: [payload: { jobId: string; runId: string; threadId: string }]
}>()
const datePickerActionRow = { selectBtnLabel: '确定', cancelBtnLabel: '取消' }
const datePickerInputAttrs = { clearable: false }
const datePickerTimeConfig = { enableSeconds: false, is24: true }

const section = ref<'scheduled' | 'inbox'>('scheduled')
const scheduleView = ref<ScheduledJobView>('ongoing')
const inboxCategory = ref<'notification' | 'task'>('notification')
const scheduledPages = ref<
  Record<ScheduledJobView, { items: ScheduledJob[]; cursor: string | null }>
>({
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
const deletingJob = ref<ScheduledJob | null>(null)
const deletingInboxItem = ref<InboxItem | null>(null)
const clearingRead = ref(false)
const editingJob = ref<ScheduledJob | null>(null)
const editForm = ref(createEditForm())
const saving = ref(false)
const error = ref('')
const successMessage = ref('')
const editorError = ref('')
const weekdayOptions = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 7, label: '日' }
]

const currentScheduledPage = computed(() => scheduledPages.value[scheduleView.value])
const currentItems = computed(() =>
  section.value === 'scheduled' ? currentScheduledPage.value.items : inboxItems.value
)
const hasUnreadInboxItems = computed(() => inboxItems.value.some(unread))
const hasReadInboxItems = computed(() => inboxItems.value.some((item) => !unread(item)))
const scheduleTypeLabel = computed(() =>
  editingJob.value?.action_type === 'agent' ? '执行方式' : '提醒方式'
)
const triggerTimeLabel = computed(() =>
  editingJob.value?.action_type === 'agent' ? '执行时间' : '提醒时间'
)
const firstTriggerTimeLabel = computed(() =>
  editingJob.value?.action_type === 'agent' ? '首次执行时间' : '首次提醒时间'
)
const emptyStateLabel = computed(() => {
  if (section.value === 'inbox')
    return inboxCategory.value === 'notification' ? '暂无通知' : '暂无任务状态'
  return {
    ongoing: '暂无进行中的定时',
    paused: '暂无已暂停的定时',
    history: '暂无历史记录'
  }[scheduleView.value]
})

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
    intervalMinutes: 60,
    cronExpression: '',
    cronRule: 'daily' as 'daily' | 'workdays' | 'weekly' | 'custom',
    cronTime: '09:00',
    cronWeekdays: [1] as number[]
  }
}

function formatTime(value: string | null | undefined, timezone?: string) {
  return value
    ? new Intl.DateTimeFormat('zh-CN', {
        dateStyle: 'medium',
        timeStyle: 'short',
        ...(timezone ? { timeZone: timezone } : {})
      }).format(new Date(value))
    : '-'
}

function scheduleMode(job: ScheduledJob) {
  const actionLabel = job.action_type === 'agent' ? '执行' : '提醒'
  return `${job.schedule_kind === 'at' ? '单次' : '重复'}${actionLabel}`
}

function scheduleRule(job: ScheduledJob) {
  if (job.schedule_kind === 'at') return formatTime(job.run_at, job.timezone)
  if (job.schedule_kind === 'interval') return describeInterval(job.interval_seconds)
  return describeCron(job.cron_expression)
}

function displayCount(value: number | null | undefined) {
  const count = Number(value || 0)
  return count > 99 ? '99+' : String(count)
}

function jobContent(job: ScheduledJob) {
  const content =
    job.action_type === 'notification' ? job.action_data?.content : job.action_data?.instruction
  return typeof content === 'string' && content.trim() ? content.trim() : '未填写正文'
}

function jobAction(job: ScheduledJob) {
  const agentSlug = job.action_data?.agent_slug
  return typeof agentSlug === 'string' && agentSlug ? `执行 Agent · ${agentSlug}` : '执行 Agent'
}

function jobTrigger(job: ScheduledJob) {
  if (job.next_run_at) return `下一次触发：${formatTime(job.next_run_at, job.timezone)}`
  const lastRunAt =
    job.last_run_at ||
    (job.status === 'completed' && job.schedule_kind === 'at' ? job.run_at : null)
  if (lastRunAt) return `最近触发：${formatTime(lastRunAt, job.timezone)}`
  return job.status === 'cancelled' ? '尚未触发' : '暂无触发记录'
}

function statusText(status: string) {
  return (
    { active: '进行中', paused: '已暂停', completed: '已完成', cancelled: '已取消' }[status] ||
    status
  )
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

function taskAction(item: TaskInboxItem) {
  return item.job.agent_slug ? `执行 Agent · ${item.job.agent_slug}` : '执行 Agent'
}

function taskRun(item: TaskInboxItem) {
  return item.latest_unread_run || item.latest_run
}

function taskResultContent(item: TaskInboxItem) {
  return taskRun(item)?.result_preview || item.latest_update?.content || '暂无状态更新'
}

function taskRunStatus(item: TaskInboxItem) {
  const status = taskRun(item)?.status || ''
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

function canViewTaskResult(item: TaskInboxItem) {
  const run = taskRun(item)
  return Boolean(
    run?.conversation_thread_id && ['succeeded', 'failed', 'cancelled'].includes(run.status)
  )
}

function inboxItemTime(item: InboxItem) {
  return isTaskItem(item) ? item.latest_update?.created_at || item.sort_at : item.created_at
}

function openTaskResult(item: TaskInboxItem) {
  const run = taskRun(item)
  if (!run?.conversation_thread_id) return
  emit('openResult', {
    jobId: item.job.id,
    runId: run.id,
    threadId: run.conversation_thread_id
  })
}

async function refresh({ reset = true }: { reset?: boolean } = {}) {
  if (!props.token || loading.value || loadingMore.value) return
  if (reset) loading.value = true
  else loadingMore.value = true
  error.value = ''
  successMessage.value = ''
  try {
    if (section.value === 'scheduled') {
      const page = currentScheduledPage.value
      const response = await scheduledJobApi.list(
        scheduleView.value,
        props.token,
        reset ? undefined : page.cursor || undefined
      )
      const items = Array.isArray(response?.items) ? response.items : []
      page.items = reset ? items : [...page.items, ...items]
      page.cursor = response?.next_cursor || null
    } else {
      const [response, counts] = await Promise.all([
        inboxApi.list(
          inboxCategory.value,
          props.token,
          reset ? undefined : inboxCursor.value || undefined
        ),
        inboxApi.unreadCount(props.token)
      ])
      const items = Array.isArray(response?.items) ? response.items : []
      inboxItems.value = reset ? items : [...inboxItems.value, ...items]
      inboxCursor.value = response?.next_cursor || null
      emit('unreadChanged', counts)
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

async function deleteJob() {
  const job = deletingJob.value
  if (!job || !props.token) return
  actingJobId.value = job.id
  try {
    await scheduledJobApi.remove(job.id, job.version, props.token)
    deletingJob.value = null
    successMessage.value = '定时记录已删除'
    await refreshScheduledPages()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '删除任务失败'
  } finally {
    actingJobId.value = null
  }
}

function openEditor(job: ScheduledJob) {
  const cronEditor = parseCronEditor(job.cron_expression)
  error.value = ''
  successMessage.value = ''
  editorError.value = ''
  editingJob.value = job
  editForm.value = {
    name: job.name,
    title: String(job.action_data?.title || ''),
    content: String(job.action_data?.content || ''),
    instruction: String(job.action_data?.instruction || ''),
    timeoutSeconds: Number(job.action_data?.timeout_seconds || 900),
    scheduleKind: job.schedule_kind,
    runAt: toZonedDateTimeInput(job.run_at, job.timezone),
    anchorAt: toZonedDateTimeInput(job.anchor_at, job.timezone),
    intervalMinutes: Number(job.interval_seconds || 3600) / 60,
    cronExpression: job.cron_expression || '',
    cronRule: cronEditor.rule,
    cronTime: cronEditor.time,
    cronWeekdays: cronEditor.weekdays
  }
}

function currentSchedule() {
  const form = editForm.value
  if (form.scheduleKind === 'at') return { kind: 'at', run_at: form.runAt }
  if (form.scheduleKind === 'interval') {
    return {
      kind: 'interval',
      interval_seconds: Number(form.intervalMinutes) * 60,
      anchor_at: form.anchorAt
    }
  }
  return {
    kind: 'cron',
    cron_expression: buildCronExpression(
      form.cronRule,
      form.cronTime,
      form.cronWeekdays,
      form.cronExpression
    )
  }
}

async function saveEditor() {
  const job = editingJob.value
  if (!job || !props.token) return
  saving.value = true
  editorError.value = ''
  try {
    const form = editForm.value
    const action =
      job.action_type === 'agent'
        ? {
            type: 'agent',
            agent_slug: String(job.action_data?.agent_slug || ''),
            instruction: form.instruction,
            timeout_seconds: Number(form.timeoutSeconds)
          }
        : { type: 'notification', title: form.title, content: form.content }
    const response = await scheduledJobApi.update(
      job.id,
      {
        version: job.version,
        name: form.name,
        timezone: job.timezone,
        schedule: currentSchedule(),
        action
      },
      props.token
    )
    for (const page of Object.values(scheduledPages.value)) {
      const index = page.items.findIndex((item) => item.id === job.id)
      if (index >= 0) page.items[index] = response.job
    }
    editingJob.value = null
    successMessage.value = '定时任务已保存'
    try {
      await refreshScheduledPages()
    } catch {
      successMessage.value = ''
      error.value = '任务已保存，但列表刷新失败，请点击右上角刷新按钮重试'
    }
  } catch (value) {
    editorError.value = value instanceof Error ? value.message : '更新任务失败'
  } finally {
    saving.value = false
  }
}

async function mark(item: InboxItem) {
  if (isTaskItem(item) || !unread(item) || !props.token) return
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

async function deleteInboxItem() {
  const item = deletingInboxItem.value
  if (!item || !props.token) return
  try {
    await inboxApi.remove(inboxCategory.value, itemId(item), props.token)
    deletingInboxItem.value = null
    successMessage.value = '收件记录已删除'
    await refresh()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '删除收件记录失败'
  }
}

async function clearReadItems() {
  if (!props.token) return
  try {
    await inboxApi.clearRead(inboxCategory.value, props.token)
    clearingRead.value = false
    successMessage.value = '已读记录已清空'
    await refresh()
  } catch (value) {
    error.value = value instanceof Error ? value.message : '清空已读失败'
  }
}

watch(
  () => [props.open, props.token, section.value, scheduleView.value, inboxCategory.value],
  ([open]) => {
    if (open) void refresh()
  }
)
watch(
  () => props.inboxNavigation?.key,
  () => {
    if (!props.inboxNavigation) return
    section.value = 'inbox'
    inboxCategory.value = props.inboxNavigation.category
    if (props.open) void refresh()
  }
)
onMounted(() => {
  if (props.open) void refresh()
})
</script>

<template>
  <aside v-if="open" class="timing-center" aria-label="定时中心">
    <header class="center-header">
      <strong>定时中心</strong>
      <div class="header-actions">
        <button type="button" title="刷新" :disabled="loading" @click="refresh()">
          <RefreshCw :size="16" />
        </button>
        <button type="button" title="关闭定时中心" @click="emit('close')"><X :size="17" /></button>
      </div>
    </header>

    <nav class="primary-tabs" aria-label="定时中心分区">
      <button
        type="button"
        :class="{ active: section === 'scheduled' }"
        @click="section = 'scheduled'"
      >
        我的定时
      </button>
      <button type="button" :class="{ active: section === 'inbox' }" @click="section = 'inbox'">
        收件箱
        <span v-if="unreadCounts?.total_unread_count" class="tab-count">{{
          displayCount(unreadCounts.total_unread_count)
        }}</span>
      </button>
    </nav>

    <div v-if="section === 'scheduled'" class="secondary-tabs">
      <div class="secondary-tablist" role="tablist" aria-label="我的定时状态">
        <button
          v-for="view in [
            { value: 'ongoing', label: '进行中' },
            { value: 'paused', label: '已暂停' },
            { value: 'history', label: '历史' }
          ]"
          :key="view.value"
          type="button"
          :class="{ active: scheduleView === view.value }"
          @click="scheduleView = view.value as ScheduledJobView"
        >
          {{ view.label }}
        </button>
      </div>
    </div>
    <div v-else class="secondary-tabs">
      <div class="secondary-tablist" role="tablist" aria-label="收件箱分类">
        <button
          type="button"
          :class="{ active: inboxCategory === 'notification' }"
          @click="inboxCategory = 'notification'"
        >
          通知
          <span v-if="unreadCounts?.notification_unread_count" class="tab-count">{{
            displayCount(unreadCounts.notification_unread_count)
          }}</span>
        </button>
        <button
          type="button"
          :class="{ active: inboxCategory === 'task' }"
          @click="inboxCategory = 'task'"
        >
          任务
          <span v-if="unreadCounts?.task_unread_count" class="tab-count">{{
            displayCount(unreadCounts.task_unread_count)
          }}</span>
        </button>
      </div>
      <button
        v-if="hasUnreadInboxItems"
        type="button"
        class="mark-all"
        :disabled="loading"
        @click="markAll"
      >
        <CheckCheck :size="14" />全部已读
      </button>
      <button
        v-if="hasReadInboxItems"
        type="button"
        class="mark-all danger"
        :disabled="loading"
        @click="clearingRead = true"
      >
        <Trash2 :size="14" />清空已读
      </button>
    </div>

    <p v-if="successMessage" class="feedback-banner success" role="status">
      <CircleCheck :size="15" />{{ successMessage }}
    </p>
    <p v-if="error" class="feedback-banner error" role="alert">{{ error }}</p>
    <p v-if="loading && !currentItems.length" class="hint">加载中…</p>
    <div v-else-if="!currentItems.length && !error" class="empty-state">
      <div class="empty-visual" aria-hidden="true">
        <CalendarClock v-if="section === 'scheduled'" :size="52" :stroke-width="1.35" />
        <Inbox v-else :size="54" :stroke-width="1.35" />
        <MessageCircleMore :size="22" :stroke-width="1.5" />
      </div>
      <p>{{ emptyStateLabel }}</p>
    </div>

    <section v-if="currentItems.length" class="center-list">
      <template v-if="section === 'scheduled'">
        <article v-for="job in currentScheduledPage.items" :key="job.id" class="scheduled-card">
          <div class="card-icon" :class="job.action_type">
            <Bell v-if="job.action_type === 'notification'" :size="18" /><Bot v-else :size="18" />
          </div>
          <div class="card-body">
            <div class="card-title">
              <strong>{{ job.name }}</strong
              ><span class="status" :class="job.status">{{ statusText(job.status) }}</span>
            </div>
            <div class="schedule-summary">
              <span class="schedule-mode">
                <CalendarClock v-if="job.schedule_kind === 'at'" :size="13" />
                <Repeat2 v-else :size="13" />
                {{ scheduleMode(job) }}
              </span>
              <span class="schedule-rule">{{ scheduleRule(job) }}</span>
            </div>
            <div v-if="job.action_type === 'agent'" class="card-meta">
              <span>{{ jobAction(job) }}</span>
            </div>
            <p
              v-if="scheduleView !== 'history' || job.action_type === 'agent'"
              class="job-content"
              :title="jobContent(job)"
            >
              {{ jobContent(job) }}
            </p>
            <div class="trigger"><CalendarClock :size="14" />{{ jobTrigger(job) }}</div>
            <div class="card-actions">
              <button
                v-if="['active', 'paused'].includes(job.status)"
                type="button"
                title="编辑任务"
                @click="openEditor(job)"
              >
                编辑
              </button>
              <button
                v-if="job.status === 'active' && job.schedule_kind !== 'at'"
                type="button"
                :disabled="actingJobId === job.id"
                @click="changeStatus(job, 'pause')"
              >
                <Pause :size="14" />暂停
              </button>
              <button
                v-if="job.status === 'paused'"
                type="button"
                :disabled="actingJobId === job.id"
                @click="changeStatus(job, 'resume')"
              >
                <Play :size="14" />恢复
              </button>
              <button
                v-if="['active', 'paused'].includes(job.status)"
                type="button"
                class="danger"
                :disabled="actingJobId === job.id"
                @click="cancellingJob = job"
              >
                <X :size="14" />取消
              </button>
              <button
                type="button"
                class="danger"
                :disabled="actingJobId === job.id"
                @click="deletingJob = job"
              >
                <Trash2 :size="14" />删除
              </button>
            </div>
          </div>
        </article>
      </template>
      <template v-else>
        <article
          v-for="item in inboxItems"
          :key="itemId(item)"
          class="inbox-card"
          :class="{ unread: unread(item), clickable: !isTaskItem(item) }"
          @click="mark(item)"
        >
          <div class="card-icon" :class="isTaskItem(item) ? 'agent' : 'notification'">
            <Bot v-if="isTaskItem(item)" :size="18" /><Bell v-else :size="18" />
          </div>
          <div class="card-body">
            <div class="card-title">
              <strong>{{ isTaskItem(item) ? item.job.name : item.title }}</strong
              ><span class="status" :class="unread(item) ? 'active' : 'read'">{{
                unread(item) ? '未读' : '已读'
              }}</span>
            </div>
            <div class="card-meta">
              <span>{{ notificationType(item) }}</span
              ><span v-if="isTaskItem(item)">{{ taskAction(item) }}</span>
              <span v-if="isTaskItem(item) && taskRun(item)">{{ taskRunStatus(item) }}</span>
            </div>
            <p class="inbox-content">
              {{ isTaskItem(item) ? taskResultContent(item) : item.content }}
            </p>
            <div v-if="isTaskItem(item) && taskRun(item)" class="task-run-meta">
              <span>{{ formatTime(taskRun(item)?.finished_at || taskRun(item)?.started_at) }}</span>
              <span><Paperclip :size="13" />{{ taskRun(item)?.artifact_count || 0 }} 个产物</span>
              <span v-if="item.unread_run_count > 1"
                >另有 {{ item.unread_run_count - 1 }} 次未读运行</span
              >
            </div>
            <div v-else class="trigger">
              <CalendarClock :size="14" />{{ `触发时间：${formatTime(inboxItemTime(item))}` }}
            </div>
            <button
              v-if="isTaskItem(item) && canViewTaskResult(item)"
              type="button"
              class="view-result"
              @click.stop="openTaskResult(item)"
            >
              <Eye :size="14" />查看结果
            </button>
            <button
              type="button"
              class="view-result danger"
              @click.stop="deletingInboxItem = item"
            >
              <Trash2 :size="14" />删除
            </button>
          </div>
        </article>
      </template>
    </section>
    <div
      v-if="section === 'scheduled' ? currentScheduledPage.cursor : inboxCursor"
      class="load-more"
    >
      <button type="button" :disabled="loadingMore" @click="refresh({ reset: false })">
        {{ loadingMore ? '加载中…' : '加载更多' }}
      </button>
    </div>

    <div v-if="cancellingJob" class="dialog-mask confirm-mask">
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-label="取消定时任务">
        <span class="confirm-icon" aria-hidden="true"><TriangleAlert :size="22" /></span>
        <h3>取消“{{ cancellingJob.name }}”吗？</h3>
        <p>取消后不会再触发，历史记录仍会保留。</p>
        <div class="confirm-actions">
          <button type="button" class="secondary" @click="cancellingJob = null">暂不取消</button
          ><button
            type="button"
            class="danger"
            :disabled="actingJobId === cancellingJob.id"
            @click="changeStatus(cancellingJob, 'cancel')"
          >
            确认取消
          </button>
        </div>
      </section>
    </div>

    <div v-if="deletingJob" class="dialog-mask confirm-mask">
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-label="删除定时任务">
        <span class="confirm-icon" aria-hidden="true"><TriangleAlert :size="22" /></span>
        <h3>删除“{{ deletingJob.name }}”吗？</h3>
        <p>任务定义和运行历史将被删除，已经生成的结果会话仍会保留。</p>
        <div class="confirm-actions">
          <button type="button" class="secondary" @click="deletingJob = null">暂不删除</button
          ><button
            type="button"
            class="danger"
            :disabled="actingJobId === deletingJob.id"
            @click="deleteJob"
          >
            确认删除
          </button>
        </div>
      </section>
    </div>

    <div v-if="deletingInboxItem" class="dialog-mask confirm-mask">
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-label="删除收件记录">
        <span class="confirm-icon" aria-hidden="true"><TriangleAlert :size="22" /></span>
        <h3>删除这条收件记录吗？</h3>
        <p>删除只清理当前账号的列表记录，不会删除关联的结果会话。</p>
        <div class="confirm-actions">
          <button type="button" class="secondary" @click="deletingInboxItem = null">暂不删除</button
          ><button type="button" class="danger" @click="deleteInboxItem">确认删除</button>
        </div>
      </section>
    </div>

    <div v-if="clearingRead" class="dialog-mask confirm-mask">
      <section class="confirm-dialog" role="dialog" aria-modal="true" aria-label="清空已读记录">
        <span class="confirm-icon" aria-hidden="true"><TriangleAlert :size="22" /></span>
        <h3>清空当前分类的已读记录吗？</h3>
        <p>未读记录不会受影响，关联的结果会话也会保留。</p>
        <div class="confirm-actions">
          <button type="button" class="secondary" @click="clearingRead = false">暂不清空</button
          ><button type="button" class="danger" @click="clearReadItems">确认清空</button>
        </div>
      </section>
    </div>

    <div v-if="editingJob" class="dialog-mask editor-mask">
      <section class="editor-dialog" role="dialog" aria-modal="true" aria-label="编辑定时任务">
        <header>
          <strong>编辑定时任务</strong
          ><button type="button" title="关闭编辑" @click="editingJob = null">
            <X :size="17" />
          </button>
        </header>
        <label>任务名称<input v-model="editForm.name" maxlength="100" /></label>
        <template v-if="editingJob.action_type === 'notification'">
          <label>通知标题<input v-model="editForm.title" maxlength="100" /></label>
          <label
            >通知正文<textarea v-model="editForm.content" rows="3" maxlength="4000"></textarea>
          </label>
        </template>
        <template v-else>
          <label
            >执行指令<textarea v-model="editForm.instruction" rows="4" maxlength="8000"></textarea>
          </label>
          <label
            >超时（秒）<input
              v-model.number="editForm.timeoutSeconds"
              type="number"
              min="60"
              max="3600"
          /></label>
        </template>
        <label
          >{{ scheduleTypeLabel
          }}<select v-model="editForm.scheduleKind">
            <option value="at">单次</option>
            <option value="interval">固定间隔</option>
            <option value="cron">按日/周重复</option>
          </select></label
        >
        <label v-if="editForm.scheduleKind === 'at'">
          {{ triggerTimeLabel }}
          <VueDatePicker
            v-model="editForm.runAt"
            class="schedule-picker"
            model-type="yyyy-MM-dd'T'HH:mm"
            format="yyyy-MM-dd HH:mm"
            :locale="zhCN"
            teleport="body"
            :action-row="datePickerActionRow"
            :input-attrs="datePickerInputAttrs"
            :time-config="datePickerTimeConfig"
          />
        </label>
        <template v-else-if="editForm.scheduleKind === 'interval'">
          <label
            >重复间隔（分钟）<input
              v-model.number="editForm.intervalMinutes"
              type="number"
              min="1"
              step="1"
          /></label>
          <label>
            {{ firstTriggerTimeLabel }}
            <VueDatePicker
              v-model="editForm.anchorAt"
              class="schedule-picker"
              model-type="yyyy-MM-dd'T'HH:mm"
              format="yyyy-MM-dd HH:mm"
              :locale="zhCN"
              teleport="body"
              :action-row="datePickerActionRow"
              :input-attrs="datePickerInputAttrs"
              :time-config="datePickerTimeConfig"
            />
          </label>
        </template>
        <template v-else>
          <label
            >重复规则<select v-model="editForm.cronRule">
              <option value="daily">每天</option>
              <option value="workdays">工作日</option>
              <option value="weekly">每周</option>
              <option v-if="editForm.cronRule === 'custom'" value="custom">自定义周期</option>
            </select></label
          >
          <div v-if="editForm.cronRule === 'weekly'" class="weekday-field">
            <span>重复日期</span>
            <div class="weekday-options">
              <label v-for="weekday in weekdayOptions" :key="weekday.value" class="weekday-option">
                <input v-model="editForm.cronWeekdays" type="checkbox" :value="weekday.value" />
                <span>周{{ weekday.label }}</span>
              </label>
            </div>
          </div>
          <label v-if="editForm.cronRule !== 'custom'">
            {{ triggerTimeLabel }}
            <VueDatePicker
              v-model="editForm.cronTime"
              class="schedule-picker"
              time-picker
              model-type="HH:mm"
              format="HH:mm"
              :locale="zhCN"
              teleport="body"
              :action-row="datePickerActionRow"
              :input-attrs="datePickerInputAttrs"
              :time-config="datePickerTimeConfig"
            />
          </label>
          <p v-else class="custom-schedule">
            当前任务使用高级自定义周期。如需修改，请先选择常用重复规则。
          </p>
        </template>
        <p v-if="editorError" class="editor-feedback" role="alert">保存失败：{{ editorError }}</p>
        <footer>
          <button type="button" @click="editingJob = null">取消</button
          ><button type="button" class="primary" :disabled="saving" @click="saveEditor">
            <Save :size="15" />{{ saving ? '保存中…' : '保存' }}
          </button>
        </footer>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.timing-center {
  position: absolute;
  z-index: 6;
  inset: 44px 0 0;
  display: flex;
  flex-direction: column;
  color: var(--gray-800);
  background: var(--gray-0);
  border-top: 1px solid var(--gray-200);
  overflow: hidden;
}
.timing-center button {
  border: 0;
  color: inherit;
  background: transparent;
  cursor: pointer;
  font: inherit;
}
.center-header {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr) 64px;
  align-items: center;
  min-height: 48px;
  padding: 0 12px;
  border-bottom: 1px solid var(--gray-200);
}
.center-header > strong {
  grid-column: 2;
  color: var(--gray-1000);
  font-size: 15px;
  text-align: center;
}
.header-actions,
.card-title,
.card-meta,
.schedule-summary,
.trigger,
.card-actions {
  display: flex;
  align-items: center;
}
.header-actions {
  gap: 2px;
}
.center-header .header-actions {
  grid-column: 3;
  justify-self: end;
}
.header-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
}
.header-actions button:hover {
  color: var(--main-900);
  background: var(--main-50);
}
.primary-tabs,
.secondary-tabs {
  flex: 0 0 auto;
  padding: 0 14px;
  border-bottom: 1px solid var(--gray-200);
}
.primary-tabs {
  display: flex;
  gap: 18px;
}
.primary-tabs button,
.secondary-tablist button {
  position: relative;
  padding: 12px 2px 10px;
  color: var(--gray-600);
}
.primary-tabs button,
.secondary-tablist button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.primary-tabs button {
  font-size: 14px;
}
.primary-tabs button.active,
.secondary-tablist button.active {
  color: var(--main-700);
  font-weight: 600;
}
.primary-tabs button.active::after,
.secondary-tablist button.active::after {
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 2px;
  background: var(--main-700);
  content: '';
}
.secondary-tabs {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 39px;
  gap: 12px;
  background: var(--gray-25);
}
.secondary-tablist {
  display: flex;
  align-items: center;
  gap: 20px;
}
.secondary-tablist button {
  padding-top: 10px;
  padding-bottom: 8px;
  font-size: 13px;
}
.tab-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border: 2px solid var(--gray-0);
  border-radius: 10px;
  color: var(--gray-0);
  background: var(--color-error-700);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.mark-all {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 7px;
  border-radius: 6px;
  color: var(--main-700) !important;
  font-size: 12px;
}
.mark-all:hover {
  background: var(--main-50);
}
.mark-all.danger,
.view-result.danger {
  color: var(--color-error-700) !important;
}
.mark-all:disabled,
.card-actions button:disabled {
  color: var(--gray-400) !important;
  cursor: not-allowed;
}
.hint {
  margin: auto 0;
  padding: 24px;
  color: var(--gray-500);
  text-align: center;
}
.hint.error {
  color: var(--color-error-700);
}
.feedback-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 10px 12px 0;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
}
.feedback-banner.success {
  color: var(--color-success-700);
  background: var(--gray-50);
  border-color: var(--gray-200);
}
.feedback-banner.error {
  color: var(--color-error-700);
  background: var(--color-error-50);
  border-color: var(--gray-200);
}
.empty-state {
  display: grid;
  justify-items: center;
  gap: 12px;
  margin: auto;
  padding: 32px 20px 72px;
  color: var(--gray-600);
  text-align: center;
}
.empty-state p {
  margin: 0;
  color: var(--gray-700);
  font-size: 14px;
}
.empty-visual {
  position: relative;
  width: 76px;
  height: 64px;
  color: var(--gray-300);
}
.empty-visual > svg:first-child {
  position: absolute;
  bottom: 0;
  left: 8px;
}
.empty-visual > svg:last-child {
  position: absolute;
  top: 0;
  right: 0;
  color: var(--gray-350, var(--gray-400));
  fill: var(--gray-25);
}
.center-list {
  display: grid;
  gap: 8px;
  padding: 12px;
  overflow: auto;
}
.scheduled-card,
.timing-center .inbox-card {
  display: flex;
  gap: 10px;
  width: 100%;
  padding: 12px;
  color: inherit;
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  text-align: left;
}
.inbox-card.clickable {
  cursor: pointer;
}
.inbox-card:hover,
.scheduled-card:hover {
  border-color: var(--main-200);
  background: var(--main-10);
}
.inbox-card.unread {
  border-left: 3px solid var(--main-600);
  padding-left: 10px;
  background: var(--main-10);
}
.card-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border-radius: 7px;
}
.card-icon.notification {
  color: var(--color-info-700);
  background: var(--color-info-50);
}
.card-icon.agent {
  color: var(--color-success-700);
  background: var(--color-success-50);
}
.card-body {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 6px;
}
.task-run-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  color: var(--gray-600);
  font-size: 12px;
}
.task-run-meta span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.view-result {
  display: inline-flex;
  align-items: center;
  justify-self: start;
  gap: 4px;
  padding: 3px 0;
  color: var(--main-700);
  background: transparent;
  border: 0;
  cursor: pointer;
}
.card-title {
  justify-content: space-between;
  gap: 8px;
  color: var(--gray-1000);
}
.card-title strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.status {
  flex: 0 0 auto;
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--gray-600);
  background: var(--gray-100);
  font-size: 11px;
  font-weight: 600;
}
.status.active {
  color: var(--color-info-700);
  background: var(--color-info-50);
}
.status.paused {
  color: var(--color-warning-700);
  background: var(--color-warning-50);
}
.status.completed {
  color: var(--color-success-700);
  background: var(--color-success-50);
}
.status.cancelled,
.status.read {
  color: var(--gray-600);
  background: var(--gray-100);
}
.card-meta {
  flex-wrap: wrap;
  gap: 0;
  color: var(--gray-600);
  font-size: 12px;
}
.card-meta span + span::before {
  margin: 0 6px;
  color: var(--gray-400);
  content: '·';
}
.job-content,
.inbox-content {
  display: -webkit-box;
  margin: 0;
  overflow: hidden;
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 500;
  line-height: 1.55;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.schedule-summary {
  flex-wrap: wrap;
  gap: 7px;
  margin: 1px 0;
}
.schedule-mode {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 7px;
  border-radius: 4px;
  color: var(--main-700);
  background: var(--main-50);
  font-size: 12px;
  font-weight: 700;
  line-height: 1.4;
}
.schedule-rule {
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
}
.trigger {
  gap: 5px;
  color: var(--gray-500);
  font-size: 12px;
}
.card-actions {
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 2px;
}
.card-actions button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 0;
  color: var(--main-700);
  font-size: 12px;
}
.card-actions .danger,
.danger {
  color: var(--color-error-700) !important;
}
.load-more {
  display: flex;
  justify-content: center;
  padding: 4px 0 16px;
}
.load-more button {
  color: var(--main-700);
}
.dialog-mask {
  position: absolute;
  z-index: 8;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  background: rgba(15, 23, 42, 0.32);
  backdrop-filter: blur(2px);
}
.confirm-dialog,
.editor-dialog {
  width: min(100%, 440px);
  max-height: calc(100% - 12px);
  padding: 16px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-0);
  box-shadow: var(--shadow-panel);
  overflow: auto;
}
.confirm-dialog p,
.editor-dialog small {
  color: var(--gray-600);
  font-size: 12px;
}
.confirm-dialog {
  width: min(100%, 336px);
  padding: 22px;
  text-align: center;
}
.confirm-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 12px;
  border-radius: 50%;
  color: var(--color-error-700);
  background: var(--color-error-50);
}
.confirm-dialog h3 {
  margin: 0;
  color: var(--gray-1000);
  font-size: 15px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.confirm-dialog p {
  margin: 8px 0 0;
  line-height: 1.55;
}
.confirm-dialog > div,
.editor-dialog footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
.confirm-dialog .confirm-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.confirm-dialog button,
.editor-dialog footer button {
  min-height: 36px;
  padding: 7px 12px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
}
.confirm-dialog .secondary:hover {
  border-color: var(--gray-300);
  background: var(--gray-50);
}
.confirm-dialog .danger {
  border-color: var(--color-error-700);
  color: var(--gray-0) !important;
  background: var(--color-error-700);
}
.confirm-dialog .danger:hover {
  filter: brightness(0.94);
}
.editor-dialog {
  display: grid;
  gap: 11px;
}
.editor-dialog header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.editor-dialog header button {
  display: inline-flex;
}
.editor-dialog label {
  display: grid;
  gap: 5px;
  color: var(--gray-700);
  font-size: 12px;
}
.editor-dialog input,
.editor-dialog textarea,
.editor-dialog select {
  width: 100%;
  padding: 7px 8px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  color: var(--gray-800);
  background: var(--gray-0);
  font: inherit;
}
.editor-dialog textarea {
  resize: vertical;
}
.editor-dialog .primary {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--gray-0);
  background: var(--main-700);
  border-color: var(--main-700);
}
.editor-feedback {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  color: var(--color-error-700);
  background: var(--color-error-50);
  font-size: 12px;
  line-height: 1.5;
}
.schedule-picker :deep(.dp__input) {
  min-height: 36px;
  padding: 7px 36px;
  border-color: var(--gray-200);
  border-radius: 6px;
  color: var(--gray-800);
  font-family: inherit;
  font-size: 13px;
}
.schedule-picker :deep(.dp__input:focus),
.schedule-picker :deep(.dp__input_focus) {
  border-color: var(--main-700);
  box-shadow: 0 0 0 2px var(--main-50);
}
:global(.dp__theme_light) {
  --dp-primary-color: var(--main-700);
  --dp-primary-text-color: var(--gray-0);
  --dp-border-color: var(--gray-200);
  --dp-menu-border-color: var(--gray-200);
  --dp-border-radius: 6px;
  --dp-font-family: inherit;
  --dp-font-size: 13px;
}
.weekday-field {
  display: grid;
  gap: 6px;
  color: var(--gray-700);
  font-size: 12px;
}
.weekday-options {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 5px;
}
.weekday-option {
  display: block !important;
  cursor: pointer;
}
.weekday-option input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
.weekday-option span {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 31px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  color: var(--gray-600);
  background: var(--gray-0);
}
.weekday-option input:checked + span {
  border-color: var(--main-700);
  color: var(--main-900);
  background: var(--main-50);
  font-weight: 600;
}
.weekday-option input:focus-visible + span {
  outline: 2px solid var(--main-200);
  outline-offset: 1px;
}
.custom-schedule {
  margin: 0;
  padding: 9px 10px;
  border-radius: 6px;
  color: var(--gray-600);
  background: var(--gray-50);
  font-size: 12px;
  line-height: 1.55;
}
@media (max-width: 420px) {
  .primary-tabs {
    gap: 14px;
  }
  .secondary-tablist {
    gap: 14px;
  }
  .center-list {
    padding: 10px;
  }
  .scheduled-card,
  .inbox-card {
    padding: 10px;
  }
}
</style>
