<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, Pencil, RefreshCw, X } from 'lucide-vue-next'
import { message } from 'ant-design-vue'
import { scheduledJobCandidateApi } from '@/apis/scheduled_job_candidate_api'
import { scheduledJobApi } from '@/apis/scheduled_job_api'
import { authApi } from '@/apis/auth_api'

const items = ref([])
const loading = ref(false)
const error = ref('')
const acting = ref('')
const editorOpen = ref(false)
const rejectOpen = ref(false)
const editing = ref(null)
const personnelOptions = ref([])
const personnelLoading = ref(false)
const preview = ref(null)
const previewLoading = ref(false)
const form = reactive(createEmptyForm())
const rejectReason = ref('')
const rejecting = ref(null)
const canReject = computed(() => Boolean(rejectReason.value.trim()))

function createEmptyForm() {
  return {
    name: '',
    title: '',
    content: '',
    timezone: 'Asia/Shanghai',
    recipientScope: 'unknown',
    recipientNames: [],
    scheduleKind: 'at',
    runAt: '',
    intervalValue: 1,
    intervalUnit: 'hours',
    anchorAt: '',
    cronExpression: ''
  }
}

function resetForm(item) {
  const schedule = item.schedule || {}
  const interval = intervalFields(schedule.interval_seconds)
  Object.assign(form, createEmptyForm(), {
    name: item.name || '',
    title: item.action?.title || '',
    content: item.action?.content || '',
    timezone: item.timezone || 'Asia/Shanghai',
    recipientScope: item.recipient_scope || 'unknown',
    recipientNames: item.recipient_names || [],
    scheduleKind: schedule.kind || 'at',
    runAt: toLocalInput(schedule.run_at),
    intervalValue: interval.value,
    intervalUnit: interval.unit,
    anchorAt: toLocalInput(schedule.anchor_at),
    cronExpression: schedule.cron_expression || ''
  })
  preview.value = null
}

function intervalFields(seconds) {
  if (seconds && seconds % 86400 === 0) return { value: seconds / 86400, unit: 'days' }
  if (seconds && seconds % 3600 === 0) return { value: seconds / 3600, unit: 'hours' }
  return { value: (seconds || 60) / 60, unit: 'minutes' }
}

function toLocalInput(value) {
  return typeof value === 'string' ? value.slice(0, 16) : ''
}

function buildSchedule() {
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

function scheduleSummary(item) {
  const schedule = item.schedule || {}
  if (schedule.kind === 'at') return `单次 · ${formatTime(schedule.run_at)}`
  if (schedule.kind === 'interval') return `每 ${Math.round(schedule.interval_seconds / 60)} 分钟`
  if (schedule.kind === 'cron') return `Cron · ${schedule.cron_expression}`
  return '待补充调度规则'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = (await scheduledJobCandidateApi.list({ status: 'pending_confirmation', limit: 20 }))?.items || []
  } catch (value) {
    error.value = value?.message || '加载候选失败'
  } finally {
    loading.value = false
  }
}

async function loadPersonnel() {
  if (personnelOptions.value.length || personnelLoading.value) return
  personnelLoading.value = true
  try {
    personnelOptions.value = await authApi.getUserAccessOptions()
  } catch (value) {
    message.error(value?.message || '加载人员目录失败')
  } finally {
    personnelLoading.value = false
  }
}

async function openEditor(item) {
  editing.value = item
  resetForm(item)
  editorOpen.value = true
  await loadPersonnel()
}

function onRecipientScopeChange(value) {
  if (value !== 'named') form.recipientNames = []
}

async function previewSchedule() {
  previewLoading.value = true
  preview.value = null
  try {
    preview.value = await scheduledJobApi.preview({ schedule: buildSchedule(), timezone: form.timezone })
  } catch (value) {
    message.error(value?.message || '调度规则不可用')
  } finally {
    previewLoading.value = false
  }
}

