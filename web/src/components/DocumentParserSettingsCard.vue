<template>
  <section class="document-parser-settings">
    <div class="settings-section-heading">
      <div>
        <h2>文档解析配置</h2>
        <p>知识库上传和来文处理未单独选择引擎时，使用这里的系统默认值。</p>
      </div>
      <a-button :loading="loading" @click="loadConfig">
        <template #icon><RefreshCw :size="16" /></template>
        刷新
      </a-button>
    </div>

    <a-spin :spinning="loading">
      <a-form layout="vertical" class="document-parser-form">
        <a-form-item label="默认 OCR 引擎">
          <a-select v-model:value="form.engine" :options="ocrEngineOptions" />
        </a-form-item>
        <a-form-item label="引擎高级参数（JSON）">
          <a-textarea
            v-model:value="form.engineConfigText"
            :auto-size="{ minRows: 7, maxRows: 16 }"
            spellcheck="false"
          />
        </a-form-item>
        <a-alert
          v-if="errorMessage"
          type="error"
          show-icon
          :message="errorMessage"
          class="parser-settings-alert"
        />
        <div class="settings-actions">
          <a-button type="primary" :loading="saving" @click="saveConfig">保存配置</a-button>
        </div>
      </a-form>
    </a-spin>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { RefreshCw } from 'lucide-vue-next'
import { configApi } from '@/apis/system_api'

const loading = ref(false)
const saving = ref(false)
const errorMessage = ref('')
const form = reactive({ engine: 'disable', engineConfigText: '{}' })

const ocrEngineOptions = [
  { value: 'disable', label: '不启用 OCR' },
  { value: 'rapid_ocr', label: 'RapidOCR (ONNX)' },
  { value: 'mineru_ocr', label: 'MinerU OCR' },
  { value: 'mineru_official', label: 'MinerU Official API' },
  { value: 'pp_structure_v3_ocr', label: 'PP-Structure-V3' },
  { value: 'deepseek_ocr', label: 'DeepSeek OCR' }
]

async function loadConfig() {
  loading.value = true
  errorMessage.value = ''
  try {
    const config = await configApi.getConfig()
    form.engine = config.document_parser_ocr_engine || 'disable'
    form.engineConfigText = JSON.stringify(config.document_parser_ocr_engine_config || {}, null, 2)
  } catch (error) {
    errorMessage.value = error.message || '加载文档解析配置失败'
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  let engineConfig
  try {
    engineConfig = JSON.parse(form.engineConfigText || '{}')
    if (!engineConfig || Array.isArray(engineConfig) || typeof engineConfig !== 'object') {
      throw new Error('高级参数必须是 JSON 对象')
    }
  } catch (error) {
    errorMessage.value = error.message || '高级参数不是有效 JSON'
    return
  }

  saving.value = true
  errorMessage.value = ''
  try {
    await configApi.updateConfigBatch({
      document_parser_ocr_engine: form.engine,
      document_parser_ocr_engine_config: engineConfig
    })
    form.engineConfigText = JSON.stringify(engineConfig, null, 2)
    message.success('文档解析配置已保存')
  } catch (error) {
    errorMessage.value = error.message || '保存文档解析配置失败'
  } finally {
    saving.value = false
  }
}

onMounted(loadConfig)
</script>

<style scoped lang="less">
.document-parser-settings {
  max-width: 720px;
}

.settings-section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.settings-section-heading h2 {
  margin: 0;
  color: var(--color-text);
  font-size: 18px;
  font-weight: 600;
}

.settings-section-heading p {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.document-parser-form {
  max-width: 620px;
}

.parser-settings-alert {
  margin-bottom: 16px;
}

.settings-actions {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 560px) {
  .settings-section-heading {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
