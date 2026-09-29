"""공고 내용 요약: "무엇에 대한 공모전인지"와 "무엇을 준비해야 하는지".

- ANTHROPIC_API_KEY 가 있으면 Claude가 포스터 이미지 + 본문을 읽고 요약한다 (유료, 건당 수십 원).
- 없으면 본문에서 '주제', '제출물' 같은 항목 근처 문장을 잘라 넣는다 (무료, 포스터만 있는 공고는 비어 있음).
"""

from __future__ import annotations

import base64
import io
import json
import os
import re

from crawler import Activity, LinkareerClient

MAX_IMAGES = 4          # 한 공고당 모델에 보내는 이미지 조각 수
MAX_LONG_EDGE = 1568    # 이보다 크면 모델 쪽에서 어차피 축소됨
MAX_TEXT_CHARS = 6000

SYSTEM_PROMPT = """너는 식품영양학과 대학생이 공모전·대외활동을 고르는 것을 돕는다.
공고 포스터 이미지와 본문을 읽고 한국어로 짧게 정리한다.
- summary: 무엇에 대한 공모전/활동인지 (주제, 목적, 누가 주최하는 어떤 성격인지). 2문장 이내.
- prepare: 지원자가 실제로 준비·제출해야 하는 것 (예: 기획서 5p, 1분 영상, 레시피+사진, 서류/면접, 활동 기간 중 SNS 콘텐츠 월 2회). 1~2문장.
공고에 없는 내용은 추측하지 말고 "공고에 명시 없음"이라고 쓴다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "prepare": {"type": "string"},
    },
    "required": ["summary", "prepare"],
    "additionalProperties": False,
}


class Summarizer:
    def __init__(self, client: LinkareerClient, config: dict):
        self.client = client
        cfg = config.get("summary", {})
        self.model = cfg.get("model", "claude-opus-5-5")
        self.max_per_run = cfg.get("max_per_run", 40)
        self.used = 0
        self.llm = None
        if os.environ.get("ANTHROPIC_API_KEY") and cfg.get("use_claude", True):
            import anthropic

            self.llm = anthropic.Anthropic()

    @property
    def mode(self) -> str:
        return f"Claude({self.model})" if self.llm else "본문 발췌(무료)"

    def summarize(self, a: Activity) -> tuple[str, str]:
        if self.llm and self.used < self.max_per_run:
            self.used += 1
            try:
                return self._claude(a)
            except Exception as e:  # noqa: BLE001 - 요약 실패가 수집 전체를 막지 않도록
                print(f"  ! 요약 실패 {a.url}: {type(e).__name__}: {e}")
                return "", ""  # 비워 두면 다음 실행 때 다시 시도
        return excerpt(a.description)

    def _claude(self, a: Activity) -> tuple[str, str]:
        import anthropic

        content: list[dict] = []
        for url in a.image_urls[:3]:
            try:
                data, _ = self.client.get_bytes(url)
                content += _image_blocks(data)
            except Exception as e:  # noqa: BLE001
                print(f"  ! 이미지 실패 {url}: {e}")
        content = content[:MAX_IMAGES]
        content.append({"type": "text", "text": (
            f"제목: {a.title}\n주최: {a.organizer}\n구분: {a.kind}\n분야: {', '.join(a.categories)}\n\n"
            f"본문:\n{a.description[:MAX_TEXT_CHARS] or '(본문 텍스트 없음 - 이미지 참고)'}")})

        kwargs = dict(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        if self.model.startswith("claude-haiku"):
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": SCHEMA}}
            response = self.llm.messages.create(**kwargs)
        else:
            kwargs["output_config"] = {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
            response = self.llm.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)

        if response.stop_reason == "refusal":
            print(f"  ! 요약 거절됨 {a.url}")
            return "", ""
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return "", ""
        return data.get("summary", "").strip(), data.get("prepare", "").strip()


def _image_blocks(data: bytes) -> list[dict]:
    """세로로 긴 이미지는 잘라서 글씨가 뭉개지지 않게 한다."""
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    w, h = im.size
    pieces = []
    step = max(w * 2, 1)  # 가로:세로 1:2 단위로 자름
    for top in range(0, h, step):
        pieces.append(im.crop((0, top, w, min(h, top + step))))
        if len(pieces) >= MAX_IMAGES:
            break
    blocks = []
    for p in pieces:
        p.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE))
        buf = io.BytesIO()
        p.save(buf, format="JPEG", quality=85)
        blocks.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(buf.getvalue()).decode()}})
    return blocks


SUMMARY_HEADS = ["공모 주제", "공모주제", "주제", "공모 내용", "활동 내용", "활동내용", "모집 분야", "공모 분야", "개요"]
PREPARE_HEADS = ["필수 제출", "제출물", "제출 규격", "제출 자료", "제출 서류", "제출서류", "제출 방법", "접수 방법", "지원 방법", "신청 방법", "응모 방법", "활동 혜택"]


def excerpt(text: str) -> tuple[str, str]:
    """본문에서 항목 제목 뒤 문장을 잘라온다. 정확도는 낮지만 무료."""
    def grab(heads: list[str]) -> str:
        for h in heads:
            m = re.search(re.escape(h) + r"\s*[:：]?\s*(.{10,160}?)(?:\s[■◆▶●○□※•]|$)", text)
            if m:
                return "(발췌) " + m.group(1).strip()
        return ""
    return grab(SUMMARY_HEADS), grab(PREPARE_HEADS)
