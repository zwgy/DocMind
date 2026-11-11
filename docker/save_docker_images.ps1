# 旧脚本只保存基础镜像，不能作为当前项目的离线部署包。
Write-Error "请在联网 Linux x86_64 构建机执行：bash scripts/package-offline.sh <release>。操作步骤见 docs/advanced/offline-deployment.md。"
exit 1
