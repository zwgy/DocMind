# 运行时镜像只扩展既有沙盒服务镜像，避免改变 provisioner 与沙盒 API 的通信协议。
ARG BASE_SANDBOX_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
FROM ${BASE_SANDBOX_IMAGE}

USER root

# MySQL Skill 在用户沙盒内执行。构建期固定依赖后，禁网运行时不会触发 uv 或 pip 下载。
RUN python -m pip install --no-cache-dir PyMySQL==1.1.1
