"""'링커리어' 탭 서식 (가독성).

크롤러는 값만 다시 쓰므로 셀 서식은 유지되지만, 줄무늬·조건부 색은 매 실행 때 지우고 다시 건다(중복 방지).
build_requests() 는 Sheets batchUpdate 요청 목록을 만든다. 저장 시 SheetStorage 가 호출한다.

디자인 원칙
- 긴 글(제목·요약·준비할 것·추천이유)은 줄바꿈해서 옆 칸을 침범하지 않게, 짧은 값은 가운데 정렬
- 줄무늬 배경으로 행 구분, 머리글은 진한 색 + 고정
- 색은 '행동이 필요한 정보'에만: 구분(공모전/대외활동), 추천도, 마감 임박 D-day, 상태
"""

from __future__ import annotations

# 열 이름 → (너비 px, 정렬, 줄바꿈)
COLUMNS = {
    "구분": (72, "CENTER", "CLIP"),
    "D-day": (64, "CENTER", "CLIP"),
    "추천도": (64, "CENTER", "CLIP"),
    "제목": (260, "LEFT", "WRAP"),
    "주최": (150, "LEFT", "WRAP"),
    "기관유형": (104, "CENTER", "WRAP"),
    "분야": (150, "LEFT", "WRAP"),
    "마감일": (92, "CENTER", "CLIP"),
    "내용요약": (300, "LEFT", "WRAP"),
    "준비할 것": (280, "LEFT", "WRAP"),
    "참가대상": (150, "LEFT", "WRAP"),
    "추천이유": (220, "LEFT", "WRAP"),
    "시상(만원)": (80, "RIGHT", "CLIP"),
    "링크": (110, "LEFT", "CLIP"),
    "상태": (84, "CENTER", "CLIP"),
    "메모": (180, "LEFT", "WRAP"),
    "수집일": (72, "CENTER", "CLIP"),
    "ID": (70, "CENTER", "CLIP"),
}
NUMBER_FORMATS = {
    "D-day": ('"D-"0;"D+"0;"D-day"', "NUMBER"),   # 12 → D-12, 0 → D-day, -1 → D+1
    "마감일": ("m/d (ddd)", "DATE"),              # 10/13 (화)
    "수집일": ("m/d", "DATE"),
    "시상(만원)": ('#,##0"만"', "NUMBER"),         # 1500 → 1,500만
}
STATUS_OPTIONS = ["관심", "지원예정", "지원완료", "패스"]
FROZEN_COLUMNS = 4  # 구분·D-day·추천도·제목은 옆으로 스크롤해도 보이게
FONT = "Arial"


def rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


HEADER_BG, HEADER_FG = rgb("#1F4E5F"), rgb("#FFFFFF")
BAND_1, BAND_2 = rgb("#FFFFFF"), rgb("#F3F6F8")


