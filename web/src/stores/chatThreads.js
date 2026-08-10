import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { threadApi } from '@/apis'
import { handleChatError } from '@/utils/errorHandler'

const PAGE_SIZE = 100

export const useChatThreadsStore = defineStore('chatThreads', () => {
  const threads = ref([])
  const currentThreadId = ref(null)
  const hasMoreThreads = ref(true)
  const isLoadingMoreThreads = ref(false)
  const nextOffset = ref(0)

  const currentThread = computed(() => {
    if (!currentThreadId.value) return null
    return threads.value.find((thread) => thread.id === currentThreadId.value) || null
  })

  const setCurrentThreadId = (threadId) => {
    currentThreadId.value = threadId || null
  }

  const loadThreads = async (agentId = null) => {
    try {
      const fetchedThreads = await threadApi.getThreads(agentId, PAGE_SIZE, 0)
      threads.value = fetchedThreads || []
      const pageThreads = threads.value.filter((thread) => !thread.is_pinned)
      nextOffset.value = pageThreads.length
      hasMoreThreads.value = pageThreads.length >= PAGE_SIZE
      if (
        currentThreadId.value &&
        !threads.value.find((thread) => thread.id === currentThreadId.value)
      ) {
        const current = await threadApi.getThread(currentThreadId.value)
        threads.value = [current, ...threads.value.filter((thread) => thread.id !== current.id)]
      }
      return threads.value
    } catch (error) {
      console.error('Failed to fetch threads:', error)
      handleChatError(error, 'fetch')
      throw error
    }
  }

  const loadMoreThreads = async (agentId = null) => {
    if (isLoadingMoreThreads.value || !hasMoreThreads.value) return

    isLoadingMoreThreads.value = true
    try {
      const fetchedThreads = await threadApi.getThreads(agentId, PAGE_SIZE, nextOffset.value)
      if (fetchedThreads && fetchedThreads.length > 0) {
        // 后端分页会重复返回置顶项，这里只追加列表中尚不存在的线程。
        const existingIds = new Set(threads.value.map((thread) => thread.id))
        const newThreads = fetchedThreads.filter((thread) => !existingIds.has(thread.id))
        threads.value = [...threads.value, ...newThreads]
        const pageThreads = fetchedThreads.filter((thread) => !thread.is_pinned)
        nextOffset.value += pageThreads.length
        hasMoreThreads.value = pageThreads.length >= PAGE_SIZE
      } else {
        hasMoreThreads.value = false
      }
    } catch (error) {
      console.error('Failed to load more chats:', error)
      handleChatError(error, 'fetch')
    } finally {
      isLoadingMoreThreads.value = false
    }
  }

  const locateThread = async (threadId) => {
    if (!threadId) return null
    let thread = threads.value.find((item) => item.id === threadId)
    if (!thread) {
      thread = await threadApi.getThread(threadId)
      threads.value = [thread, ...threads.value.filter((item) => item.id !== thread.id)]
    }
    return thread
  }

  const createThread = async (agentId, title = '新的对话') => {
    if (!agentId) return null

    try {
      const thread = await threadApi.createThread(agentId, title)
      if (thread) {
        threads.value = [thread, ...threads.value.filter((item) => item.id !== thread.id)]
      }
      return thread
    } catch (error) {
      console.error('Failed to create thread:', error)
      handleChatError(error, 'create')
      throw error
    }
  }

  const deleteThread = async (threadId) => {
    if (!threadId) return

    try {
      await threadApi.deleteThread(threadId)
      threads.value = threads.value.filter((thread) => thread.id !== threadId)
      if (currentThreadId.value === threadId) {
        currentThreadId.value = null
      }
    } catch (error) {
      console.error('Failed to delete thread:', error)
      handleChatError(error, 'delete')
      throw error
    }
  }

  const updateThread = async (threadId, title, isPinned) => {
    if (!threadId) return

    if (title) {
      const normalizedTitle = String(title).replace(/\s+/g, ' ').trim().slice(0, 255)
      if (!normalizedTitle) return

      try {
        await threadApi.updateThread(threadId, normalizedTitle, isPinned)
        const thread = threads.value.find((item) => item.id === threadId)
        if (thread) {
          thread.title = normalizedTitle
          if (isPinned !== undefined) {
            thread.is_pinned = isPinned
          }
        }
      } catch (error) {
        console.error('Failed to update thread:', error)
        handleChatError(error, 'update')
        throw error
      }
      return
    }

    if (isPinned !== undefined) {
      try {
        await threadApi.updateThread(threadId, null, isPinned)
        const thread = threads.value.find((item) => item.id === threadId)
        if (thread) {
          thread.is_pinned = isPinned
        }
      } catch (error) {
        console.error('Failed to update thread pin status:', error)
        handleChatError(error, 'update')
        throw error
      }
    }
  }

  return {
    threads,
    currentThreadId,
    currentThread,
    hasMoreThreads,
    isLoadingMoreThreads,
    setCurrentThreadId,
    loadThreads,
    loadMoreThreads,
    locateThread,
    createThread,
    deleteThread,
    updateThread
  }
})
