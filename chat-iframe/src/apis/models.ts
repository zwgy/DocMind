import type { ModelOption } from '../types'

type ProviderModel = {
  spec?: string
  model_id?: string
  name?: string
  display_name?: string
}

type ProviderGroup = {
  provider_id?: string
  provider_name?: string
  provider_display_name?: string
  models?: ProviderModel[]
}

export async function listChatModels(token?: string): Promise<ModelOption[]> {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch('/api/system/model-providers/models/v2?model_type=chat', { headers })
  if (!response.ok) throw new Error(`获取模型列表失败：${response.status}`)
  const data = await response.json()
  const groups: ProviderGroup[] = Array.isArray(data?.providers)
    ? data.providers
    : Object.values((data?.data || {}) as Record<string, ProviderGroup>)
  return groups.flatMap((group) => {
    const options: ModelOption[] = []
    for (const model of group.models || []) {
      const value = model.spec || (group.provider_id && model.model_id ? `${group.provider_id}:${model.model_id}` : '')
      if (!value) continue
      options.push({
        value,
        label: model.name || model.display_name || model.model_id || value,
        provider: group.provider_name || group.provider_display_name || group.provider_id,
        model_id: model.model_id
      })
    }
    return options
  })
}
