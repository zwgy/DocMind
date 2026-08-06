<script setup>
import { computed, onMounted, ref } from 'vue'
import { Pause, Pencil, Play, RefreshCw, X } from 'lucide-vue-next'
import { message } from 'ant-design-vue'
import { useScheduledJobsStore } from '@/stores/scheduledJobs'
import { scheduledJobApi } from '@/apis/scheduled_job_api'
import { agentApi } from '@/apis/agent_api'
import PageHeader from '@/components/shared/PageHeader.vue'
import CandidateList from '@/components/scheduled-jobs/CandidateList.vue'
import { useUserStore } from '@/stores/user'

const store = useScheduledJobsStore()
const userStore = useUserStore()
const page = computed(() => store.currentPage)
const tabs = [
  { value: 'ongoing', label: '进行中', empty: '暂无进行中的任务' },
  { value: 'paused', label: '已暂停', empty: '暂无已暂停任务' },
  { value: 'history', label: '历史', empty: '暂无历史任务' }
]
if (userStore.isAdmin) tabs.splice(1, 0, { value: 'pending_confirmation', label: '待确认', empty: '' })
const currentTab = computed(() => tabs.find((item) => item.value === store.activeView) || tabs[0])
const detail = ref(null)
const runs = ref([])
const detailLoading = ref(false)
const editorOpen = ref(false)
const editingJob = ref(null)
const preview = ref(null)
const previewLoading = ref(false)
const saving = ref(false)
const agents = ref([])
const editForm = ref(createEditForm())

function createEditForm() {
  return {
    name: '',
    actionType: 'notification',
    title: '',
    content: '',
    agentSlug: '',
    instruction: '',
    timeoutSeconds: 900,
    timezone: 'Asia/Shanghai',
    scheduleKind: 'at',
    runAt: '',
    intervalValue: 1,
    intervalUnit: 'hours',
    anchorAt: '',
    cronExpression: ''
  }
}

function intervalFields(seconds) {
  if (seconds && seconds % 86400 === 0) return { value: seconds / 86400, unit: 'days' }
  if (seconds && seconds % 3600 === 0) return { value: seconds / 3600, unit: 'hours' }
  return { value: (seconds || 60) / 60, unit: 'minutes' }
}

function toLocalInput(value) {
  return typeof value === 'string' ? value.slice(0, 16) : ''
}

function resetEditForm(job) {
  const interval = intervalFields(job.interval_seconds)
  editForm.value = {
    ...createEditForm(),
    name: job.name || '',
    actionType: job.action_type || job.action_data?.type || 'notification',
    title: job.action_data?.title || '',
    content: job.action_data?.content || '',
    agentSlug: job.action_data?.agent_slug || '',
    instruction: job.action_data?.instruction || '',
    timeoutSeconds: job.action_data?.timeout_seconds || 900,
    timezone: job.timezone || 'Asia/Shanghai',
    scheduleKind: job.schedule_kind || 'at',
    runAt: toLocalInput(job.run_at),
    intervalValue: interval.value,
    intervalUnit: interval.unit,
    anchorAt: toLocalInput(job.anchor_at),
    cronExpression: job.cron_expression || ''
  }
  preview.value = null
}

function buildSchedule() {
  const form = editForm.value
  if (form.scheduleKind === 'at') return { kind: 'at', run_at: form.runAt }
  if (form.scheduleKind === 'interval') {
    const secondsByUnit = { minutes: 60, hours: 3600, days: 86400 }
    return {
      kind: 'interval',
      interval_seconds: Number(form.intervalValue) * secondsByUnit[form.intervalUnit],
      anchor_at: form.anchorAt
    }
  }
  return { kind: 'cron', cron_expression: form.cronExpression }
}

function formatTime(value) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function scheduleSummary(job) {
  if (job.schedule_kind === 'at') return `单次 · ${formatTime(job.run_at)}`
  if (job.schedule_kind === 'interval') return `每 ${Math.round(job.interval_seconds / 60)} 分钟`
  return `Cron · ${job.cron_expression}`
}