def build_requests(sheet_id: int, header: list[str], banding_ids: list[int],
                   n_conditional: int) -> list[dict]:
    """sheet_id 탭 서식 요청. banding_ids·n_conditional 은 지금 탭에 걸린 줄무늬 ID와 조건부 서식 개수(지우고 다시 건다)."""
    reqs: list[dict] = []
    ncols = len(header)
    col = {name: i for i, name in enumerate(header)}

    def rng(c0: int, c1: int, r0: int = 1, r1: int | None = None) -> dict:
        g = {"sheetId": sheet_id, "startRowIndex": r0, "startColumnIndex": c0, "endColumnIndex": c1}
        if r1 is not None:
            g["endRowIndex"] = r1
        return g

    # 0) 이전 줄무늬·조건부 서식 제거 (다시 걸 때 겹치지 않게)
    reqs += [{"deleteBanding": {"bandedRangeId": b}} for b in banding_ids]
    reqs += [{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}} for _ in range(n_conditional)]

    # 1) 고정, 머리글 높이
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": FROZEN_COLUMNS}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 36}, "fields": "pixelSize"}})

    # 2) 전체 본문 기본값: 글꼴, 세로 가운데, 여백
    reqs.append({"repeatCell": {
        "range": rng(0, ncols),
        "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE",
                                       "textFormat": {"fontFamily": FONT, "fontSize": 10},
                                       "padding": {"top": 4, "bottom": 4, "left": 6, "right": 6}}},
        "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.textFormat.fontFamily,"
                  "userEnteredFormat.textFormat.fontSize,userEnteredFormat.padding"}})

    # 3) 머리글
    reqs.append({"repeatCell": {
        "range": rng(0, ncols, 0, 1),
        "cell": {"userEnteredFormat": {"backgroundColor": HEADER_BG, "horizontalAlignment": "CENTER",
                                       "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP",
                                       "textFormat": {"foregroundColor": HEADER_FG, "bold": True,
                                                      "fontFamily": FONT, "fontSize": 10}}},
        "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.horizontalAlignment,"
                  "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy,userEnteredFormat.textFormat"}})

    # 4) 열별 너비·정렬·줄바꿈·숫자 형식
    for name, i in col.items():
        width, align, wrap = COLUMNS.get(name, (140, "LEFT", "WRAP"))
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": width}, "fields": "pixelSize"}})
        fmt: dict = {"horizontalAlignment": align, "wrapStrategy": wrap}
        fields = "userEnteredFormat.horizontalAlignment,userEnteredFormat.wrapStrategy"
        if name in NUMBER_FORMATS:
            pattern, kind = NUMBER_FORMATS[name]
            fmt["numberFormat"] = {"type": kind, "pattern": pattern}
            fields += ",userEnteredFormat.numberFormat"
        reqs.append({"repeatCell": {"range": rng(i, i + 1), "cell": {"userEnteredFormat": fmt}, "fields": fields}})

    # 제목은 굵게, 추천이유·ID·수집일은 흐리게 (덜 중요한 정보)
    if "제목" in col:
        reqs.append({"repeatCell": {"range": rng(col["제목"], col["제목"] + 1),
                                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                                    "fields": "userEnteredFormat.textFormat.bold"}})
    for name in ("추천이유", "수집일", "ID", "링크"):
        if name in col:
            reqs.append({"repeatCell": {"range": rng(col[name], col[name] + 1),
                                        "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": rgb("#80868B"),
                                                                                      "fontSize": 9}}},
                                        "fields": "userEnteredFormat.textFormat.foregroundColor,"
                                                  "userEnteredFormat.textFormat.fontSize"}})

    # 5) 줄무늬 (새 행에도 자동 적용되도록 끝을 열어 둠)
    reqs.append({"addBanding": {"bandedRange": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": ncols},
        "rowProperties": {"headerColor": HEADER_BG, "firstBandColor": BAND_1, "secondBandColor": BAND_2}}}})

    # 6) 조건부 색 (앞에 있는 규칙이 우선)
    def cond(c: int, formula: str, bg: str | None = None, fg: str | None = None, bold: bool = False,
             whole_row: bool = False) -> None:
        fmt: dict = {}
        if bg:
            fmt["backgroundColor"] = rgb(bg)
        tf: dict = {}
        if fg:
            tf["foregroundColor"] = rgb(fg)
        if bold:
            tf["bold"] = True
        if tf:
            fmt["textFormat"] = tf
        target = rng(0, ncols) if whole_row else rng(c, c + 1)
        reqs.append({"addConditionalFormatRule": {"index": len([r for r in reqs if "addConditionalFormatRule" in r]),
                                                  "rule": {"ranges": [target], "booleanRule": {
                                                      "condition": {"type": "CUSTOM_FORMULA",
                                                                    "values": [{"userEnteredValue": formula}]},
                                                      "format": fmt}}}})

    def ref(name: str) -> str:  # 2행 기준 절대열 참조, 예: $O2
        return f"${chr(ord('A') + col[name])}2"

    if "상태" in col:  # 패스한 공고는 행 전체를 흐리게
        cond(0, f'={ref("상태")}="패스"', fg="#9AA0A6", whole_row=True)
    if "D-day" in col:
        d = ref("D-day")
        cond(col["D-day"], f"=AND(ISNUMBER({d}),{d}<=3)", bg="#FCE8E6", fg="#C5221F", bold=True)
        cond(col["D-day"], f"=AND(ISNUMBER({d}),{d}<=7)", bg="#FEF3E2", fg="#B45309", bold=True)
    if "추천도" in col:
        cond(col["추천도"], f'={ref("추천도")}="추천"', bg="#D5F0DD", fg="#0D652D", bold=True)
        cond(col["추천도"], f'={ref("추천도")}="검토"', bg="#FEF7E0", fg="#8A5A00")
    if "구분" in col:
        cond(col["구분"], f'={ref("구분")}="공모전"', bg="#E8F0FE", fg="#1A56B8", bold=True)
        cond(col["구분"], f'={ref("구분")}="대외활동"', bg="#F3E8FD", fg="#7B1FA2", bold=True)
    if "상태" in col:
        s = ref("상태")
        cond(col["상태"], f'={s}="지원완료"', bg="#D5F0DD", fg="#0D652D", bold=True)
        cond(col["상태"], f'={s}="지원예정"', bg="#E8F0FE", fg="#1A56B8", bold=True)
        cond(col["상태"], f'={s}="관심"', bg="#FEF7E0", fg="#8A5A00")
    for name in ("내용요약", "준비할 것"):  # 아직 자동 발췌 상태인 요약은 흐리게 (Claude 요약 전)
        if name in col:
            c = ref(name)
            cond(col[name], f'=OR(LEFT({c},4)="(발췌)",LEFT({c},8)="(포스터 OCR)")', fg="#9AA0A6")

    # 7) 상태 칸 드롭다운 (목록 밖 값도 허용)
    if "상태" in col:
        reqs.append({"setDataValidation": {"range": rng(col["상태"], col["상태"] + 1), "rule": {
            "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in STATUS_OPTIONS]},
            "showCustomUi": True, "strict": False}}})
    return reqs
