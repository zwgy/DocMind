<template>
  <div class="incoming-documents-view">
    <PageHeader title="来文管理" :loading="loading" :show-border="true">
      <template #actions>
        <a-button @click="loadDocuments">
          <template #icon><RefreshCw :size="14" /></template>
          刷新
        </a-button>
      </template>
    </PageHeader>

    <div class="incoming-content">
      <div class="toolbar">
        <a-input-search
          v-model:value="filters.keyword"
          class="keyword-input"
          placeholder="搜索文件名或外部单号"
          allow-clear
          @search="reloadFirstPage"
        />
        <a-select
          v-model:value="filters.status"
          class="filter-select"
          placeholder="处理状态"
          allow-clear
          @change="reloadFirstPage"
        >
          <a-select-option v-for="item in processingStatusOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-select-option>
        </a-select>
        <a-select
          v-model:value="filters.knowledge_import_status"
          class="filter-select"
          placeholder="知识库状态"
          allow-clear
          @change="reloadFirstPage"
        >
          <a-select-option v-for="item in importStatusOptions" :key="item.value" :value="item.value">
            {{ item.label }}
          </a-select-option>
        </a-select>
      </div>

      <a-table
        row-key="incomingId"
        :columns="columns"
        :data-source="documents"
        :loading="loading"
        :pagination="pagination"
        :scroll="{ x: 1160 }"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'filename'">
            <div class="file-cell">
              <FileText :size="16" />
              <span :title="record.filename">{{ record.filename }}</span>
            </div>
          </template>
          <template v-else-if="column.key === 'classification'">
            <a-tag>{{ record.classification || '未分类' }}</a-tag>
          </template>
          <template v-else-if="column.key === 'status'">
            <a-tag :color="processingStatusMeta(record.status).color">
              {{ processingStatusMeta(record.status).label }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'knowledgeImportStatus'">
            <a-tag :color="importStatusMeta(record.knowledgeImportStatus).color">
              {{ importStatusMeta(record.knowledgeImportStatus).label }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'linkedKbId'">
            {{ databaseName(record.linkedKbId) || '-' }}
          </template>
          <template v-else-if="column.key === 'createdAt'">
            {{ formatDate(record.createdAt) }}
          </template>
          <template v-else-if="column.key === 'actions'">
            <div class="row-actions">
              <a-button type="link" size="small" @click="openDetail(record)">查看</a-button>
              <a-button
                v-if="canRetry(record)"
                type="link"
                size="small"
                :loading="retryingId === record.incomingId"
                @click="retryProcessing(record)"
              >
                重试处理
              </a-button>
              <a-button
                type="link"
                size="small"
                :disabled="!canImport(record)"
                @click="openImport(record)"
              >
                存入知识库
              </a-button>
            </div>
          </template>
        </template>
      </a-table>
    </div>

    <a-drawer
      v-model:open="detailOpen"
      width="min(920px, 92vw)"
      :title="detail?.filename || '来文详情'"
      :destroy-on-close="true"
    >
      <a-spin :spinning="detailLoading">
        <div v-if="detail" class="detail-body">
          <section class="detail-section">
            <h2>基本信息</h2>
            <a-descriptions size="small" bordered :column="2">
              <a-descriptions-item label="来源系统">{{ detail.sourceSystem || '-' }}</a-descriptions-item>
              <a-descriptions-item label="功能 ID">{{ detail.sourceFunctionId || '-' }}</a-descriptions-item>
              <a-descriptions-item label="外部单号">{{ detail.sourceDocumentId || '-' }}</a-descriptions-item>
              <a-descriptions-item label="文件大小">{{ formatSize(detail.fileSize) }}</a-descriptions-item>
              <a-descriptions-item label="上传时间">{{ formatDate(detail.createdAt) }}</a-descriptions-item>
              <a-descriptions-item label="内容 Hash" :span="2">{{ detail.contentHash || '-' }}</a-descriptions-item>
              <a-descriptions-item label="原文存储" :span="2">
                <span class="path-text">{{ detail.originalFileUrl || '-' }}</span>
              </a-descriptions-item>
              <a-descriptions-item label="Markdown 存储" :span="2">
                <span class="path-text">{{ detail.markdownFileUrl || '-' }}</span>
              </a-descriptions-item>
            </a-descriptions>
          </section>

          <section class="detail-section">
            <h2>处理结果</h2>
            <div class="tag-row">
              <a-tag :color="processingStatusMeta(detail.status).color">
                {{ processingStatusMeta(detail.status).label }}
              </a-tag>
              <a-tag>{{ detail.classification || '未分类' }}</a-tag>
              <span v-if="detail.classificationConfidence !== null" class="muted">
                置信度 {{ percent(detail.classificationConfidence) }}
              </span>
            </div>
            <a-alert
              v-if="detail.processingError"
              type="error"
              show-icon
              :message="detail.processingError"
            />
            <a-typography-paragraph class="summary-text">
              {{ detail.summary || '暂无摘要' }}
            </a-typography-paragraph>
          </section>

          <section class="detail-section">
            <h2>结构化结果</h2>
            <pre class="json-box">{{ stringifyJson(detail.structuredResult) }}</pre>
          </section>

          <section class="detail-section">
            <h2>原文预览</h2>
            <pre class="markdown-box">{{ detail.markdownPreview || '暂无可预览内容' }}</pre>
          </section>

          <section class="detail-section">
            <h2>知识库信息</h2>
            <div class="knowledge-row">
              <a-tag :color="importStatusMeta(detail.knowledgeImportStatus).color">
                {{ importStatusMeta(detail.knowledgeImportStatus).label }}
              </a-tag>
              <span>{{ databaseName(detail.linkedKbId) || detail.linkedKbId || '未选择知识库' }}</span>
              <span v-if="detail.linkedFileId" class="muted">文件 ID：{{ detail.linkedFileId }}</span>
            </div>
            <a-alert
              v-if="detail.knowledgeImportError"
              type="error"
              show-icon
              :message="detail.knowledgeImportError"
            />
            <a-button
              v-if="canImport(detail)"
              type="primary"
              class="detail-action-button"
              @click="openImport(detail)"
            >
              存入知识库
            </a-button>
            <a-button
              v-if="canOpenKnowledgePreview(detail)"
              class="detail-action-button"
              @click="openKnowledgePreview(detail)"
            >
              预览文件
            </a-button>
            <a-button
              v-if="canRetry(detail)"
              class="detail-action-button"
              :loading="retryingId === detail.incomingId"
              @click="retryProcessing(detail)"
            >
              重试处理
            </a-button>
          </section>
        </div>
      </a-spin>
    </a-drawer>

    <a-modal
      v-model:open="importOpen"
      title="存入知识库"
      width="720px"
      :confirm-loading="importing"
      :destroy-on-close="true"
      @ok="submitImport"
    >
      <a-form layout="vertical">
        <a-form-item label="目标知识库" required>
          <a-select
            v-model:value="importForm.kbId"
            placeholder="选择知识库"
            show-search
            option-filter-prop="label"
            :options="databaseOptions"
          />
        </a-form-item>
        <a-form-item label="目标文件夹">
          <a-tree-select
            v-model:value="importForm.parentId"
            :tree-data="folderTreeData"
            :loading="folderLoading"
            allow-clear
            show-search
            tree-default-expand-all
            tree-node-filter-prop="title"
            placeholder="默认根目录"
          />
        </a-form-item>
        <a-form-item label="OCR 引擎">
          <a-select v-model:value="importForm.ocrEngine" :options="ocrOptions" />
        </a-form-item>
        <ChunkParamsConfig
          :temp-chunk-params="importForm.chunkParams"
          :allow-preset-follow-default="true"
          :database-preset-id="selectedDatabasePresetId"
        />
      </a-form>
    </a-modal>

    <FileDetailModal
      v-model:open="knowledgePreviewOpen"
      :kb-id="knowledgePreview.kbId"
      :file-id="knowledgePreview.fileId"
    />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { message } from 'ant-design-vue'
import { FileText, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import ChunkParamsConfig from '@/components/ChunkParamsConfig.vue'
import FileDetailModal from '@/components/FileDetailModal.vue'
import { incomingDocumentApi } from '@/apis/incoming_document_api'
import { databaseApi, documentApi } from '@/apis/knowledge_api'
import { buildChunkParamsPayload } from '@/utils/chunk_presets'

const documents = ref([])
const total = ref(0)
const loading = ref(false)
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const databases = ref([])
const importOpen = ref(false)
const importing = ref(false)
const importTarget = ref(null)
const retryingId = ref('')
const folderLoading = ref(false)
const folderTreeData = ref([])
const knowledgePreviewOpen = ref(false)
const knowledgePreview = reactive({ kbId: '', fileId: '' })

const filters = reactive({
  keyword: '',
  status: undefined,
  knowledge_import_status: undefined
})

const pager = reactive({ page: 1, pageSize: 20 })

const importForm = reactive({
  kbId: undefined,
  parentId: null,
  ocrEngine: 'disable',
  chunkParams: {
    chunk_preset_id: '',
    chunk_parser_config: {}
  }
})

const columns = [
  { title: '文件名', key: 'filename', dataIndex: 'filename', width: 260, fixed: 'left' },
  { title: '来源系统', key: 'sourceSystem', dataIndex: 'sourceSystem', width: 120 },
  { title: '功能 ID', key: 'sourceFunctionId', dataIndex: 'sourceFunctionId', width: 140 },
  { title: '外部单号', key: 'sourceDocumentId', dataIndex: 'sourceDocumentId', width: 180 },
  { title: '分类', key: 'classification', dataIndex: 'classification', width: 140 },
  { title: '处理状态', key: 'status', dataIndex: 'status', width: 120 },
  { title: '知识库状态', key: 'knowledgeImportStatus', dataIndex: 'knowledgeImportStatus', width: 120 },
  { title: '目标知识库', key: 'linkedKbId', dataIndex: 'linkedKbId', width: 160 },
  { title: '上传时间', key: 'createdAt', dataIndex: 'createdAt', width: 170 },
  { title: '操作', key: 'actions', width: 200, fixed: 'right' }
]

const processingStatusOptions = [
  { value: 'uploaded', label: '待处理' },
  { value: 'parsing', label: '解析中' },
  { value: 'summarizing', label: '摘要中' },
  { value: 'ready', label: '已完成' },
  { value: 'failed', label: '失败' }
]

const importStatusOptions = [
  { value: 'none', label: '未入库' },
  { value: 'importing', label: '入库中' },
  { value: 'indexed', label: '已入库' },
  { value: 'failed', label: '入库失败' }
]

const ocrOptions = [
  { value: 'disable', label: '不启用 OCR' },
  { value: 'rapid_ocr', label: 'RapidOCR (ONNX)' },
  { value: 'mineru_ocr', label: 'MinerU OCR' },
  { value: 'pp_structure_v3_ocr', label: 'PP-StructureV3 OCR' },
  { value: 'deepseek_ocr', label: 'DeepSeek OCR' }
]

const databaseOptions = computed(() =>
  databases.value
    .filter((item) => item.supports_documents !== false)
    .map((item) => ({ value: item.kb_id, label: item.name || item.kb_id }))
)

const selectedDatabasePresetId = computed(() => {
  const database = databases.value.find((item) => item.kb_id === importForm.kbId)
  return database?.additional_params?.chunk_preset_id || 'general'
})

const pagination = computed(() => ({
  current: pager.page,
  pageSize: pager.pageSize,
  total: total.value,
  showSizeChanger: true,
  showTotal: (count) => `共 ${count} 条`
}))

function processingStatusMeta(status) {
  return (
    {
      uploaded: { label: '待处理', color: 'default' },
      parsing: { label: '解析中', color: 'processing' },
      summarizing: { label: '摘要中', color: 'processing' },
      ready: { label: '已完成', color: 'success' },
      failed: { label: '失败', color: 'error' }
    }[status] || { label: status || '未知', color: 'default' }
  )
}

function importStatusMeta(status) {
  return (
    {
      none: { label: '未入库', color: 'default' },
      importing: { label: '入库中', color: 'processing' },
      indexed: { label: '已入库', color: 'success' },
      failed: { label: '入库失败', color: 'error' }
    }[status || 'none'] || { label: status || '未知', color: 'default' }
  )
}

function databaseName(kbId) {
  if (!kbId) return ''
  return databases.value.find((item) => item.kb_id === kbId)?.name || kbId
}

function formatDate(value) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-'
}

function formatSize(value) {
  const size = Number(value || 0)
  if (!size) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`
}

function stringifyJson(value) {
  return JSON.stringify(value || {}, null, 2)
}

function canImport(record) {
  return record?.status === 'ready' && !['importing', 'indexed'].includes(record?.knowledgeImportStatus)
}

function canRetry(record) {
  return record?.status === 'failed'
}

function canOpenKnowledgePreview(record) {
  return Boolean(record?.linkedKbId && record?.linkedFileId)
}

function buildFolderTree(items) {
  const folders = (items || []).filter((item) => item.is_folder || item.isFolder)
  const fileId = (item) => item.file_id || item.fileId
  const parentId = (item) => item.parent_id || item.parentId
  const nodeMap = new Map(
    folders.map((item) => [
      fileId(item),
      {
        title: item.filename || item.name,
        value: fileId(item),
        key: fileId(item),
        children: []
      }
    ])
  )
  const roots = []
  folders.forEach((item) => {
    const node = nodeMap.get(fileId(item))
    const parent = nodeMap.get(parentId(item))
    if (parent) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  })
  return roots
}

async function loadDocuments() {
  loading.value = true
  try {
    const result = await incomingDocumentApi.list({
      page: pager.page,
      page_size: pager.pageSize,
      ...filters
    })
    documents.value = result.items || []
    total.value = result.total || 0
  } catch (error) {
    message.error(error.message || '加载来文失败')
  } finally {
    loading.value = false
  }
}

async function loadDatabases() {
  try {
    const result = await databaseApi.getAccessibleDatabases()
    databases.value = result.databases || []
  } catch (error) {
    message.error(error.message || '加载知识库失败')
  }
}

async function loadFolderTree(kbId) {
  if (!kbId) {
    folderTreeData.value = []
    return
  }
  folderLoading.value = true
  try {
    const result = await documentApi.listDocuments(kbId, {
      page: 1,
      page_size: 500,
      status: 'all',
      recursive: true
    })
    folderTreeData.value = buildFolderTree(result.items || [])
  } catch (error) {
    folderTreeData.value = []
    message.error(error.message || '加载知识库目录失败')
  } finally {
    folderLoading.value = false
  }
}

function reloadFirstPage() {
  pager.page = 1
  loadDocuments()
}

function handleTableChange(nextPagination) {
  pager.page = nextPagination.current
  pager.pageSize = nextPagination.pageSize
  loadDocuments()
}

async function openDetail(record) {
  detailOpen.value = true
  detailLoading.value = true
  try {
    detail.value = await incomingDocumentApi.detail(record.incomingId)
  } catch (error) {
    message.error(error.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

function openImport(record) {
  importTarget.value = record
  importForm.kbId = record.linkedKbId || databaseOptions.value[0]?.value
  importForm.parentId = null
  importForm.ocrEngine = 'disable'
  importForm.chunkParams.chunk_preset_id = ''
  importForm.chunkParams.chunk_parser_config = {}
  loadFolderTree(importForm.kbId)
  importOpen.value = true
}

async function retryProcessing(record) {
  if (!record?.incomingId) return
  retryingId.value = record.incomingId
  try {
    await incomingDocumentApi.retry(record.incomingId)
    message.success('已提交重试任务')
    await loadDocuments()
    if (detailOpen.value && detail.value?.incomingId === record.incomingId) {
      detail.value = await incomingDocumentApi.detail(record.incomingId)
    }
  } catch (error) {
    message.error(error.message || '提交重试失败')
  } finally {
    retryingId.value = ''
  }
}

function openKnowledgePreview(record) {
  knowledgePreview.kbId = record.linkedKbId
  knowledgePreview.fileId = record.linkedFileId
  knowledgePreviewOpen.value = true
}

async function submitImport() {
  if (!importTarget.value?.incomingId || !importForm.kbId) {
    message.warning('请选择目标知识库')
    return
  }

  importing.value = true
  try {
    const params = {
      ocr_engine: importForm.ocrEngine,
      ocr_engine_config: {},
      ...buildChunkParamsPayload(importForm.chunkParams)
    }
    await incomingDocumentApi.importToKnowledge(importTarget.value.incomingId, {
      kbId: importForm.kbId,
      parentId: importForm.parentId || null,
      params
    })
    message.success('已提交入库任务')
    importOpen.value = false
    await loadDocuments()
    if (detailOpen.value && detail.value?.incomingId === importTarget.value.incomingId) {
      detail.value = await incomingDocumentApi.detail(importTarget.value.incomingId)
    }
  } catch (error) {
    message.error(error.message || '提交入库失败')
  } finally {
    importing.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadDatabases(), loadDocuments()])
})

watch(
  () => importForm.kbId,
  (kbId) => {
    if (importOpen.value) {
      importForm.parentId = null
      loadFolderTree(kbId)
    }
  }
)
</script>

<style scoped lang="less">
.incoming-documents-view {
  height: 100%;
  background: var(--gray-25);
}

.incoming-content {
  padding: 16px var(--page-padding) 24px;
}

.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}

.keyword-input {
  width: 320px;
}

.filter-select {
  width: 150px;
}

.file-cell,
.row-actions,
.tag-row,
.knowledge-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.file-cell span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-body {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.detail-section h2 {
  margin: 0 0 10px;
  color: var(--gray-1000);
  font-size: 15px;
  font-weight: 600;
}

.summary-text {
  margin: 10px 0 0;
  color: var(--gray-800);
  white-space: pre-wrap;
}

.json-box,
.markdown-box {
  max-height: 300px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
  color: var(--gray-900);
  font-size: 12px;
  line-height: 1.5;
}

.markdown-box {
  max-height: 420px;
  white-space: pre-wrap;
}

.muted {
  color: var(--gray-500);
  font-size: 13px;
}

.path-text {
  word-break: break-all;
  color: var(--gray-700);
  font-size: 12px;
}

.detail-action-button {
  margin-top: 12px;
  margin-right: 8px;
}

@media (max-width: 760px) {
  .toolbar {
    flex-wrap: wrap;
  }

  .keyword-input,
  .filter-select {
    width: 100%;
  }
}
</style>
