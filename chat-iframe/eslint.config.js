import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'

export default defineConfig([
  {
    name: 'app/files-to-lint',
    files: ['**/*.{vue,js,mjs,jsx,ts}']
  },
  globalIgnores(['**/dist/**', '**/coverage/**']),
  {
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser
      },
      globals: {
        ...globals.browser,
        ...globals.node
      }
    }
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  skipFormatting,
  {
    rules: {
      // 父页面集成脚本是原生构造函数风格，使用 self 保存 this 能兼容旧浏览器事件回调。
      '@typescript-eslint/no-this-alias': 'off'
    }
  }
])
