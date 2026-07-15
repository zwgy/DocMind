# syntax=docker/dockerfile:1

FROM node:20-alpine AS development

WORKDIR /app
ENV TZ=Asia/Shanghai

COPY ./chat-iframe/package.json ./chat-iframe/pnpm-lock.yaml ./

# 按项目声明固定 pnpm 版本，避免开发与生产构建使用不同的依赖解析结果。
RUN corepack enable && corepack pnpm install --frozen-lockfile

COPY ./chat-iframe .

EXPOSE 5174

FROM development AS build-stage

RUN corepack pnpm build

FROM nginx:alpine AS production

COPY ./docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY ./chat-iframe/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build-stage /app/dist /usr/share/nginx/html/chat-iframe

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
