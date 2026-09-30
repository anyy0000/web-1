"""공고 내용 요약: 무엇에 대한 공모전인지 / 무엇을 준비해야 하는지 / 누가 지원할 수 있는지.

우선순위
1. GEMINI_API_KEY 가 있으면 Gemini(무료 등급 Flash)가 포스터 이미지 + 본문을 읽고 짧은 문장으로 정리
2. ANTHROPIC_API_KEY 가 있으면 Claude가 같은 방식으로 정리 (유료)
3. 둘 다 없거나 한도 초과면 본문에서 '주제', '제출물' 같은 항목을 잘라 넣는다 (무료 발췌)
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time

import requests

from crawler import Activity, LinkareerClient

MAX_IMAGES = 4          # 한 공고당 모델에 보내는 이미지 조각 수
MAX_LONG_EDGE = 1568    # 이보다 크면 모델 쪽에서 어차피 축소됨
MAX_TEXT_CHARS = 6000

SYSTEM_PROMPT = """너는 식품영양학과 대학생이 공모전·대외활동 목록을 훑어보며 고르는 것을 돕는다.
공고 포스터 이미지와 본문을 읽고, 스프레드시트 한 칸에 들어갈 만큼 짧고 쉬운 한국어로 정리한다.
- summary: 무엇을 하는 공모전/활동인지 한 문장. 50자 이내, 명사형으로 끝낸다.
  예) "국내산 양식 수산물로 만든 요리를 겨루는 대회", "당근잎차 브랜드 SNS 콘텐츠를 만드는 서포터즈"
- prepare: 지원할 때(또는 선발 후 활동으로) 실제로 해야 하는 일. 50자 이내.
  예) "요리 과정 영상(10분 이내) + 완성 사진 제출", "지원서 제출 후 4주간 릴스 제작"
- target: 참가 대상. 20자 이내. 예) "대학생", "누구나", "4학년 이상 재학·졸업생", "초등학생"
날짜·상금·문의처는 다른 칸에 있으니 넣지 않는다. 공고에 없는 내용은 추측하지 말고 "공고에 명시 없음"이라고 쓴다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "prepare": {"type": "string"},
        "target": {"type": "string"},
    },
    "required": ["summary", "prepare", "target"],
    "additionalProperties": False,
}
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_SCHEMA = {  # Gemini responseSchema 형식 (OpenAPI 부분집합)
    "type": "OBJECT",
    "properties": {k: {"type": "STRING"} for k in ("summary", "prepare", "target")},
    "required": ["summary", "prepare", "target"],
}


class QuotaExhausted(Exception):
    """무료 한도 소진. 이번 실행의 나머지는 무료 발췌로 채운다."""