async function saveEditor() {
  if (!editing.value) return
  acting.value = editing.value.id
  try {
    const response = await scheduledJobCandidateApi.update(editing.value.id, {
      version: editing.value.version,
      name: form.name,
      action: { type: 'notification', title: form.title, content: form.content },
      schedule: buildSchedule(),
      timezone: form.timezone,
      recipient_scope: form.recipientScope,
      recipient_names: form.recipientScope === 'named' ? form.recipientNames : []
    })
    const candidate = response?.candidate
    items.value = items.value.map((item) => (item.id === candidate?.id ? candidate : item))
    editing.value = candidate || editing.value
    message.success('候选已保存，请确认校验结果后启用')
  } catch (value) {
    message.error(value?.message || '保存候选失败')
  } finally {
    acting.value = ''
  }
}

async function enable(item) {
  acting.value = item.id
  try {
    await scheduledJobCandidateApi.enable(item.id, item.version)
    message.success('候选已启用')
    await load()
  } catch (value) {
    message.error(value?.message || '启用失败')
  } finally {
    acting.value = ''
  }
}

function openReject(item) {
  rejecting.value = item
  rejectReason.value = ''
  rejectOpen.value = true
}

async function reject() {
  if (!rejecting.value || !canReject.value) return
  acting.value = rejecting.value.id
  try {
    await scheduledJobCandidateApi.reject(rejecting.value.id, rejecting.value.version, rejectReason.value.trim())
    message.success('候选已拒绝')
    rejectOpen.value = false
    await load()
  } catch (value) {
    message.error(value?.message || '拒绝失败')
  } finally {
    acting.value = ''
  }
}