function statusText(status) {
  return { active: '进行中', paused: '已暂停', completed: '已完成', cancelled: '已取消' }[status] || status
}

function actionSummary(job) {
  return job.action_type === 'agent' ? `Agent：${job.action_data?.agent_slug || '-'}` : '站内通知'
}

function runStatusText(status) {
  return { queued: '排队中', running: '执行中', succeeded: '成功', failed: '失败', cancelled: '已取消', partial: '部分完成', skipped: '已跳过' }[status] || status
}

async function handleAction(job, action) {
  await store.changeStatus(job, action)
}

async function openDetail(job) {
  detail.value = job
  detailLoading.value = true
  try { runs.value = (await scheduledJobApi.runs(job.id, { limit: 20 }))?.items || [] } finally { detailLoading.value = false }
}

function openEditor(job) {
  editingJob.value = job
  resetEditForm(job)
  editorOpen.value = true
}

async function previewSchedule() {
  previewLoading.value = true
  preview.value = null
  try {
    preview.value = await scheduledJobApi.preview({ schedule: buildSchedule(), timezone: editForm.value.timezone })
  } catch (error) {
    message.error(error?.message || '调度规则不可用')
  } finally {
    previewLoading.value = false
  }
}

async function saveEditor() {
  if (!editingJob.value) return
  saving.value = true
  try {
    const form = editForm.value
    await store.update(editingJob.value.id, {
      version: editingJob.value.version,
      name: form.name,
      action: form.actionType === 'agent'
        ? { type: 'agent', agent_slug: form.agentSlug, instruction: form.instruction, timeout_seconds: Number(form.timeoutSeconds) }
        : { type: 'notification', title: form.title, content: form.content },
      schedule: buildSchedule(),
      timezone: form.timezone
    })
    editorOpen.value = false
    message.success('任务已更新')
  } catch (error) {
    message.error(error?.message || '更新任务失败')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  void store.refresh()
  try {
    const response = await agentApi.getAgents()
    agents.value = (response?.agents || []).filter((agent) => !agent.is_subagent)
  } catch (error) {
    message.error(error?.message || '加载 Agent 列表失败')
  }
})
</script>