class Summarizer:
    def __init__(self, client: LinkareerClient, config: dict):
        self.client = client
        cfg = config.get("summary", {})
        self.model = cfg.get("model", "claude-opus-5-5")
        self.max_per_run = cfg.get("max_per_run", 40)
        self.used = 0
        self.llm = None
        self.gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.gemini_models = list(cfg.get("gemini_models", ["gemini-flash-latest", "gemini-2.5-flash"]))
        self.gemini_interval = float(cfg.get("gemini_interval_sec", 7))  # 무료 등급 분당 요청 제한 대비
        self._gemini_model: str | None = None
        self._last_call = 0.0
        if self.gemini_key:
            self.llm = "gemini"
        elif os.environ.get("ANTHROPIC_API_KEY") and cfg.get("use_claude", True):
            import anthropic

            self.llm = anthropic.Anthropic()

    @property
    def uses_llm(self) -> bool:
        return self.llm is not None

    @property
    def mode(self) -> str:
        if self.llm == "gemini":
            return "Gemini(무료 등급)"
        return f"Claude({self.model})" if self.llm else "본문 발췌(무료)"

    def summarize(self, a: Activity) -> tuple[str, str, str]:
        """(내용요약, 준비할 것, 참가대상)"""
        if self.llm and self.used < self.max_per_run:
            self.used += 1
            try:
                return self._gemini(a) if self.llm == "gemini" else self._claude(a)
            except QuotaExhausted as e:
                print(f"  ! Gemini 무료 한도 소진 → 이번 실행은 발췌로 채움 ({e})")
                self.llm = None
            except Exception as e:  # noqa: BLE001 - 요약 실패가 수집 전체를 막지 않도록
                print(f"  ! 요약 실패 {a.url}: {type(e).__name__}: {str(e)[:200]}")
                return "", "", ""  # 비워 두면 다음 실행 때 다시 시도
        label = "(포스터 OCR)" if "[포스터 OCR]" in a.description else "(발췌)"
        summary, prepare = excerpt(a.description.replace("[포스터 OCR]", " "), a.title, label)
        return summary, prepare, ", ".join(a.targets)

    # ------------------------------------------------------------------ Gemini
    def _gemini(self, a: Activity) -> tuple[str, str, str]:
        parts: list[dict] = []
        for url in a.image_urls[:3]:
            try:
                data, _ = self.client.get_bytes(url)
                parts += [{"inline_data": {"mime_type": "image/jpeg", "data": base64.standard_b64encode(j).decode()}}
                          for j in _jpeg_pieces(data)]
            except Exception as e:  # noqa: BLE001
                print(f"  ! 이미지 실패 {url}: {e}")
        parts = parts[:MAX_IMAGES]
        parts.append({"text": _post_text(a)})
        body = {
            "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": GEMINI_SCHEMA,
                                 "temperature": 0.2},
        }
        data = self._gemini_call(body)
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            print(f"  ! Gemini 응답 형식 오류 {a.url}: {cand.get('finishReason')}")
            return "", "", ""
        return tuple(str(out.get(k, "")).strip() for k in ("summary", "prepare", "target"))

    def _gemini_call(self, body: dict) -> dict:
        headers = {"x-goog-api-key": self.gemini_key}
        models = [self._gemini_model] if self._gemini_model else self.gemini_models
        for attempt in range(2):
            for model in models:
                wait = self.gemini_interval - (time.monotonic() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
                r = requests.post(f"{GEMINI_BASE}/models/{model}:generateContent",
                                  headers=headers, json=body, timeout=90)
                if r.status_code == 200:
                    if self._gemini_model != model:
                        print(f"  · Gemini 모델: {model}")
                    self._gemini_model = model
                    return r.json()
                if r.status_code == 404:  # 모델 이름이 바뀌었거나 없어짐 → 다음 후보
                    continue
                if r.status_code == 429:
                    if attempt == 0:
                        time.sleep(40)  # 분당 한도일 수 있으니 한 번 쉬었다가 재시도
                        break
                    raise QuotaExhausted(r.text[:200])
                r.raise_for_status()
            else:
                discovered = self._discover_flash_model()
                if not discovered or discovered in models:
                    raise RuntimeError("사용 가능한 Gemini Flash 모델을 찾지 못했습니다")
                models = [discovered]
        raise QuotaExhausted("재시도 후에도 한도 초과")

    def _discover_flash_model(self) -> str | None:
        """설정한 모델이 모두 없으면, API에 모델 목록을 물어 최신 Flash 모델을 고른다."""
        r = requests.get(f"{GEMINI_BASE}/models", headers={"x-goog-api-key": self.gemini_key},
                         params={"pageSize": 200}, timeout=30)
        r.raise_for_status()
        names = [m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])
                 and "flash" in m["name"] and not any(x in m["name"] for x in ("lite", "image", "tts", "live", "exp"))]
        names.sort(key=lambda n: ("preview" in n, [-int(x) for x in re.findall(r"\d+", n)[:2]]))
        return names[0] if names else None

    # ------------------------------------------------------------------ Claude

    def _claude(self, a: Activity) -> tuple[str, str, str]:
        import anthropic

        content: list[dict] = []
        for url in a.image_urls[:3]:
            try:
                data, _ = self.client.get_bytes(url)
                content += _image_blocks(data)
            except Exception as e:  # noqa: BLE001
                print(f"  ! 이미지 실패 {url}: {e}")
        content = content[:MAX_IMAGES]
        content.append({"type": "text", "text": _post_text(a)})

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
            return "", "", ""
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return "", "", ""
        return tuple(str(data.get(k, "")).strip() for k in ("summary", "prepare", "target"))


def _post_text(a: Activity) -> str:
    body = a.description.replace("[포스터 OCR]", "\n[포스터 글자 인식 결과 - 오타 있음]\n")
    return (f"제목: {a.title}\n주최: {a.organizer}\n구분: {a.kind}\n분야: {', '.join(a.categories)}\n"
            f"링커리어 표기 대상: {', '.join(a.targets) or '-'}\n\n"
            f"본문:\n{body[:MAX_TEXT_CHARS] or '(본문 텍스트 없음 - 이미지 참고)'}")


