export const AVATAR_BACKGROUND_TOKENS = [
  { background: 'linear-gradient(135deg, var(--main-600), var(--color-info-500))', color: '#fff' },
  {
    background: 'linear-gradient(135deg, var(--chart-palette-5), var(--chart-palette-9))',
    color: '#fff'
  },
  {
    background: 'linear-gradient(135deg, var(--chart-palette-7), var(--chart-palette-3))',
    color: '#fff'
  },
  {
    background: 'linear-gradient(135deg, var(--color-accent-500), var(--chart-palette-6))',
    color: '#fff'
  },
  {
    background: 'linear-gradient(135deg, var(--chart-palette-4), var(--color-error-500))',
    color: '#fff'
  }
]

const normalizeSeed = (id) => {
  if (id === null || id === undefined || String(id).trim() === '') {
    throw new Error('generatePixelAvatar requires an id')
  }
  return String(id).trim()
}

/** 根据 ID 在本地生成固定的像素头像，避免内网请求外部头像服务。 */
export const generatePixelAvatar = (id) => {
  const seed = normalizeSeed(id)
  let hash = 0
  for (const char of seed) {
    hash = (hash * 31 + char.codePointAt(0)) >>> 0
  }
  const hue = hash % 360
  const pixels = []
  // 五列左右对称；只把计算出的数字放入 SVG，不把原始 ID 拼入图片内容。
  for (let row = 0; row < 5; row += 1) {
    for (let column = 0; column < 3; column += 1) {
      if ((hash >>> (row * 3 + column)) & 1) {
        pixels.push(`<rect x="${column + 1}" y="${row + 1}" width="1" height="1"/>`)
        if (column < 2) {
          pixels.push(`<rect x="${5 - column}" y="${row + 1}" width="1" height="1"/>`)
        }
      }
    }
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 7 7"><rect width="7" height="7" fill="hsl(${hue},55%,92%)"/><g fill="hsl(${hue},60%,38%)">${pixels.join('')}</g></svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

export const getAvatarInitials = (name, kind = 'user') => {
  const fallback = kind === 'agent' ? '智能' : '用户'
  const normalizedName = String(name || '').trim()
  if (!normalizedName) return fallback
  return Array.from(normalizedName).slice(0, 2).join('')
}

export const getAvatarColorIndex = (seed) => {
  const normalizedSeed = String(seed || '').trim()
  const value = normalizedSeed || 'avatar'
  let hash = 0
  for (const char of value) {
    hash = (hash * 31 + char.codePointAt(0)) >>> 0
  }
  return hash % AVATAR_BACKGROUND_TOKENS.length
}

export const getAvatarFallbackStyle = (seed) => AVATAR_BACKGROUND_TOKENS[getAvatarColorIndex(seed)]
