import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

function hmrConnectionDiagnostics() {
  let nextClientId = 0
  return {
    name: 'hmr-connection-diagnostics',
    configureServer(server) {
      server.ws.on('connection', (socket, request) => {
        const clientId = ++nextClientId
        const remoteAddress = request.socket.remoteAddress || 'unknown'
        server.config.logger.info(`[hmr] client=${clientId} connected remote=${remoteAddress}`)
        socket.on('close', (code, reason) => {
          // 关闭码可区分页面主动卸载与异常断线，避免再从界面闪烁反推责任方。
          server.config.logger.warn(
            `[hmr] client=${clientId} disconnected code=${code} reason=${reason.toString() || '-'}`
          )
        })
      })
    }
  }
}

export default defineConfig(({ mode }) => {
  // eslint-disable-next-line no-undef
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue(), hmrConnectionDiagnostics()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    server: {
      proxy: {
        '^/api': {
          target: env.VITE_API_URL || 'http://api:5050',
          changeOrigin: true
        },
        '^/minio/public/': {
          target: env.VITE_MINIO_URL || 'http://minio:9000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/minio/, '')
        }
      },
      watch: {
        usePolling: true,
        ignored: ['**/node_modules/**', '**/dist/**'],
      },
      host: '0.0.0.0',
    }
  }
})
