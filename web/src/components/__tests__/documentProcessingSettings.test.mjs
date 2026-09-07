import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const settingsModal = readFileSync(resolve(__dirname, '../SettingsModal.vue'), 'utf8')
const uploadModal = readFileSync(resolve(__dirname, '../FileUploadModal.vue'), 'utf8')
const parserSettings = readFileSync(resolve(__dirname, '../DocumentParserSettingsCard.vue'), 'utf8')

assert.match(settingsModal, /文档解析配置/, '系统设置应提供全局文档解析配置入口')
assert.match(settingsModal, /DocumentParserSettingsCard/, '设置页应挂载实际的文档解析配置面板')
assert.match(parserSettings, /configApi\.getConfig/, '文档解析配置面板应读取当前系统配置')
assert.match(parserSettings, /configApi\.updateConfigBatch/, '文档解析配置面板应保存到运行时配置 API')
assert.match(parserSettings, /document_parser_ocr_engine_config/, '高级 MinerU 参数必须以系统默认配置保存')
assert.match(uploadModal, /ocr_engine:\s*'system_default'/, '知识库上传默认应继承系统文档解析配置')
assert.match(uploadModal, /使用系统默认配置/, '知识库上传应允许用户明确选择系统默认配置')
