"""在联网构建阶段将 OCR 模型与分句数据写入成品镜像，避免运行时下载。"""

import nltk

from yuxi.knowledge.parser.rapid_ocr import RapidOCRParser


def main() -> None:
    """任何资源下载或加载失败都中止构建，不交付缺模型的镜像。"""
    health = RapidOCRParser().check_health()
    if health["status"] != "healthy":
        raise RuntimeError(health["message"])
    if not nltk.download("punkt_tab", download_dir="/usr/local/share/nltk_data", raise_on_error=True):
        raise RuntimeError("NLTK punkt_tab 下载失败")


if __name__ == "__main__":
    main()
