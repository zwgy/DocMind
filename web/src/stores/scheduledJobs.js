import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import { scheduledJobApi } from '@/apis/scheduled_job_api'

const VIEWS = ['ongoing', 'pending_confirmation', 'paused', 'history']

function createPage() {
  return { items: [], cursor: null, loading: false, loadingMore: false, error: null }
}

export const useScheduledJobsStore = defineStore('scheduledJobs', () => {
  const pages = ref(Object.fromEntries(VIEWS.map((view) => [view, createPage()])))
  const activeView = ref('ongoing')
  const sourceType = ref('personal')

  const currentPage = computed(() => pages.value[activeView.value])

  async function load(view = activeView.value, { reset = false } = {}) {
    if (view === 'pending_confirmation') return
    const page = pages.value[view]
    if (!page || page.loading || (!reset && page.cursor === null && page.items.length)) return
    if (!reset && page.loadingMore) return
    if (reset) page.loading = true
    else page.loadingMore = true
    page.error = null
    try {
      const listMethod = sourceType.value === 'incoming' ? scheduledJobApi.listIncoming : scheduledJobApi.list
      const response = await listMethod({
        view,
        cursor: reset ? undefined : page.cursor,
        limit: 20
      })
      const items = Array.isArray(response?.items) ? response.items : []
      page.items = reset ? items : [...page.items, ...items]
      page.cursor = response?.next_cursor || null
    } catch (error) {
      page.error = error
      throw error
    } finally {
      page.loading = false
      page.loadingMore = false
    }
  }

  async function refresh(view = activeView.value) {
    try {
      await load(view, { reset: true })
    } catch (error) {
      message.error(error?.message || '加载定时任务失败')
    }
  }

  async function changeStatus(job, action, reason) {
    try {
      const changeMethod = job.source_type === 'incoming'
        ? scheduledJobApi.changeIncomingStatus
        : scheduledJobApi.changeStatus
      await changeMethod(job.id, { action, version: job.version, ...(reason ? { reason } : {}) })
      message.success(action === 'pause' ? '任务已暂停' : action === 'resume' ? '任务已恢复' : '任务已取消')
      await Promise.all([refresh(activeView.value), refresh('ongoing'), refresh('paused'), refresh('history')])
    } catch (error) {
      message.error(error?.message || '更新任务状态失败')
      throw error
    }
  }

  async function update(jobId, payload) {
    const response = await scheduledJobApi.update(jobId, payload)
    await Promise.all(VIEWS.map((view) => refresh(view)))
    return response?.job
  }

  async function remove(job) {
    await scheduledJobApi.remove(job)
    message.success('记录已删除')
    await refresh(activeView.value)
  }

  function setSourceType(value) {
    if (!['personal', 'incoming'].includes(value) || sourceType.value === value) return
    sourceType.value = value
    activeView.value = 'ongoing'
    pages.value = Object.fromEntries(VIEWS.map((view) => [view, createPage()]))
    void refresh('ongoing')
  }

  function setActiveView(view) {
    if (!VIEWS.includes(view)) return
    activeView.value = view
    const page = pages.value[view]
    if (!page.items.length && !page.loading) void refresh(view)
  }

  return {
    activeView,
    sourceType,
    currentPage,
    pages,
    load,
    refresh,
    changeStatus,
    update,
    remove,
    setActiveView,
    setSourceType
  }
})
