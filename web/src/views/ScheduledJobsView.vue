<script setup>
import { computed, onMounted } from 'vue'
import { Pause, Play, RefreshCw, X } from 'lucide-vue-next'
import { useScheduledJobsStore } from '@/stores/scheduledJobs'
import PageHeader from '@/components/PageHeader.vue'

const store = useScheduledJobsStore()
const page = computed(() => store.currentPage)
const tabs = [
  { value: 'ongoing', label: '进行中', empty: '暂无进行中的任务' },
  { value: 'paused', label: '已暂停', empty: '暂无已暂停任务' },
  { value: 'history', label: '历史', empty: '暂无历史任务' }
]
const currentTab = computed(() => tabs.find((item) => item.value === store.activeView) || tabs[0])

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

async function handleAction(job, action) {
  await store.changeStatus(job, action)
}

onMounted(() => void store.refresh())
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
      <a-alert v-if="page.error" class="load-error" type="error" show-icon :message="page.error.message || '加载任务失败'">
        <template #action><a-button size="small" @click="store.refresh()">重试</a-button></template>
      </a-alert>
      <a-skeleton v-if="page.loading && !page.items.length" active :paragraph="{ rows: 6 }" />
      <a-empty v-else-if="!page.items.length" :description="currentTab.empty" />
      <section v-else class="job-list" aria-label="定时任务列表">
        <article v-for="job in page.items" :key="job.id" class="job-row">
          <div class="job-main">
            <div class="job-title"><strong>{{ job.name }}</strong><a-tag :class="`status-${job.status}`">{{ statusText(job.status) }}</a-tag></div>
            <div class="job-meta"><span>{{ job.source_type === 'incoming' ? '来文任务' : '个人任务' }}</span><span>{{ scheduleSummary(job) }}</span><span>{{ job.timezone }}</span></div>
            <div class="job-next">下一次：{{ formatTime(job.next_run_at) }}</div>
          </div>
          <div class="job-actions">
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
@media (max-width: 640px) { .scheduled-jobs-content { padding: 16px; } .job-row { align-items: flex-start; flex-direction: column; } .job-actions { width: 100%; } }
</style>
