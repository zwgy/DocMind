// Node ESM 解析钩子：把 Vite 风格的 "@/..." 别名展开到 src/ 下，
// 并按 .ts / .tsx / /index.ts 顺序补齐扩展名，方便 node:test 配合 --experimental-strip-types 运行。
import { existsSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve as pathResolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const projectRoot = pathResolve(here, '..')

function pickFile(base) {
  const candidates = [`${base}.ts`, `${base}.tsx`, `${base}.js`, `${base}/index.ts`]
  return candidates.find((path) => existsSync(path)) || base
}

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('@/')) {
    const tail = specifier.slice(2)
    const target = pickFile(pathResolve(projectRoot, 'src', tail))
    return nextResolve(pathToFileURL(target).href, context)
  }
  return nextResolve(specifier, context)
}
