export const MAX_ATTACHMENT_FILES = 10
export const MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024
export const MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
export const IMAGE_MIME_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/bmp']
export const IMAGE_ACCEPT = IMAGE_MIME_TYPES.join(',')
export const ATTACHMENT_LIMIT_TEXT = `任意文件，每个不超过 ${MAX_ATTACHMENT_SIZE_BYTES / 1024 / 1024} MB，一次最多 ${MAX_ATTACHMENT_FILES} 个`
export const IMAGE_LIMIT_TEXT = `jpg、jpeg、png、webp、gif、bmp，最大 ${MAX_IMAGE_SIZE_BYTES / 1024 / 1024} MB`

export function attachmentValidationError(files: File[], existingCount = 0) {
  if (existingCount + files.length > MAX_ATTACHMENT_FILES) return `一次最多添加 ${MAX_ATTACHMENT_FILES} 个附件`
  const oversized = files.find((file) => file.size > MAX_ATTACHMENT_SIZE_BYTES)
  return oversized ? `附件“${oversized.name}”超过 5 MB 限制` : ''
}

export function imageValidationError(file: File | null | undefined) {
  if (!file) return ''
  if (!IMAGE_MIME_TYPES.includes(file.type)) return '仅支持 jpg、jpeg、png、webp、gif、bmp 图片'
  return file.size > MAX_IMAGE_SIZE_BYTES ? `图片“${file.name}”超过 10 MB 限制` : ''
}