def _jpeg_pieces(data: bytes) -> list[bytes]:
    return [base64.standard_b64decode(b["source"]["data"]) for b in _image_blocks(data)]


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


# 우선순위 순서. 앞쪽 항목이 있으면 그걸 쓴다.
SUMMARY_KEYS = ["공모 주제", "공모주제", "주제", "공모 내용", "공모내용", "공모 부문", "공모부문", "공모 분야",
                "모집 분야", "모집분야", "활동 내용", "활동내용", "주요 활동", "대회 소개", "행사 소개",
                "프로그램 소개", "활동 소개", "사업 소개", "담당업무", "교육 내용", "개요", "소개"]
PREPARE_KEYS = ["필수 제출", "제출물", "제출 서류", "제출서류", "제출 규격", "제출 자료", "출품 가이드", "출품 규격",
                "참여 방법", "참가 방법", "응모 방법", "지원 방법", "접수 방법", "신청 방법", "활동 미션",
                "주요 활동", "활동 내용", "선발 절차", "전형 절차", "지원 자격", "참가 자격", "모집 대상"]
SECTION_RE = re.compile(r"[■◆◇▶▷●○□◎★☆✅✔❗▣◉]|【|\[|<|[\U0001F300-\U0001FAFF]")
# 링커리어가 자동으로 붙이는 첫 문장 ("…입니다. 혜택으로는 … 등이 있습니다. …지원해주세요!")
TEMPLATE_RE = re.compile(r"^.{0,200}?입니다\.\s*(?:혜택으로는[^.]{0,120}?있습니다\.\s*)?(?:[^.!]{0,80}?지원해\s?주세요[!.]\s*)?")
MAX_EXCERPT = 180
MIN_EXCERPT = 25  # 이보다 짧으면("릴스 서포터즈 10명") 다음 항목을 찾는다
URL_RE = re.compile(r"https?://\S+")


def excerpt(text: str, title: str = "", label: str = "(발췌)") -> tuple[str, str]:
    """본문을 ■, [ ] 같은 제목 단위로 나눠 '주제'와 '제출/참여 방법' 항목을 찾는다 (무료, 규칙 기반)."""
    body = TEMPLATE_RE.sub("", text, count=1).strip()
    if title and body.startswith(title):
        body = body[len(title):].strip()
    sections = []
    for seg in SECTION_RE.split(body):
        seg = seg.strip(" :：-]】>")
        if seg:
            sections.append(seg)

    def after_key(text: str, key: str) -> str | None:
        """key 바로 뒤가 글자로 이어지면('주제로 진행…') 제목이 아니라 문장 속 단어이므로 버린다."""
        i = text.find(key)
        while i >= 0:
            rest = text[i + len(key):]
            if not rest or not ("가" <= rest[0] <= "힣"):
                return rest.lstrip(" :：]】>-)")
            i = text.find(key, i + 1)
        return None

    def good(content: str | None, skip: str) -> str:
        if content is None:
            return ""
        clipped = _clip(URL_RE.sub(" ", content))
        return clipped if len(clipped) >= MIN_EXCERPT and clipped != skip else ""

    def find(keys: list[str], skip: str = "") -> str:
        for key in keys:
            for seg in sections:
                if key in seg[:len(key) + 4]:
                    found = good(after_key(seg, key), skip)
                    if found:
                        return found
        return ""

    def find_anywhere(keys: list[str], skip: str = "") -> str:  # OCR 글처럼 구분 기호가 없는 경우
        for key in keys:
            for variant in {key, key.replace(" ", "")}:
                found = good(after_key(body, variant), skip)
                if found:
                    return found
        return ""

    summary = find(SUMMARY_KEYS) or find_anywhere(SUMMARY_KEYS[:8])
    prepare = find(PREPARE_KEYS, skip=summary) or find_anywhere(PREPARE_KEYS[:14], skip=summary)
    if not summary:  # 제목 항목이 없으면 본문 앞부분
        summary = good(body, "")
    return (f"{label} {summary}" if summary else "", f"{label} {prepare}" if prepare else "")


def _clip(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= MAX_EXCERPT:
        return text
    cut = text[:MAX_EXCERPT]
    for mark in (". ", "다. ", " - ", " • ", " "):
        i = cut.rfind(mark)
        if i > MAX_EXCERPT * 0.5:
            return cut[:i + len(mark)].strip() + "…"
    return cut + "…"
