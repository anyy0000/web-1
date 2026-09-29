"""결과 저장: Google 스프레드시트 / 엑셀.

매 실행마다 기존 행을 읽어 신규 행과 합친 뒤, 구분 → 마감일(D-day) 순으로 정렬해 다시 쓴다.
상태/메모처럼 사람이 적은 칸과 모르는 열도 그대로 보존한다.
  - 메인 탭 "링커리어": 매칭된 공고
  - "_seen" 탭: 이미 확인한 공고 ID (제외된 것 포함 → 다음 실행 때 다시 읽지 않음)
  - "설정" 탭 (구글시트만): 관심기업/관심분야/키워드를 시트에서 직접 추가
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

HEADER = ["구분", "D-day", "추천도", "제목", "주최", "기관유형", "분야", "마감일",
          "내용요약", "준비할 것", "추천이유", "시상(만원)", "링크", "상태", "메모", "수집일", "ID"]
RENAMED = {"매칭근거": "추천이유"}  # 이전 버전 열 이름
DROPPED = {"점수"}
DATE_COLS = {"마감일", "수집일"}
DEADLINE_COL = chr(ord("A") + HEADER.index("마감일"))
MAIN_TAB = "링커리어"
SEEN_TAB = "_seen"
SETTINGS_TAB = "설정"
SETTINGS_HEADER = ["구분", "값", "설명"]
SETTINGS_DEFAULT = [
    ["관심분야", "요리/식품", "링커리어 분야명과 똑같이 적기. 일치하면 +4점(추천)"],
    ["참고분야", "의료/보건", "일치하면 +2점(검토). 다른 조건과 합쳐 4점 이상이면 추천"],
    ["참고분야", "체육/헬스", ""],
    ["관심기업", "", "기업·기관명(일부만 적어도 됨). 이 곳이 주최하면 추천"],
    ["키워드", "", "제목에 있으면 +2점, 본문에 있으면 +1점 (4점 이상 추천, 2~3점 검토)"],
    ["제외키워드", "", "제목·본문에 있으면 -3점"],
    ["제외기업", "", "이 주최사의 공고는 항상 제외"],
]
# 행 위치와 무관하게 동작 (정렬해도 깨지지 않음)
DDAY_FORMULA = f'=IF(INDIRECT("{DEADLINE_COL}"&ROW())="","",DAYS(INDIRECT("{DEADLINE_COL}"&ROW()),TODAY()))'


def to_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < value < 80000:  # 스프레드시트 날짜 일련번호
        return date(1899, 12, 30) + timedelta(days=int(value))
    if isinstance(value, str) and value.strip():
        for fmt in ("%Y-%m-%d", "%Y. %m. %d", "%Y.%m.%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(value.strip()[:12].rstrip("."), fmt).date()
            except ValueError:
                pass
    return None


def rows_from_table(values: list[list]) -> list[dict]:
    if not values:
        return []
    header = [RENAMED.get(str(h), str(h)) for h in values[0]]
    rows = []
    for raw in values[1:]:
        row = {h: v for h, v in zip(header, raw) if h and h not in DROPPED}
        if row.get("ID"):
            row["ID"] = str(row["ID"])
            rows.append(row)
    return rows


def build_table(rows: list[dict]) -> tuple[list[str], list[list]]:
    extra = []  # 사용자가 직접 추가한 열
    for r in rows:
        extra += [k for k in r if k not in HEADER and k not in extra]
    header = HEADER + extra
    far, today = date(9999, 12, 31), date.today()

    def sort_key(r):  # 구분 → 진행 중(D-day 가까운 순) → 마감된 것은 맨 아래
        d = to_date(r.get("마감일")) or far
        return (str(r.get("구분", "")), d < today, d if d >= today else date.max - (d - date.min))

    rows = sorted(rows, key=sort_key)
    table = []
    for r in rows:
        line = []
        for h in header:
            v = r.get(h, "")
            if h == "D-day":
                v = DDAY_FORMULA
            elif h in DATE_COLS:
                v = to_date(v) or v
            line.append("" if v is None else v)
        table.append(line)
    return header, table


def merge_rows(existing: list[dict], new: list[dict]) -> list[dict]:
    """신규 행 추가 + 기존 행의 빈 칸(요약 등)만 보강. 사람이 적은 값은 덮어쓰지 않는다."""
    by_id = {r["ID"]: r for r in existing}
    for r in new:
        old = by_id.get(r["ID"])
        if old is None:
            by_id[r["ID"]] = r
        else:
            for k, v in r.items():
                if v not in (None, "") and old.get(k) in (None, ""):
                    old[k] = v
    return list(by_id.values())


class SheetStorage:
    def __init__(self, spreadsheet_id: str):
        import gspread

        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if raw:
            gc = gspread.service_account_from_dict(json.loads(raw))
        else:
            gc = gspread.service_account(
                filename=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
        self.sh = gc.open_by_key(spreadsheet_id)
        self.ws = self._get_or_create(MAIN_TAB, HEADER)
        self.seen_ws = self._get_or_create(SEEN_TAB, ["ID", "처리일", "결과"])
        self.settings_ws = self._get_or_create(SETTINGS_TAB, SETTINGS_HEADER, SETTINGS_DEFAULT)

    def _get_or_create(self, title: str, header: list[str], defaults: list[list] | None = None):
        import gspread

        try:
            ws = self.sh.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(title=title, rows=1000, cols=max(len(header), 3))
        if not ws.row_values(1):
            ws.update([header] + (defaults or []), "A1")
            ws.freeze(rows=1)
        return ws

    def load_rows(self) -> list[dict]:
        return rows_from_table(self.ws.get_all_values(value_render_option="UNFORMATTED_VALUE"))

    def seen_ids(self) -> set[str]:
        return {r["ID"] for r in self.load_rows()} | {str(i) for i in self.seen_ws.col_values(1)[1:] if i}

    def load_settings(self) -> dict[str, list[str]]:
        settings: dict[str, list[str]] = {}
        for row in self.settings_ws.get_all_values()[1:]:
            if len(row) >= 2 and row[0].strip() and row[1].strip():
                settings.setdefault(row[0].strip(), []).append(row[1].strip())
        return settings

    def save(self, rows: list[dict], seen: list[list]) -> None:
        header, table = build_table(rows)
        for line in table:
            for i, v in enumerate(line):
                if isinstance(v, date):
                    line[i] = v.isoformat()
        self.ws.resize(rows=max(self.ws.row_count, len(table) + 50), cols=max(self.ws.col_count, len(header)))
        self.ws.batch_clear(["A2:ZZ"])
        self.ws.update([header] + table, "A1", value_input_option="USER_ENTERED")
        if seen:
            self.seen_ws.append_rows(seen, value_input_option="RAW")


class ExcelStorage:
    def __init__(self, path: Path):
        from openpyxl import Workbook, load_workbook

        self.path = path
        if path.exists():
            self.wb = load_workbook(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.wb = Workbook()
            self.wb.active.title = MAIN_TAB
        self.ws = self._sheet(MAIN_TAB, HEADER)
        self.seen_ws = self._sheet(SEEN_TAB, ["ID", "처리일", "결과"])

    def _sheet(self, title: str, header: list[str]):
        ws = self.wb[title] if title in self.wb.sheetnames else self.wb.create_sheet(title)
        if ws.max_row == 1 and ws.cell(1, 1).value is None:
            for col, name in enumerate(header, start=1):
                ws.cell(1, col, name)
            ws.freeze_panes = "A2"
        return ws

    def load_rows(self) -> list[dict]:
        return rows_from_table([list(r) for r in self.ws.iter_rows(values_only=True)])

    def seen_ids(self) -> set[str]:
        ids = {r["ID"] for r in self.load_rows()}
        for (value,) in self.seen_ws.iter_rows(min_row=2, max_col=1, values_only=True):
            if value:
                ids.add(str(value))
        return ids

    def load_settings(self) -> dict[str, list[str]]:
        return {}

    def save(self, rows: list[dict], seen: list[list]) -> None:
        header, table = build_table(rows)
        self.ws.delete_rows(1, self.ws.max_row)
        self.ws.append(header)
        link_col = header.index("링크") + 1
        for i, line in enumerate(table, start=2):
            self.ws.append(line)
            for name in DATE_COLS:
                self.ws.cell(i, header.index(name) + 1).number_format = "yyyy-mm-dd"
            if line[link_col - 1]:
                self.ws.cell(i, link_col).hyperlink = line[link_col - 1]
        for s in seen:
            self.seen_ws.append(s)
        self.wb.save(self.path)
