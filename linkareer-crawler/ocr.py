"""본문 글이 거의 없는(포스터 이미지뿐인) 공고의 이미지를 무료 OCR(Tesseract)로 읽는다.

Tesseract가 설치돼 있지 않으면 조용히 건너뛴다.
  - GitHub Actions: 워크플로에서 설치함
  - Windows 로컬: https://github.com/UB-Mannheim/tesseract/wiki 에서 설치 (한국어 데이터 포함)
"""

from __future__ import annotations

import io
import re
import shutil

from crawler import Activity, LinkareerClient

MIN_BODY_CHARS = 200  # 본문이 이보다 짧으면 포스터 위주 공고로 보고 OCR
MAX_IMAGES = 3
OCR_MARK = "[포스터 OCR]"
TEMPLATE_RE = re.compile(r"^.{0,200}?입니다\.\s*(?:혜택으로는[^.]{0,120}?있습니다\.\s*)?(?:[^.!]{0,80}?지원해\s?주세요[!.]\s*)?")


def available() -> bool:
    if not shutil.which("tesseract"):
        return False
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def needs_ocr(a: Activity) -> bool:
    body = TEMPLATE_RE.sub("", a.description, count=1)
    return len(body.strip()) < MIN_BODY_CHARS and bool(a.image_urls) and OCR_MARK not in a.description


def read_posters(client: LinkareerClient, a: Activity) -> str:
    """포스터 이미지의 글자를 읽어 본문 뒤에 붙인다. 읽은 글자 수를 반환."""
    import pytesseract
    from PIL import Image

    texts = []
    for url in a.image_urls[:MAX_IMAGES]:
        try:
            data, _ = client.get_bytes(url)
            im = Image.open(io.BytesIO(data)).convert("L")
            if im.width < 1000:  # 작은 이미지는 키워야 한글 인식률이 오른다
                ratio = 1000 / im.width
                im = im.resize((1000, int(im.height * ratio)))
            texts.append(pytesseract.image_to_string(im, lang="kor+eng"))
        except Exception as e:  # noqa: BLE001 - 이미지 하나 실패는 무시
            print(f"  ! OCR 실패 {url}: {type(e).__name__}")
    text = " ".join(" ".join(texts).split())
    if text:
        a.description = f"{a.description} {OCR_MARK} {text}"[:12000]
    return len(text)
