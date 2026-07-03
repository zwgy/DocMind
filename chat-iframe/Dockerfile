# syntax=docker/dockerfile:1

FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json pnpm-lock.yaml ./

# 使用 Corepack 是为了按 packageManager 固定 pnpm 版本，避免构建机全局包管理器漂移。
RUN corepack enable && corepack pnpm install --frozen-lockfile

COPY . .
RUN corepack pnpm build

FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html/chat-iframe
COPY --from=builder /app/public/docmind-chat-iframe-parent.js /usr/share/nginx/html/chat-iframe/docmind-chat-iframe-parent.js

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
