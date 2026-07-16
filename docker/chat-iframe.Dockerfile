# syntax=docker/dockerfile:1

# 与主 Web 对齐 Node 24；仍选 Alpine 以保持前端构建镜像轻量。
FROM node:24-alpine AS development

WORKDIR /app
ENV TZ=Asia/Shanghai

COPY ./chat-iframe/package.json ./chat-iframe/pnpm-lock.yaml ./

# 按项目声明固定 pnpm 版本，避免开发与生产构建使用不同的依赖解析结果。
RUN corepack enable && corepack pnpm install --frozen-lockfile

COPY ./chat-iframe .

EXPOSE 5174

FROM development AS build-stage

# 开发阶段保留调试资源；只有生产构建产物需要移除示例页面和测试附件。
RUN corepack pnpm build \
    && rm -rf dist/test1 dist/test2 dist/example.html dist/example-files \
    && test -f dist/index.html \
    && test -f dist/docmind-chat-iframe-parent.js

FROM nginx:alpine AS production

COPY ./docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./chat-iframe/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build-stage /app/dist /usr/share/nginx/html/chat-iframe

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