<template>
  <div class="scheduled-jobs-view">
    <PageHeader title="定时任务" :loading="page.loading" :show-border="true">
      <template #actions>
        <a-button @click="store.refresh()"><template #icon><RefreshCw :size="15" /></template>刷新</a-button>
      </template>
    </PageHeader>
    <main class="scheduled-jobs-content">
      <p class="page-intro">集中查看和管理个人及来文产生的定时通知。</p>
      <a-tabs :active-key="store.activeView" @change="store.setActiveView">
        <a-tab-pane v-for="tab in tabs" :key="tab.value" :tab="tab.label" />
      </a-tabs>
      <CandidateList v-if="store.activeView === 'pending_confirmation'" />
      <a-alert v-if="page.error" class="load-error" type="error" show-icon :message="page.error.message || '加载任务失败'">
        <template #action><a-button size="small" @click="store.refresh()">重试</a-button></template>
      </a-alert>
      <a-skeleton v-if="page.loading && !page.items.length" active :paragraph="{ rows: 6 }" />
      <a-empty v-else-if="!page.items.length" :description="currentTab.empty" />
      <section v-else class="job-list" aria-label="定时任务列表">
        <article v-for="job in page.items" :key="job.id" class="job-row">
          <div class="job-main">
            <div class="job-title"><strong>{{ job.name }}</strong><a-tag :class="`status-${job.status}`">{{ statusText(job.status) }}</a-tag></div>
            <div class="job-meta"><span>{{ job.source_type === 'incoming' ? '来文任务' : '个人任务' }}</span><span>{{ actionSummary(job) }}</span><span>{{ scheduleSummary(job) }}</span><span>{{ job.timezone }}</span></div>
            <div class="job-next">下一次：{{ formatTime(job.next_run_at) }}</div>
          </div>
          <div class="job-actions">
            <a-button type="link" @click="openDetail(job)">详情</a-button>
            <a-button v-if="['active', 'paused'].includes(job.status)" type="text" @click="openEditor(job)"><Pencil :size="16" />编辑</a-button>
            <a-button v-if="job.status === 'active' && job.schedule_kind !== 'at'" type="text" :loading="page.loading" @click="handleAction(job, 'pause')"><Pause :size="16" />暂停</a-button>
            <a-button v-if="job.status === 'paused'" type="text" :loading="page.loading" @click="handleAction(job, 'resume')"><Play :size="16" />恢复</a-button>
            <a-popconfirm v-if="['active', 'paused'].includes(job.status)" title="确认取消此任务？" ok-text="取消任务" cancel-text="返回" @confirm="handleAction(job, 'cancel')">
              <a-button type="text" danger :loading="page.loading"><X :size="16" />取消</a-button>
            </a-popconfirm>
          </div>
        </article>
      </section>
      <div v-if="page.cursor" class="load-more"><a-button :loading="page.loadingMore" @click="store.load()">加载更多</a-button></div>
    </main>
    <a-drawer v-model:open="detail" width="min(680px, 92vw)" :title="detail?.name || '任务详情'">
      <a-descriptions v-if="detail" size="small" bordered :column="1">
        <a-descriptions-item label="状态">{{ statusText(detail.status) }}</a-descriptions-item>
        <a-descriptions-item label="调度">{{ scheduleSummary(detail) }}</a-descriptions-item>
        <a-descriptions-item label="时区">{{ detail.timezone }}</a-descriptions-item>
        <a-descriptions-item label="动作">{{ actionSummary(detail) }}</a-descriptions-item>
        <a-descriptions-item label="下一次">{{ formatTime(detail.next_run_at) }}</a-descriptions-item>
      </a-descriptions>
      <h3 class="run-heading">运行历史</h3>
      <a-skeleton v-if="detailLoading" active :paragraph="{ rows: 3 }" />
      <a-empty v-else-if="!runs.length" description="暂无运行历史" />
      <a-timeline v-else><a-timeline-item v-for="run in runs" :key="run.id"><strong>{{ runStatusText(run.status) }}</strong><div>{{ formatTime(run.scheduled_for) }} · 第 {{ run.attempt_count }} 次尝试</div><small v-if="run.agent_run_id">Agent Run：{{ run.agent_run_id }}</small><small v-if="run.error_message">{{ run.error_message }}</small></a-timeline-item></a-timeline>
    </a-drawer>
    <a-drawer v-model:open="editorOpen" width="min(680px, 92vw)" title="编辑个人定时任务">
      <a-form layout="vertical">
        <a-form-item label="任务名称" required><a-input v-model:value="editForm.name" :maxlength="100" /></a-form-item>
        <a-form-item label="动作类型" required><a-radio-group v-model:value="editForm.actionType"><a-radio value="notification">站内通知</a-radio><a-radio value="agent">执行 Agent</a-radio></a-radio-group></a-form-item>
        <template v-if="editForm.actionType === 'notification'"><a-form-item label="通知标题" required><a-input v-model:value="editForm.title" :maxlength="100" /></a-form-item><a-form-item label="通知正文" required><a-textarea v-model:value="editForm.content" :rows="4" :maxlength="4000" /></a-form-item></template>
        <template v-else><a-form-item label="目标 Agent" required><a-select v-model:value="editForm.agentSlug" placeholder="选择可执行 Agent"><a-select-option v-for="agent in agents" :key="agent.slug" :value="agent.slug">{{ agent.name }}</a-select-option></a-select></a-form-item><a-form-item label="执行指令" required><a-textarea v-model:value="editForm.instruction" :rows="5" :maxlength="8000" /></a-form-item><a-form-item label="超时（秒）" required><a-input-number v-model:value="editForm.timeoutSeconds" :min="60" :max="3600" :precision="0" /></a-form-item></template>
        <div class="form-grid">
          <a-form-item label="时区" required><a-input v-model:value="editForm.timezone" /></a-form-item>
          <a-form-item label="调度类型" required><a-select v-model:value="editForm.scheduleKind"><a-select-option value="at">单次</a-select-option><a-select-option value="interval">间隔</a-select-option><a-select-option value="cron">Cron</a-select-option></a-select></a-form-item>
        </div>
        <a-form-item v-if="editForm.scheduleKind === 'at'" label="触发时间" required><a-input v-model:value="editForm.runAt" type="datetime-local" /></a-form-item>
        <template v-else-if="editForm.scheduleKind === 'interval'"><div class="form-grid"><a-form-item label="间隔" required><a-space-compact class="full-width"><a-input-number v-model:value="editForm.intervalValue" :min="1" :precision="0" class="interval-value" /><a-select v-model:value="editForm.intervalUnit" class="interval-unit"><a-select-option value="minutes">分钟</a-select-option><a-select-option value="hours">小时</a-select-option><a-select-option value="days">天</a-select-option></a-select></a-space-compact></a-form-item><a-form-item label="首次触发时间" required><a-input v-model:value="editForm.anchorAt" type="datetime-local" /></a-form-item></div></template>
        <a-form-item v-else label="Cron 表达式" required><a-input v-model:value="editForm.cronExpression" placeholder="0 9 * * 1-5" /></a-form-item>
        <a-alert v-if="preview" type="info" show-icon class="preview-result" :message="`下一次：${formatTime(preview.next_run_at)}`" :description="preview.occurrences?.map((entry) => entry.local).join('；')" />
        <p class="edit-hint">任务一旦已产生运行记录将不能编辑，接收人始终为当前用户。</p>
        <div class="editor-actions"><a-button :loading="previewLoading" @click="previewSchedule">预览触发时间</a-button><a-button type="primary" :loading="saving" @click="saveEditor">保存</a-button></div>
      </a-form>
    </a-drawer>
  </div>
