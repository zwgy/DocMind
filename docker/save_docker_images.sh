#!/usr/bin/env bash
set -euo pipefail

# 旧清单只导出基础镜像，不能部署当前系统；保留入口并统一到完整交付流程。
exec bash "$(dirname "${BASH_SOURCE[0]}")/../scripts/package-offline.sh" "$@"