onMounted(load)
</script>
<template>
  <section class="candidate-list">
    <div class="candidate-toolbar">
      <span>来文待确认任务</span>
      <a-button size="small" :loading="loading" @click="load"><RefreshCw :size="15" />刷新</a-button>
    </div>
    <a-alert v-if="error" type="error" show-icon :message="error" />
    <a-skeleton v-else-if="loading" active :paragraph="{ rows: 4 }" />
    <a-empty v-else-if="!items.length" description="暂无待确认任务" />
    <article v-for="item in items" v-else :key="item.id" class="candidate-row">
      <div class="candidate-main">
        <div class="candidate-title"><strong>{{ item.name || '未命名任务' }}</strong><a-tag>{{ item.recipient_scope }}</a-tag></div>
        <p>{{ item.action?.title || '通知内容待补充' }}</p>
        <p class="candidate-schedule">{{ scheduleSummary(item) }} · {{ item.timezone || '未设置时区' }}</p>
        <p v-if="item.validation_errors?.length" class="invalid">{{ item.validation_errors.map((entry) => entry.message || entry).join('；') }}</p>
        <p v-if="item.validation_warnings?.length" class="warning">{{ item.validation_warnings.map((entry) => entry.message || entry).join('；') }}</p>
      </div>
      <div class="candidate-actions">
        <a-button type="text" size="small" @click="openEditor(item)"><Pencil :size="15" />编辑</a-button>
        <a-button type="primary" size="small" :disabled="Boolean(item.validation_errors?.length)" :loading="acting === item.id" @click="enable(item)"><Check :size="15" />启用</a-button>
        <a-button danger type="text" size="small" :loading="acting === item.id" @click="openReject(item)"><X :size="15" />拒绝</a-button>
      </div>
    </article>
  </section>

  <a-drawer v-model:open="editorOpen" width="min(720px, 94vw)" title="编辑来文任务候选">
    <a-form layout="vertical">
      <a-form-item label="任务名称" required><a-input v-model:value="form.name" :maxlength="100" /></a-form-item>
      <a-form-item label="通知标题" required><a-input v-model:value="form.title" :maxlength="100" /></a-form-item>
      <a-form-item label="通知正文" required><a-textarea v-model:value="form.content" :rows="4" :maxlength="4000" /></a-form-item>
      <div class="form-grid">
        <a-form-item label="时区" required><a-input v-model:value="form.timezone" /></a-form-item>
        <a-form-item label="调度类型" required>
          <a-select v-model:value="form.scheduleKind">
            <a-select-option value="at">单次</a-select-option><a-select-option value="interval">间隔</a-select-option><a-select-option value="cron">Cron</a-select-option>
          </a-select>
        </a-form-item>
      </div>
      <a-form-item v-if="form.scheduleKind === 'at'" label="触发时间" required><a-input v-model:value="form.runAt" type="datetime-local" /></a-form-item>
      <template v-else-if="form.scheduleKind === 'interval'"><div class="form-grid"><a-form-item label="间隔" required><a-space-compact class="full-width"><a-input-number v-model:value="form.intervalValue" :min="1" :precision="0" class="interval-value" /><a-select v-model:value="form.intervalUnit" class="interval-unit"><a-select-option value="minutes">分钟</a-select-option><a-select-option value="hours">小时</a-select-option><a-select-option value="days">天</a-select-option></a-select></a-space-compact></a-form-item><a-form-item label="首次触发时间" required><a-input v-model:value="form.anchorAt" type="datetime-local" /></a-form-item></div></template>
      <a-form-item v-else label="Cron 表达式" required><a-input v-model:value="form.cronExpression" placeholder="0 9 * * 1-5" /></a-form-item>
      <a-form-item label="接收范围" required><a-radio-group v-model:value="form.recipientScope" @change="onRecipientScopeChange($event.target.value)"><a-radio value="named">指定人员</a-radio><a-radio value="all">全体人员</a-radio><a-radio value="unknown">待补充</a-radio></a-radio-group></a-form-item>
      <a-form-item v-if="form.recipientScope === 'named'" label="接收人" required><a-select v-model:value="form.recipientNames" mode="multiple" :loading="personnelLoading" :options="personnelOptions.map((user) => ({ value: user.username, label: user.department_name ? `${user.username}（${user.department_name}）` : user.username }))" placeholder="从人员目录选择" /></a-form-item>
      <a-alert v-if="preview" type="info" show-icon class="preview-result" :message="`下一次：${formatTime(preview.next_run_at)}`" :description="preview.occurrences?.map((entry) => entry.local).join('；')" />
      <a-alert v-if="editing?.validation_errors?.length" type="warning" show-icon class="validation-tip" :message="editing.validation_errors.map((entry) => entry.message || entry).join('；')" />
      <div class="editor-actions"><a-button :loading="previewLoading" @click="previewSchedule">预览触发时间</a-button><a-button type="primary" :loading="acting === editing?.id" @click="saveEditor">保存</a-button></div>
    </a-form>
  </a-drawer>

  <a-modal v-model:open="rejectOpen" title="拒绝来文任务候选" :confirm-loading="acting === rejecting?.id" @ok="reject">
    <p>拒绝会保留候选和审计记录，请填写原因。</p>
    <a-textarea v-model:value="rejectReason" :rows="4" :maxlength="512" placeholder="请输入拒绝原因" />
    <template #footer><a-button @click="rejectOpen = false">返回</a-button><a-popconfirm title="确认拒绝此候选？" ok-text="确认拒绝" cancel-text="返回" @confirm="reject"><a-button danger type="primary" :disabled="!canReject" :loading="acting === rejecting?.id">拒绝候选</a-button></a-popconfirm></template>
  </a-modal>
</template>
<style lang="less" scoped>
.candidate-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.candidate-toolbar :deep(.ant-btn), .candidate-actions :deep(.ant-btn) { display: inline-flex; align-items: center; gap: 4px; }
.candidate-row { display: flex; gap: 16px; justify-content: space-between; padding: 15px; margin-bottom: 8px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.candidate-main { min-width: 0; }.candidate-title { display: flex; gap: 8px; align-items: center; }.candidate-row p { margin: 6px 0; color: var(--color-text-secondary); font-size: 13px; }.candidate-row .candidate-schedule { color: var(--gray-600); }.candidate-row .invalid { color: var(--color-error-700); }.candidate-row .warning { color: var(--color-warning-900); }.candidate-actions { display: flex; align-items: center; flex: 0 0 auto; }.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }.full-width { width: 100%; }.interval-value { width: calc(100% - 100px); }.interval-unit { width: 100px; }.preview-result, .validation-tip { margin-bottom: 16px; }.editor-actions { display: flex; justify-content: flex-end; gap: 8px; }
@media (max-width: 640px) { .candidate-row { flex-direction: column; }.candidate-actions { justify-content: flex-end; }.form-grid { grid-template-columns: 1fr; gap: 0; } }
</style>
