import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { inboxApi } from '@/apis/inbox_api'

function page() {
  return { items: [], cursor: null, loading: false, moreLoading: false, error: null }
}

export const useInboxStore = defineStore('inbox', () => {
  const open = ref(false)
  const category = ref('notification')
  const pages = ref({ notification: page(), task: page() })
  const counts = ref({ notification_unread_count: 0, task_unread_count: 0, total_unread_count: 0 })
  let timer = null
  const currentPage = computed(() => pages.value[category.value])
  const hasUnread = computed(() => counts.value.total_unread_count > 0)

  async function refreshCounts() {
    const result = await inboxApi.unreadCount()
    counts.value = { ...counts.value, ...result }
  }
  async function load(target = category.value, { reset = false } = {}) {
    const targetPage = pages.value[target]
    if (
      targetPage.loading ||
      targetPage.moreLoading ||
      (!reset && targetPage.cursor === null && targetPage.items.length)
    )
      return
    targetPage[reset ? 'loading' : 'moreLoading'] = true
    targetPage.error = null
    try {
      const result = await inboxApi.list(target, {
        cursor: reset ? undefined : targetPage.cursor,
        limit: 20
      })
      const items = Array.isArray(result?.items) ? result.items : []
      targetPage.items = reset ? items : [...targetPage.items, ...items]
      targetPage.cursor = result?.next_cursor || null
    } catch (error) {
      targetPage.error = error
      throw error
    } finally {
      targetPage.loading = false
      targetPage.moreLoading = false
    }
  }
  async function refresh(target = category.value) {
    await Promise.all([load(target, { reset: true }), refreshCounts()])
  }
  async function markRead(target, id) {
    await inboxApi.markRead(target, id)
    await refresh(target)
  }
  async function markRunRead(jobId, runId) {
    await inboxApi.markRunRead(jobId, runId)
    await refresh('task')
  }
  async function markAllRead(target = category.value) {
    await inboxApi.markAllRead(target)
    await refresh(target)
  }
  async function remove(target, id) {
    await inboxApi.remove(target, id)
    await refresh(target)
  }
  async function clearRead(target = category.value) {
    await inboxApi.clearRead(target)
    await refresh(target)
  }
  function setCategory(target) {
    if (!pages.value[target]) return
    category.value = target
    if (!pages.value[target].items.length) void refresh(target)
  }
  function setOpen(value) {
    open.value = value
    if (value) {
      void refresh(category.value)
      startPolling()
    } else stopPolling()
  }
  function startPolling() {
    if (timer) return
    timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refreshCounts()
    }, 30000)
  }
  function stopPolling() {
    if (timer) window.clearInterval(timer)
    timer = null
  }
  return {
    open,
    category,
    currentPage,
    counts,
    hasUnread,
    load,
    refresh,
    refreshCounts,
    markRead,
    markRunRead,
    markAllRead,
    remove,
    clearRead,
    setCategory,
    setOpen,
    startPolling,
    stopPolling
  }
})
