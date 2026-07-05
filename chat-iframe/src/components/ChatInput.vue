<script setup lang="ts">
import { Image, Paperclip, SendHorizontal, Square, X } from 'lucide-vue-next'
import { ref } from 'vue'
import type { ModelOption } from '@/types'

withDefaults(
  defineProps<{
    disabled?: boolean
    streaming?: boolean
    askPage?: boolean
    askFile?: boolean
    models?: ModelOption[]
    selectedModelSpec?: string
  }>(),
  {
    disabled: false,
    streaming: false,
    askPage: true,
    askFile: true,
    models: () => [],
    selectedModelSpec: ''
  }
)

const emit = defineEmits<{
  submit: [payload: { text: string; files: File[]; imageFile?: File | null }]
  stop: []
  'update:askPage': [value: boolean]
  'update:askFile': [value: boolean]
  'update:selectedModelSpec': [value: string]
}>()

const text = ref('')
const files = ref<File[]>([])
const imageFile = ref<File | null>(null)

function submit() {
  const content = text.value.trim()
  if (!content) return
  emit('submit', { text: content, files: files.value, imageFile: imageFile.value })
  text.value = ''
  files.value = []
  imageFile.value = null
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  files.value = [...files.value, ...Array.from(input.files || [])]
  // 同一个文件二次选择也要触发 change，否则用户替换附件时会觉得按钮失灵。
  input.value = ''
}

function onImageChange(event: Event) {
  const input = event.target as HTMLInputElement
  imageFile.value = Array.from(input.files || [])[0] || null
  input.value = ''
}

function removeFile(index: number) {
  files.value = files.value.filter((_, itemIndex) => itemIndex !== index)
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
      <label class="attach-button" title="添加图片">
        <Image :size="16" />
        <input type="file" accept="image/*" @change="onImageChange" />
      </label>
    </div>

    <div v-if="files.length || imageFile" class="attached-files">
      <span v-if="imageFile">
        图片：{{ imageFile.name }}
        <button type="button" title="移除图片" @click="imageFile = null"><X :size="12" /></button>
      </span>
      <span v-for="(file, index) in files" :key="`${file.name}-${index}`">
        {{ file.name }}
        <button type="button" title="移除附件" @click="removeFile(index)"><X :size="12" /></button>
      </span>
    </div>

    <div class="input-row">
      <textarea
        v-model="text"
        rows="2"
        placeholder="输入问题..."
        :disabled="disabled"
        @keydown.enter.exact.prevent="submit"
      />
      <button v-if="streaming" type="button" title="停止" @click="$emit('stop')">
        <Square :size="16" />
      </button>
      <button v-else type="submit" :disabled="disabled || !text.trim()" title="发送">
        <SendHorizontal :size="18" />
      </button>
    </div>
  </form>
</template>
