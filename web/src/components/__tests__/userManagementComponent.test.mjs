import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const component = readFileSync(resolve(__dirname, '../UserManagementComponent.vue'), 'utf8')

const addUserModal = component.match(/const showAddUserModal = async \(\) => \{[\s\S]*?\n\}/)?.[0] || ''

assert.match(
  addUserModal,
  /await fetchDepartments\(\)/,
  'superadmin 打开添加用户弹窗时应补拉部门列表，避免挂载时角色未恢复导致下拉为空'
)
