<script setup lang="ts">
import { Paperclip, SendHorizontal } from 'lucide-vue-next'
import { ref } from 'vue'
import type { ModelOption } from '@/types'

withDefaults(
  defineProps<{
    disabled?: boolean
    askPage?: boolean
    askFile?: boolean
    models?: ModelOption[]
    selectedModelSpec?: string
  }>(),
  {
    disabled: false,
    askPage: true,
    askFile: true,
    models: () => [],
    selectedModelSpec: ''
  }
)

const emit = defineEmits<{
  submit: [payload: { text: string; files: File[] }]
  'update:askPage': [value: boolean]
  'update:askFile': [value: boolean]
  'update:selectedModelSpec': [value: string]
}>()

const text = ref('')
const files = ref<File[]>([])

function submit() {
  const content = text.value.trim()
  if (!content) return
  emit('submit', { text: content, files: files.value })
  text.value = ''
  files.value = []
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  files.value = Array.from(input.files || [])
  // 同一个文件二次选择也要触发 change，否则用户替换附件时会觉得按钮失灵。
  input.value = ''
}

function emitAskPage(event: Event) {
  emit('update:askPage', (event.target as HTMLInputElement).checked)
}

function emitAskFile(event: Event) {
  emit('update:askFile', (event.target as HTMLInputElement).checked)
}

function emitModel(event: Event) {
  emit('update:selectedModelSpec', (event.target as HTMLSelectElement).value)
}
</script>

<template>
  <form class="chat-input" @submit.prevent="submit">
    <div class="input-toolbar">
      <label>
        <input type="checkbox" :checked="askPage" @change="emitAskPage" />
        问网页
      </label>
      <label>
        <input type="checkbox" :checked="askFile" @change="emitAskFile" />
        问文件
      </label>
      <select
        :value="selectedModelSpec"
        :disabled="!models.length"
        title="模型"
        @change="emitModel"
      >
        <option value="">默认模型</option>
        <option v-for="model in models" :key="model.value" :value="model.value">
          {{ model.label }}
        </option>
      </select>
      <label class="attach-button" title="添加附件">
        <Paperclip :size="16" />
        <input type="file" multiple @change="onFileChange" />
      </label>
    </div>

    <div v-if="files.length" class="attached-files">
      <span v-for="file in files" :key="file.name">{{ file.name }}</span>
    </div>

    <div class="input-row">
      <textarea
        v-model="text"
        rows="2"
        placeholder="输入问题..."
        :disabled="disabled"
        @keydown.enter.exact.prevent="submit"
      />
      <button type="submit" :disabled="disabled || !text.trim()" title="发送">
        <SendHorizontal :size="18" />
      </button>
    </div>
  </form>
</template>
