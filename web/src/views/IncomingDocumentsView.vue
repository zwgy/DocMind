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
          <a-select-option
            v-for="item in processingStatusOptions"
            :key="item.value"
            :value="item.value"
          >
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
          <a-select-option
            v-for="item in importStatusOptions"
            :key="item.value"
            :value="item.value"
          >
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
        :expanded-row-keys="expandedRowKeys"
        :show-expand-column="false"
        :scroll="{ x: 1220 }"
        @change="handleTableChange"
      >
        <template #expandedRowRender="{ record }">
          <a-spin :spinning="isAttachmentListLoading(record.incomingId)">
            <a-list
              v-if="expandedAttachmentFiles(record.incomingId).length"
              class="expanded-attachment-list"
              size="small"
              :data-source="expandedAttachmentFiles(record.incomingId)"
            >
              <template #renderItem="{ item }">
                <a-list-item>
                  <div>
                    <a-space>
                      <FileText :size="15" />
                      <span>{{ item.filename }}</span>
                      <a-tag v-if="item.isMainFile">主文件</a-tag>
                      <a-tag :color="processingStatusMeta(item.status).color">
                        {{ processingStatusMeta(item.status).label }}
                      </a-tag>
                      <a-tag :color="importStatusMeta(item.knowledgeImportStatus).color">
                        {{ importStatusMeta(item.knowledgeImportStatus).label }}
                      </a-tag>
                    </a-space>
                    <p v-if="!item.isMainFile && item.summary" class="attachment-summary">
                      {{ item.summary }}
                    </p>
                  </div>
                  <template #actions>
                    <a-button
                      type="link"
                      size="small"
                      @click="openDetail(record, item.sourceFileId)"
                    >
                      查看
                    </a-button>
                  </template>
                </a-list-item>
              </template>
            </a-list>
            <div
              v-else-if="!isAttachmentListLoading(record.incomingId)"
              class="empty-content compact"
            >
              <p>暂无附件</p>
            </div>
          </a-spin>
        </template>
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'filename'">
            <div
              class="file-cell attachment-toggle"
              role="button"
              tabindex="0"
              :aria-label="`展开${record.title || record.sourceDocumentId}的附件`"
              @click="toggleAttachmentList(record)"
              @keydown.enter.prevent="toggleAttachmentList(record)"
              @keydown.space.prevent="toggleAttachmentList(record)"
            >
              <ChevronRight
                :size="16"
                :class="{ 'is-expanded': isAttachmentListExpanded(record.incomingId) }"
              />
              <FileText :size="16" />
              <span :title="record.title || record.sourceDocumentId">{{
                record.title || record.sourceDocumentId
              }}</span>
            </div>
          </template>
          <template v-else-if="column.key === 'classification'">
            <a-tag>{{ record.effectiveClassificationLabel || '未分类' }}</a-tag>
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
                重新处理
              </a-button>
              <a-button
                type="link"
                size="small"
                :disabled="!canImport(record)"
                @click="openImport(record)"
              >
                批量入库
              </a-button>
              <a-button
                v-if="canDelete(record)"
                type="link"
                danger
                size="small"
                :loading="deletingId === record.incomingId"
                @click="openDeleteConfirm(record)"
              >
                删除
              </a-button>
            </div>
          </template>
        </template>
      </a-table>
    </div>

    <a-drawer
      v-model:open="detailOpen"
      width="min(920px, 92vw)"
      :title="detail?.title || detail?.sourceDocumentId || '来文详情'"
      :destroy-on-close="true"
    >
      <a-spin :spinning="detailLoading">
        <div v-if="detail" class="detail-body">
          <section class="detail-section">
            <h2>基本信息</h2>
            <a-descriptions size="small" bordered :column="2">
              <a-descriptions-item label="来源系统">{{
                detail.sourceSystem || '-'
              }}</a-descriptions-item>
              <a-descriptions-item label="功能 ID">{{
                detail.sourceFunctionId || '-'
              }}</a-descriptions-item>
              <a-descriptions-item label="外部单号">{{
                detail.sourceDocumentId || '-'
              }}</a-descriptions-item>
              <a-descriptions-item label="附件数量">{{
                detail.files?.length || 0
              }}</a-descriptions-item>
              <a-descriptions-item label="上传时间">{{
                formatDate(detail.createdAt)
              }}</a-descriptions-item>
              <a-descriptions-item label="来文标题" :span="2">{{
                detail.title || '-'
              }}</a-descriptions-item>
              <a-descriptions-item
                v-for="[key, value] in documentMetadataEntries"
                :key="key"
                :label="documentMetadataLabel(key)"
              >
                {{ displayValue(value) }}
              </a-descriptions-item>
            </a-descriptions>
          </section>

          <section class="detail-section">
            <h2>处理结果</h2>
            <div class="tag-row">
              <a-tag :color="processingStatusMeta(detail.status).color">
                {{ processingStatusMeta(detail.status).label }}
              </a-tag>
              <a-tag>{{ detail.effectiveClassificationLabel || '未分类' }}</a-tag>
              <a-tag v-if="detail.confirmedClassification" color="blue">已人工纠偏</a-tag>
              <a-tag v-if="detail.reviewStatus === 'confirmed'" color="green">已确认</a-tag>
              <span v-if="detail.classificationConfidence !== null" class="muted">
                置信度 {{ percent(detail.classificationConfidence) }}
              </span>
              <a-select
                v-model:value="selectedClassification"
                size="small"
                class="classification-select"
                :options="classificationOptions"
              />
              <a-button
                size="small"
                :loading="correcting"
                :disabled="
                  detail.status !== 'ready' ||
                  ['importing', 'partial', 'indexed'].includes(detail.knowledgeImportStatus)
                "
                @click="correctClassification"
                >纠偏并重跑</a-button
              >
              <a-button
                size="small"
                type="primary"
                :disabled="detail.status !== 'ready' || detail.reviewStatus === 'confirmed'"
                @click="confirmDocument"
                >确认来文</a-button
              >
            </div>
            <a-alert
              v-if="detail.processingError"
              type="error"
              show-icon
              :message="detail.processingError"
            />
            <div v-if="detail.additionalClassifications?.length" class="additional-classifications">
              <p
                v-for="item in detail.additionalClassifications"
                :key="item.classification"
                class="summary-text"
              >
                <a-tag color="purple">附加分类：{{ item.classificationLabel }}</a-tag>
                置信度 {{ percent(item.confidence) }}；原文依据：{{ item.evidence }}
              </p>
            </div>
            <a-typography-paragraph class="summary-text">
              {{ detail.summary || '暂无摘要' }}
            </a-typography-paragraph>
          </section>

          <section class="detail-section">
            <h2>结构化结果</h2>
            <a-collapse
              v-if="businessExtractionGroups.length"
              class="business-extraction-list"
              ghost
            >
              <a-collapse-panel
                v-for="group in businessExtractionGroups"
                :key="group.itemType"
                class="business-extraction-group"
                :header="`${group.label}（${group.items.length}）`"
              >
                <div class="business-extraction-group-content">
                  <p class="summary-text">{{ group.summary }}</p>
                  <article
                    v-for="(item, index) in group.items"
                    :key="item.item_id || `${group.itemType}-${index}`"
                    class="business-extraction-item"
                  >
                    <strong>{{ group.label }} {{ index + 1 }}</strong>
                    <dl v-if="displayExtractionDataEntries(item).length">
                      <template
                        v-for="[key, value] in displayExtractionDataEntries(item)"
                        :key="key"
                      >
                        <dt>{{ key }}</dt>
                        <dd>{{ displayValue(value) }}</dd>
                      </template>
                    </dl>
                    <div
                      v-for="evidence in item.evidence || []"
                      :key="`${evidence.file_name}-${evidence.source_location}`"
                      class="muted"
                    >
                      来源定位：{{ evidence.file_name }} {{ evidence.source_location || '' }}
                      <blockquote v-if="evidence.quote">模型参考片段：{{ evidence.quote }}</blockquote>
                    </div>
                  </article>
                </div>
              </a-collapse-panel>
            </a-collapse>
            <div v-else class="empty-content compact">
              <p>正式结构化结果暂未生成</p>
            </div>
          </section>

          <section class="detail-section">
            <h2>附件（{{ detail.files?.length || 0 }}）</h2>
            <a-list size="small" :data-source="detail.files || []">
              <template #renderItem="{ item }">
                <a-list-item>
                  <div>
                    <a-space>
                      <FileText :size="15" />
                      <span>{{ item.filename }}</span>
                      <a-tag v-if="item.isMainFile">主文件</a-tag>
                      <a-tag :color="processingStatusMeta(item.status).color">{{
                        processingStatusMeta(item.status).label
                      }}</a-tag>
                      <a-tag :color="importStatusMeta(item.knowledgeImportStatus).color">
                        {{ importStatusMeta(item.knowledgeImportStatus).label }}
                      </a-tag>
                    </a-space>
                    <p v-if="!item.isMainFile && item.summary" class="attachment-summary">
                      {{ item.summary }}
                    </p>
                  </div>
                  <template #actions>
                    <a-button type="link" size="small" @click="selectAttachment(item)"
                      >查看原文</a-button
                    >
                    <a-button
                      v-if="canOpenKnowledgePreview(item)"
                      type="link"
                      size="small"
                      @click="openKnowledgePreview(item)"
                      >知识库预览</a-button
                    >
                  </template>
                </a-list-item>
              </template>
            </a-list>
          </section>

          <section
            v-if="selectedAttachment && !selectedAttachment.isMainFile && selectedAttachment.summary"
            class="detail-section"
          >
            <h2>副附件摘要</h2>
            <p class="summary-text">{{ selectedAttachment.summary }}</p>
          </section>

          <section class="detail-section">
            <h2>原文预览</h2>
            <a-tabs
              v-if="hasOriginalFile || hasMarkdownFile"
              v-model:active-key="previewTab"
              class="preview-tabs"
            >
              <a-tab-pane v-if="hasOriginalFile" key="source">
                <template #tab>
                  <span class="preview-tab-label">
                    <FileSearch :size="14" />
                    原文
                  </span>
                </template>
                <div class="source-preview-wrapper">
                  <a-spin v-if="sourcePreview.loading" tip="正在加载原文..." />
                  <div
                    v-else-if="sourcePreview.message && !sourcePreviewHasContent"
                    class="empty-content"
                  >
                    <p>{{ sourcePreview.message }}</p>
                  </div>
                  <AgentFilePreview
                    v-else-if="sourcePreviewHasContent"
                    :file="sourcePreview"
                    :file-path="selectedAttachment?.filename || ''"
                    :show-header="false"
                    :show-download="false"
                    :full-height="true"
                    :borderless="true"
                    container-class="source-preview-container"
                    content-class="source-preview-content"
                  />
                </div>
              </a-tab-pane>
              <a-tab-pane v-if="hasMarkdownFile" key="markdown">
                <template #tab>
                  <span class="preview-tab-label">
                    <FileText :size="14" />
                    Markdown
                  </span>
                </template>
                <a-alert
                  v-if="attachmentMarkdownTruncated"
                  type="warning"
                  show-icon
                  message="内容较长，当前仅展示前 40000 个字符"
                />
                <pre class="markdown-box">{{ attachmentMarkdown || '暂无可预览内容' }}</pre>
              </a-tab-pane>
            </a-tabs>
            <div v-else class="empty-content">
              <p>暂无可预览内容</p>
            </div>
          </section>

          <section class="detail-section">
            <h2>知识库信息</h2>
            <div class="knowledge-row">
              <a-tag :color="importStatusMeta(detail.knowledgeImportStatus).color">
                {{ importStatusMeta(detail.knowledgeImportStatus).label }}
              </a-tag>
              <span>{{
                databaseName(detail.linkedKbId) || detail.linkedKbId || '未选择知识库'
              }}</span>
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
              批量入库
            </a-button>
            <a-button
              v-if="canOpenKnowledgePreview(selectedAttachment)"
              class="detail-action-button"
              @click="openKnowledgePreview(selectedAttachment)"
            >
              预览当前附件
            </a-button>
            <a-button
              v-if="canRetry(detail)"
              class="detail-action-button"
              :loading="retryingId === detail.incomingId"
              @click="retryProcessing(detail)"
            >
              重新处理
            </a-button>
            <a-button
              v-if="canDelete(detail)"
              class="detail-action-button"
              danger
              :loading="deletingId === detail.incomingId"
              @click="openDeleteConfirm(detail)"
            >
              删除来文
            </a-button>
          </section>
        </div>
      </a-spin>
    </a-drawer>

    <a-modal
      v-model:open="importOpen"
      title="批量入库"
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
            :disabled="importTarget?.knowledgeImportStatus === 'partial'"
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
        <a-form-item label="选择要入库的附件" required>
          <a-checkbox-group v-model:value="importForm.sourceFileIds" class="import-file-list">
            <div
              v-for="file in importTarget?.files || []"
              :key="file.sourceFileId"
              class="import-file-item"
            >
              <a-button type="link" size="small" @click.stop="openImportAttachmentPreview(file)">
                预览
              </a-button>
              <a-checkbox
                :value="file.sourceFileId"
                :disabled="file.knowledgeImportStatus === 'indexed'"
              >
                {{ file.filename }}{{ file.isMainFile ? '（主文件）' : '' }}
                <a-tag :color="importStatusMeta(file.knowledgeImportStatus).color">
                  {{ importStatusMeta(file.knowledgeImportStatus).label }}
                </a-tag>
              </a-checkbox>
            </div>
          </a-checkbox-group>
          <div class="muted">
            已选择
            {{ importForm.sourceFileIds.length }}
            个附件；默认选择尚未入库的全部附件，可取消不需要进入知识库的附件。
          </div>
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

    <a-modal
      v-model:open="importPreviewOpen"
      :title="`预览附件：${importPreviewFile?.filename || ''}`"
      width="900px"
      :footer="null"
      :destroy-on-close="true"
      @after-close="resetImportPreview"
    >
      <a-spin :spinning="importPreview.loading" tip="正在加载原文...">
        <AgentFilePreview
          v-if="importPreviewFile && !importPreview.loading"
          :file="importPreview"
          :file-path="importPreviewFile.filename"
          :show-download="false"
          :show-fullscreen="true"
          :full-height="true"
          content-class="import-preview-content"
        />
      </a-spin>
    </a-modal>

    <FileDetailModal
      v-model:open="knowledgePreviewOpen"
      :kb-id="knowledgePreview.kbId"
      :file-id="knowledgePreview.fileId"
    />

    <a-modal
      v-model:open="deleteOpen"
      :title="`删除来文：${deleteTarget?.title || deleteTarget?.sourceDocumentId || ''}`"
      width="520px"
      :confirm-loading="deletingId === deleteTarget?.incomingId"
      :destroy-on-close="true"
      ok-text="确认删除"
      ok-button-props="{ danger: true }"
      @ok="confirmDelete"
      @cancel="closeDeleteConfirm"
    >
      <a-alert
        v-if="deleteTarget"
        type="warning"
        show-icon
        :message="
          deleteTarget.reviewStatus === 'confirmed'
            ? '该来文已确认，删除后无法恢复'
            : '此操作将彻底删除来文及其附件'
        "
        class="delete-warning"
      />
      <a-descriptions v-if="deleteTarget" size="small" :column="1" bordered class="delete-summary">
        <a-descriptions-item label="来源单号">{{
          deleteTarget.sourceDocumentId
        }}</a-descriptions-item>
        <a-descriptions-item label="处理状态">
          <a-tag :color="processingStatusMeta(deleteTarget.status).color">
            {{ processingStatusMeta(deleteTarget.status).label }}
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="上传时间">{{
          formatDate(deleteTarget.createdAt)
        }}</a-descriptions-item>
      </a-descriptions>
      <p class="delete-hint">
        为避免误删，请在下方输入来源单号后
        <strong>6 位</strong>（不区分大小写、忽略空白）以确认删除。
      </p>
      <a-input v-model:value="deleteConfirmText" placeholder="请输入来源单号后 6 位" allow-clear />
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { message } from 'ant-design-vue'
import { ChevronRight, FileSearch, FileText, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import ChunkParamsConfig from '@/components/ChunkParamsConfig.vue'
import FileDetailModal from '@/components/FileDetailModal.vue'
import { incomingDocumentApi } from '@/apis/incoming_document_api'
import { databaseApi, documentApi } from '@/apis/knowledge_api'
import { buildChunkParamsPayload } from '@/utils/chunk_presets'
import { getPreviewTypeByPath, normalizePreviewResponse } from '@/utils/file_preview'

const documents = ref([])
const total = ref(0)
const loading = ref(false)
const expandedRowKeys = ref([])
const expandedAttachments = reactive({})
const expandedAttachmentLoadingIds = ref([])
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const selectedAttachment = ref(null)
const attachmentMarkdown = ref('')
const attachmentMarkdownTruncated = ref(false)
const selectedClassification = ref()
const classificationOptions = ref([])
const correcting = ref(false)
const databases = ref([])
const importOpen = ref(false)
const importing = ref(false)
const importTarget = ref(null)
const importPreviewOpen = ref(false)
const importPreviewFile = ref(null)
const importPreviewSeq = ref(0)
const importPreview = reactive({
  loading: false,
  url: '',
  content: null,
  previewType: '',
  previewUrl: '',
  supported: true,
  message: ''
})
const retryingId = ref('')
// "原文 / Markdown" Tab 与原文预览状态，与知识库 FileDetailModal 保持同样的请求序号防抖模式。
const previewTab = ref('source')
const sourcePreviewSeq = ref(0)
const sourcePreview = reactive({
  loading: false,
  url: '',
  content: null,
  previewType: '',
  previewUrl: '',
  supported: true,
  message: ''
})
const folderLoading = ref(false)
const folderTreeData = ref([])
const knowledgePreviewOpen = ref(false)
const knowledgePreview = reactive({ kbId: '', fileId: '' })
const deleteOpen = ref(false)
const deleteTarget = ref(null)
const deleteConfirmText = ref('')
const deletingId = ref('')

const filters = reactive({
  keyword: '',
  status: undefined,
  knowledge_import_status: undefined
})

const pager = reactive({ page: 1, pageSize: 20 })

const importForm = reactive({
  kbId: undefined,
  parentId: null,
  sourceFileIds: [],
  ocrEngine: 'disable',
  chunkParams: {
    chunk_preset_id: '',
    chunk_parser_config: {}
  }
})

const columns = [
  { title: '来文', key: 'filename', dataIndex: 'title', width: 260, fixed: 'left' },
  { title: '来源系统', key: 'sourceSystem', dataIndex: 'sourceSystem', width: 120 },
  { title: '功能 ID', key: 'sourceFunctionId', dataIndex: 'sourceFunctionId', width: 140 },
  { title: '外部单号', key: 'sourceDocumentId', dataIndex: 'sourceDocumentId', width: 180 },
  { title: '分类', key: 'classification', dataIndex: 'classification', width: 140 },
  { title: '处理状态', key: 'status', dataIndex: 'status', width: 120 },
  {
    title: '知识库状态',
    key: 'knowledgeImportStatus',
    dataIndex: 'knowledgeImportStatus',
    width: 120
  },
  { title: '目标知识库', key: 'linkedKbId', dataIndex: 'linkedKbId', width: 160 },
  { title: '上传时间', key: 'createdAt', dataIndex: 'createdAt', width: 170 },
  { title: '操作', key: 'actions', width: 260, fixed: 'right' }
]

const processingStatusOptions = [
  { value: 'uploaded', label: '待处理' },
  { value: 'parsing', label: '解析中' },
  { value: 'extracting', label: '抽取中' },
  { value: 'ready', label: '已完成' },
  { value: 'failed', label: '失败' }
]

const importStatusOptions = [
  { value: 'none', label: '未入库' },
  { value: 'importing', label: '入库中' },
  { value: 'partial', label: '部分入库' },
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
      extracting: { label: '抽取中', color: 'processing' },
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
      partial: { label: '部分入库', color: 'warning' },
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

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`
}

function canImport(record) {
  return (
    record?.status === 'ready' && !['importing', 'indexed'].includes(record?.knowledgeImportStatus)
  )
}

function canRetry(record) {
  // 已完成但结构化结果为空时也需要人工重跑，正在处理中的状态不开放重复提交。
  return record?.status === 'failed' || record?.status === 'ready'
}

function canDelete(record) {
  // 处理中（parsing/extracting）或已入库知识库的来文禁止在管理页删除。
  if (!record) return false
  if (['parsing', 'extracting'].includes(record.status)) return false
  if (['importing', 'partial', 'indexed'].includes(record.knowledgeImportStatus)) return false
  return true
}

const deleteConfirmExpectedSuffix = computed(() => {
  const target = deleteTarget.value
  if (!target?.sourceDocumentId) return ''
  return String(target.sourceDocumentId).replace(/\s+/g, '').slice(-6).toLowerCase()
})

const isDeleteConfirmValid = computed(() => {
  const expected = deleteConfirmExpectedSuffix.value
  if (!expected || expected.length < 6) return false
  return deleteConfirmText.value.replace(/\s+/g, '').toLowerCase() === expected
})

async function openDeleteConfirm(record) {
  if (!record?.incomingId || !canDelete(record)) return
  // 列表行只有摘要字段；详情抽屉场景需要拉详情以展示附件数 / 上传时间等。
  let target = record
  if (!record.createdAt || !record.sourceDocumentId) {
    try {
      target = await incomingDocumentApi.detail(record.incomingId)
    } catch (error) {
      message.error(error.message || '加载来文详情失败')
      return
    }
  }
  deleteTarget.value = target
  deleteConfirmText.value = ''
  deleteOpen.value = true
}

function closeDeleteConfirm() {
  deleteOpen.value = false
  deleteTarget.value = null
  deleteConfirmText.value = ''
}

async function confirmDelete() {
  if (!deleteTarget.value?.incomingId) {
    closeDeleteConfirm()
    return
  }
  if (!isDeleteConfirmValid.value) {
    message.warning('请输入正确的来源单号后 6 位以确认删除')
    return
  }
  const incomingId = deleteTarget.value.incomingId
  deletingId.value = incomingId
  try {
    const result = await incomingDocumentApi.remove(incomingId)
    const removedFiles = result?.removedFiles ?? 0
    const minioErrors = Array.isArray(result?.minioErrors) ? result.minioErrors : []
    message.success(
      minioErrors.length
        ? `来文已删除，但 ${minioErrors.length} 个对象清理失败，请联系运维`
        : `来文已删除（清理 ${removedFiles} 个附件）`
    )
    if (detailOpen.value && detail.value?.incomingId === incomingId) {
      detailOpen.value = false
    }
    closeDeleteConfirm()
    await loadDocuments()
  } catch (error) {
    message.error(error.message || '删除来文失败')
  } finally {
    deletingId.value = ''
  }
}

function canOpenKnowledgePreview(record) {
  return Boolean(detail.value?.linkedKbId && record?.linkedFileId)
}

function isAttachmentListExpanded(incomingId) {
  return expandedRowKeys.value.includes(incomingId)
}

function isAttachmentListLoading(incomingId) {
  return expandedAttachmentLoadingIds.value.includes(incomingId)
}

function expandedAttachmentFiles(incomingId) {
  return expandedAttachments[incomingId] || []
}

const hasOriginalFile = computed(() => Boolean(selectedAttachment.value?.hasOriginalFile))
const hasMarkdownFile = computed(() => Boolean(selectedAttachment.value?.hasMarkdownFile))
const sourcePreviewHasContent = computed(
  () => Boolean(sourcePreview.content) || Boolean(sourcePreview.previewUrl || sourcePreview.url)
)
const businessExtractionGroups = computed(() => {
  return (detail.value?.businessExtraction?.groups || []).map((group) => ({
    itemType: group.itemType,
    label: extractionItemTypeText(group.itemType),
    summary: group.summary,
    items: group.details || []
  }))
})
const documentMetadataEntries = computed(() =>
  Object.entries(detail.value?.documentMetadata || {}).filter(
    ([key, value]) => key !== 'title' && value !== null && value !== undefined && value !== ''
  )
)

function documentMetadataLabel(key) {
  return (
    {
      document_number: '来文编号',
      incoming_type: '来文类型',
      source_unit: '发文单位',
      incoming_date: '来文日期'
    }[key] || key
  )
}

function extractionItemTypeText(itemType) {
  return (
    detail.value?.businessExtraction?.display?.schemaLabels?.[itemType] || itemType || '结构化对象'
  )
}

function displayExtractionDataEntries(item) {
  const data = item?.data || {}
  const labels = detail.value?.businessExtraction?.display?.fieldLabels?.[item?.item_type] || {}
  return Object.entries(data)
    .filter(
      ([key, value]) =>
        key !== 'source_quote' && value !== null && value !== undefined && value !== ''
    )
    .map(([key, value]) => [labels[key] || key, value])
}

function displayValue(value) {
  if (Array.isArray(value)) return value.join('、')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '')
}

function revokeSourcePreviewUrl() {
  // 释放上一份 blob URL，避免反复打开时泄漏
  const url = sourcePreview.previewUrl || sourcePreview.url
  if (url && url.startsWith('blob:')) {
    try {
      window.URL.revokeObjectURL(url)
    } catch (err) {
      // 静默忽略；URL 可能已被其他清理路径释放
      console.warn('revokeObjectURL failed:', err)
    }
  }
}

function revokeImportPreviewUrl() {
  const url = importPreview.previewUrl || importPreview.url
  if (url && url.startsWith('blob:')) {
    window.URL.revokeObjectURL(url)
  }
}

function resetSourcePreview() {
  sourcePreviewSeq.value += 1
  revokeSourcePreviewUrl()
  sourcePreview.loading = false
  sourcePreview.url = ''
  sourcePreview.content = null
  sourcePreview.previewType = ''
  sourcePreview.previewUrl = ''
  sourcePreview.supported = true
  sourcePreview.message = ''
}

function resetImportPreview() {
  importPreviewSeq.value += 1
  revokeImportPreviewUrl()
  importPreview.loading = false
  importPreview.url = ''
  importPreview.content = null
  importPreview.previewType = ''
  importPreview.previewUrl = ''
  importPreview.supported = true
  importPreview.message = ''
  importPreviewFile.value = null
}

async function loadMarkdownPreview() {
  if (!detail.value?.incomingId || !selectedAttachment.value?.hasMarkdownFile) {
    attachmentMarkdown.value = ''
    attachmentMarkdownTruncated.value = false
    return
  }
  try {
    const response = await incomingDocumentApi.getMarkdown(
      detail.value.incomingId,
      selectedAttachment.value.sourceFileId
    )
    attachmentMarkdown.value = response.content || ''
    attachmentMarkdownTruncated.value = Boolean(response.truncated)
  } catch (error) {
    attachmentMarkdown.value = error?.message || '加载 Markdown 失败'
    attachmentMarkdownTruncated.value = false
  }
}

function pickDefaultPreviewTab() {
  if (!selectedAttachment.value?.hasOriginalFile) return 'markdown'
  // 文件类型不可预览（zip、exe 等）时直接落到 Markdown，避免空白原文 tab
  return getPreviewTypeByPath(selectedAttachment.value.filename || '') === 'unsupported'
    ? 'markdown'
    : 'source'
}

async function loadSourcePreview() {
  if (!detail.value?.incomingId || !selectedAttachment.value?.hasOriginalFile) return
  const requestId = ++sourcePreviewSeq.value
  sourcePreview.loading = true
  sourcePreview.message = ''
  try {
    const response = await incomingDocumentApi.getOriginalFile(
      detail.value.incomingId,
      selectedAttachment.value.sourceFileId
    )
    if (requestId !== sourcePreviewSeq.value) {
      // 用户已经切换详情或关闭抽屉，丢弃延迟到达的响应
      if (response?.blob) {
        response
          .blob()
          .then((blob) => window.URL.revokeObjectURL(window.URL.createObjectURL(blob)))
          .catch(() => {})
      }
      return
    }
    revokeSourcePreviewUrl()
    const preview = await normalizePreviewResponse(response)
    sourcePreview.previewType = preview.previewType || ''
    sourcePreview.previewUrl = preview.previewUrl || ''
    sourcePreview.url = preview.previewUrl || ''
    sourcePreview.content = preview.content ?? null
    sourcePreview.supported = preview.supported !== false
    sourcePreview.message = preview.message || ''
  } catch (error) {
    if (requestId !== sourcePreviewSeq.value) return
    sourcePreview.supported = false
    sourcePreview.message = error?.message || '加载原文失败'
  } finally {
    if (requestId === sourcePreviewSeq.value) {
      sourcePreview.loading = false
    }
  }
}

function selectAttachment(file) {
  selectedAttachment.value = file
  resetSourcePreview()
  attachmentMarkdown.value = ''
  attachmentMarkdownTruncated.value = false
  previewTab.value = pickDefaultPreviewTab()
  if (previewTab.value === 'source') loadSourcePreview()
  loadMarkdownPreview()
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
    expandedRowKeys.value = []
    Object.keys(expandedAttachments).forEach((incomingId) => delete expandedAttachments[incomingId])
    expandedAttachmentLoadingIds.value = []
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

async function openDetail(record, sourceFileId) {
  detailOpen.value = true
  detailLoading.value = true
  try {
    detail.value = await incomingDocumentApi.detail(record.incomingId)
    selectAttachment(
      detail.value.files?.find((file) => file.sourceFileId === sourceFileId) ||
        detail.value.files?.find((file) => file.isMainFile) ||
        detail.value.files?.[0] ||
        null
    )
    selectedClassification.value = detail.value.effectiveClassification
  } catch (error) {
    message.error(error.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

async function toggleAttachmentList(record) {
  const incomingId = record.incomingId
  if (isAttachmentListExpanded(incomingId)) {
    expandedRowKeys.value = expandedRowKeys.value.filter((value) => value !== incomingId)
    return
  }

  expandedRowKeys.value = [...expandedRowKeys.value, incomingId]
  if (Object.prototype.hasOwnProperty.call(expandedAttachments, incomingId)) return

  // 分页列表首次加载不携带全部附件，用户展开某一来文时才读取一次详情，避免形成 N+1 请求。
  expandedAttachmentLoadingIds.value = [...expandedAttachmentLoadingIds.value, incomingId]
  try {
    const result = await incomingDocumentApi.detail(incomingId)
    expandedAttachments[incomingId] = result.files || []
  } catch (error) {
    expandedAttachments[incomingId] = []
    message.error(error.message || '加载来文附件失败')
  } finally {
    expandedAttachmentLoadingIds.value = expandedAttachmentLoadingIds.value.filter(
      (value) => value !== incomingId
    )
  }
}

async function openImportAttachmentPreview(file) {
  if (!importTarget.value?.incomingId) return
  // 入库判断只需核对当前附件原文；独立状态避免干扰已打开详情抽屉中的预览内容。
  resetImportPreview()
  importPreviewFile.value = file
  importPreviewOpen.value = true
  const requestId = ++importPreviewSeq.value
  importPreview.loading = true
  try {
    const response = await incomingDocumentApi.getOriginalFile(
      importTarget.value.incomingId,
      file.sourceFileId
    )
    if (requestId !== importPreviewSeq.value) return
    const preview = await normalizePreviewResponse(response)
    importPreview.previewType = preview.previewType || ''
    importPreview.previewUrl = preview.previewUrl || ''
    importPreview.url = preview.previewUrl || ''
    importPreview.content = preview.content ?? null
    importPreview.supported = preview.supported !== false
    importPreview.message = preview.message || ''
  } catch (error) {
    if (requestId !== importPreviewSeq.value) return
    importPreview.supported = false
    importPreview.message = error?.message || '加载原文失败'
  } finally {
    if (requestId === importPreviewSeq.value) importPreview.loading = false
  }
}

async function correctClassification() {
  if (!detail.value?.incomingId || !selectedClassification.value) return
  correcting.value = true
  try {
    await incomingDocumentApi.correctClassification(
      detail.value.incomingId,
      selectedClassification.value
    )
    detail.value = await incomingDocumentApi.detail(detail.value.incomingId)
    message.success('已按纠偏分类重新抽取')
    await loadDocuments()
  } catch (error) {
    message.error(error.message || '分类纠偏失败')
  } finally {
    correcting.value = false
  }
}

async function confirmDocument() {
  if (!detail.value?.incomingId) return
  try {
    await incomingDocumentApi.confirm(detail.value.incomingId)
    detail.value = await incomingDocumentApi.detail(detail.value.incomingId)
    message.success('来文已确认')
  } catch (error) {
    message.error(error.message || '确认失败')
  }
}

async function loadClassificationOptions() {
  const result = await incomingDocumentApi.options()
  classificationOptions.value = Object.entries(result.classifications || {}).map(
    ([value, label]) => ({
      value,
      label
    })
  )
}

async function openImport(record) {
  try {
    importTarget.value = Array.isArray(record.files)
      ? record
      : await incomingDocumentApi.detail(record.incomingId)
  } catch (error) {
    message.error(error.message || '加载来文附件失败')
    return
  }
  importForm.kbId = importTarget.value.linkedKbId || databaseOptions.value[0]?.value
  importForm.parentId = null
  importForm.sourceFileIds = (importTarget.value.files || [])
    .filter((file) => file.knowledgeImportStatus !== 'indexed')
    .map((file) => file.sourceFileId)
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
  knowledgePreview.kbId = detail.value.linkedKbId
  knowledgePreview.fileId = record.linkedFileId
  knowledgePreviewOpen.value = true
}

async function submitImport() {
  if (!importTarget.value?.incomingId || !importForm.kbId) {
    message.warning('请选择目标知识库')
    return
  }
  if (!importForm.sourceFileIds.length) {
    message.warning('请至少选择一个待入库附件')
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
      sourceFileIds: importForm.sourceFileIds,
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
  await Promise.all([loadDatabases(), loadDocuments(), loadClassificationOptions()])
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

// 用户从 Markdown tab 切回原文 tab 时按需触发加载
watch(previewTab, (tab) => {
  if (tab === 'source' && !sourcePreviewHasContent.value && !sourcePreview.loading) {
    loadSourcePreview()
  }
})

watch(detailOpen, (open) => {
  if (!open) {
    resetSourcePreview()
    selectedAttachment.value = null
    attachmentMarkdown.value = ''
    attachmentMarkdownTruncated.value = false
    previewTab.value = 'source'
  }
})

onBeforeUnmount(() => {
  resetSourcePreview()
  resetImportPreview()
})
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

.attachment-toggle {
  cursor: pointer;
}

.attachment-toggle:hover {
  color: var(--color-primary-700);
}

.attachment-toggle svg {
  flex: none;
  transition: transform 0.2s ease;
}

.attachment-toggle svg.is-expanded {
  transform: rotate(90deg);
}

.expanded-attachment-list {
  max-width: 760px;
}

.attachment-summary {
  max-width: 680px;
  margin: 5px 0 0 23px;
  overflow: hidden;
  color: var(--gray-600);
  font-size: 13px;
  line-height: 1.5;
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

.business-extraction-list {
  margin-top: 4px;
}

.business-extraction-group-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.business-extraction-group-content .summary-text {
  margin-top: 0;
}

.business-extraction-item {
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
}

.business-extraction-item strong {
  display: block;
  margin-bottom: 8px;
  color: var(--gray-900);
  font-size: 13px;
}

.business-extraction-item dl {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 6px 10px;
  margin: 0;
}

.business-extraction-item dt {
  color: var(--gray-500);
}

.business-extraction-item dd {
  min-width: 0;
  margin: 0;
  color: var(--gray-800);
  word-break: break-word;
}

.business-extraction-item blockquote {
  margin: 10px 0 0;
  padding-left: 10px;
  border-left: 3px solid var(--gray-200);
  color: var(--gray-600);
  white-space: pre-wrap;
}

.summary-result-collapse {
  margin-top: 10px;
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

.import-file-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.import-file-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.import-preview-content {
  min-height: 420px;
  max-height: 65vh;
}

.preview-tabs {
  margin-top: 4px;
}

.delete-warning {
  margin-bottom: 12px;
}

.delete-summary {
  margin-bottom: 12px;
}

.delete-hint {
  margin: 8px 0;
  color: var(--gray-700);
  font-size: 13px;
}

.preview-tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.source-preview-wrapper {
  min-height: 240px;
  max-height: 480px;
  overflow: auto;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
}

.source-preview-wrapper :deep(.source-preview-container),
.source-preview-wrapper :deep(.source-preview-content) {
  min-height: 240px;
  background: var(--gray-25);
}

.empty-content {
  padding: 40px 0;
  text-align: center;
  color: var(--gray-500);
}

.empty-content.compact {
  padding: 18px 0;
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
