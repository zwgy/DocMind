export function splitStreamingText(text: string, chunkSize = 6) {
  const chars = Array.from(text || '')
  const size = Math.max(1, chunkSize)
  const chunks: string[] = []
  for (let index = 0; index < chars.length; index += size) {
    chunks.push(chars.slice(index, index + size).join(''))
  }
  return chunks
}
