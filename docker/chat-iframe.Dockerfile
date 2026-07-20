# 与主 Web 对齐 Node 24；仍选 Alpine 以保持前端构建镜像轻量。
# 默认直连 Docker Hub；镜像代理由每台机器的 .env/.env.prod 覆盖，避免将某个网络环境写死到镜像定义中。
ARG NODE_ALPINE_IMAGE=node:24-alpine
ARG NGINX_ALPINE_IMAGE=nginx:alpine

FROM ${NODE_ALPINE_IMAGE} AS development

WORKDIR /app
ENV TZ=Asia/Shanghai

COPY ./chat-iframe/package.json ./chat-iframe/pnpm-lock.yaml ./

# 固定 packageManager 声明的 pnpm 版本，并直接使用项目已有的镜像源。
# Corepack 会绕过 npm 配置访问 registry.npmjs.org，内网环境容易在此超时。
RUN npm install -g pnpm@10.11.0 --registry=https://registry.npmmirror.com \
    && pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com

COPY ./chat-iframe .

EXPOSE 5174

FROM development AS build-stage

# 开发阶段保留调试资源；只有生产构建产物需要移除示例页面和测试附件。
RUN pnpm build \
    && rm -rf dist/test1 dist/test2 dist/example.html dist/example-files \
    && test -f dist/index.html \
    && test -f dist/docmind-chat-iframe-parent.js

FROM ${NGINX_ALPINE_IMAGE} AS production

COPY ./docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./chat-iframe/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build-stage /app/dist /usr/share/nginx/html/chat-iframe

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
