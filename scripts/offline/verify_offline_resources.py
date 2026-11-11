"""由打包入口在无网络的新容器中执行，验证镜像自带资源能够实际工作。"""

import nltk
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from yuxi.knowledge.parser.rapid_ocr import RapidOCRParser


def main() -> None:
    """真实识别中文和数字，防止仅有模型文件却无法运行的镜像通过交付检查。"""
    parser = RapidOCRParser()
    parser._load_model()
    # 复用知识库端到端验收的清晰扫描样本，检查中文与校验码，不把单个复杂字的识别率当成资源缺失。
    image = Image.new("RGB", (1500, 400), "white")
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 48)
    sample = "【测试】部署验收专用资料\n青岚设备的校验码是 846213。\n此资料只用于知识库闭环测试。"
    ImageDraw.Draw(image).multiline_text((60, 50), sample, font=font, fill="black", spacing=30)
    result = parser.ocr(np.asarray(image))
    text = "".join(result.txts or [])
    if "846213" not in text or "校验码" not in text:
        raise RuntimeError(f"离线 OCR 识别未通过：{text!r}")
    if len(nltk.sent_tokenize("First sentence. Second sentence.")) != 2:
        raise RuntimeError("离线分句验收未通过")
    print("离线中文 OCR 与 NLTK 分句资源验收通过。")


if __name__ == "__main__":
    main()