</template>

<style lang="less" scoped>
.scheduled-jobs-content { max-width: 1080px; padding: 20px 24px 36px; margin: 0 auto; }
.page-intro { margin: 0 0 18px; color: var(--color-text-secondary); }
.load-error { margin: 0 0 16px; }
.job-list { border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.job-row { display: flex; gap: 16px; justify-content: space-between; padding: 16px; border-bottom: 1px solid var(--gray-100); }
.job-row:last-child { border-bottom: 0; }
.job-main { min-width: 0; }
.job-title { display: flex; gap: 8px; align-items: center; color: var(--color-text); }
.job-meta, .job-next { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 7px; color: var(--color-text-secondary); font-size: 13px; }
.job-meta span + span::before { content: '·'; margin-right: 8px; color: var(--gray-400); }
.job-actions { display: flex; align-items: center; flex: 0 0 auto; }
.job-actions :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 5px; }
.status-active { color: var(--color-info-700); background: var(--color-info-50); border-color: transparent; }
.status-paused { color: var(--color-warning-900); background: var(--color-warning-50); border-color: transparent; }
.status-completed { color: var(--color-success-700); background: var(--color-success-50); border-color: transparent; }
.status-cancelled { color: var(--gray-600); background: var(--gray-100); border-color: transparent; }
.load-more { display: flex; justify-content: center; padding: 20px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.full-width { width: 100%; }
.interval-value { width: calc(100% - 100px); }
.interval-unit { width: 100px; }
.preview-result { margin-bottom: 16px; }
.edit-hint { margin: 0 0 16px; color: var(--color-text-secondary); font-size: 13px; }
.editor-actions { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 640px) { .scheduled-jobs-content { padding: 16px; } .job-row { align-items: flex-start; flex-direction: column; } .job-actions { width: 100%; } .form-grid { grid-template-columns: 1fr; gap: 0; } }
</style>
