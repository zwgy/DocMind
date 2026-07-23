# 前端仅在构建阶段使用 Node，不依赖 glibc，因此使用同一 Node 24 的 Alpine 变体减小镜像体积。
# 默认直连 Docker Hub；镜像代理由每台机器的 .env/.env.prod 覆盖，避免将某个网络环境写死到镜像定义中。
ARG NODE_ALPINE_IMAGE=node:24-alpine
ARG NGINX_ALPINE_IMAGE=nginx:alpine

# 开发阶段
FROM ${NODE_ALPINE_IMAGE} AS development
WORKDIR /app
ENV TZ=Asia/Shanghai

# 安装 pnpm
RUN npm install -g pnpm@10.11.0 --registry=https://registry.npmmirror.com

# 复制 package.json 和 pnpm-lock.yaml
COPY ./web/package*.json ./
COPY ./web/pnpm-lock.yaml* ./

# 安装依赖
RUN pnpm install --registry=https://registry.npmmirror.com

# 复制源代码
COPY ./web .

# 暴露端口
EXPOSE 5173

# 启动开发服务器的命令在 docker-compose 文件中定义

# 生产阶段
FROM ${NODE_ALPINE_IMAGE} AS build-stage
WORKDIR /app

# Vite 只在构建时读取前端变量；静态镜像不能依赖容器启动后的 environment 注入。
ARG VITE_MINIO_CONSOLE_URL
ARG VITE_MILVUS_WEBUI_URL
ENV VITE_MINIO_CONSOLE_URL=${VITE_MINIO_CONSOLE_URL}
ENV VITE_MILVUS_WEBUI_URL=${VITE_MILVUS_WEBUI_URL}

# 安装 pnpm
RUN npm install -g pnpm@10.11.0 --registry=https://registry.npmmirror.com

# 复制依赖文件
COPY ./web/package*.json ./
COPY ./web/pnpm-lock.yaml* ./

# 安装依赖
RUN pnpm install --frozen-lockfile --registry=https://registry.npmmirror.com

# 复制源代码并构建
COPY ./web .
RUN pnpm run build

# 生产环境运行阶段
FROM ${NGINX_ALPINE_IMAGE} AS production
COPY --from=build-stage /app/dist /usr/share/nginx/html
COPY ./docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./docker/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
