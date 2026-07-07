// 注册 @/ 别名解析钩子，供 node:test 跑带路径别名的源码使用。
import { register } from 'node:module'
import { pathToFileURL } from 'node:url'

register('./alias-resolver.mjs', pathToFileURL('./test/'))
