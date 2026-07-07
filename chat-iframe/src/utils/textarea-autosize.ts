type AutosizeTextarea = {
  scrollHeight: number
  style: {
    height: string
    overflowY: string
  }
}

export function autosizeTextarea(textarea: AutosizeTextarea | null, maxHeight = 180) {
  if (!textarea) return
  textarea.style.height = 'auto'
  const nextHeight = Math.min(textarea.scrollHeight, maxHeight)
  textarea.style.height = `${nextHeight}px`
  textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden'
}
