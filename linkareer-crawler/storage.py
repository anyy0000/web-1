"""결과 저장: Google 스프레드시트 / 엑셀.

두 저장소 모두
  - 메인 탭: 매칭된 공고 (기존 행은 건드리지 않고 신규만 아래에 추가 → 상태/메모 수정 보존)
  - "_seen" 탭: 이미 확인한 공고 ID (제외된 것 포함, 다음 실행 때 상세 페이지 재요청 방지)
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

HEADER = ["ID", "구분", "추천도", "점수", "제목", "주최", "분야", "마감일", "D-day",
          "매칭근거", "링크", "수집일", "상태", "메모"]
DEADLINE_COL = "H"  # 마감일 열 (D-day 수식이 참조)
SEEN_TAB = "_seen"


def dday_formula(row: int) -> str:
    return f'=IF({DEADLINE_COL}{row}="","",{DEADLINE_COL}{row}-TODAY())'


class SheetStorage:
    def __init__(self, spreadsheet_id: str, tab: str = "링커리어"):
        import gspread

        raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if raw:
            gc = gspread.service_account_from_dict(json.loads(raw))
        else:
            gc = gspread.service_account(
                filename=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"))
        sh = gc.open_by_key(spreadsheet_id)
        self.ws = self._get_or_create(sh, tab, HEADER)
        self.seen_ws = self._get_or_create(sh, SEEN_TAB, ["ID", "처리일", "결과"])

    @staticmethod
    def _get_or_create(sh, title: str, header: list[str]):
        import gspread

        try:
            ws = sh.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=title, rows=1000, cols=len(header))
        if not ws.row_values(1):
            ws.update([header], "A1")
            ws.freeze(rows=1)
        return ws

    def seen_ids(self) -> set[str]:
        ids = set(self.ws.col_values(1)[1:]) | set(self.seen_ws.col_values(1)[1:])
        return {i for i in ids if i}

    def append(self, rows: list[list], seen: list[list]) -> None:
        if rows:
            start = len(self.ws.col_values(1)) + 1
            values = []
            for i, r in enumerate(rows):
                r = list(r)
                r[7] = r[7].isoformat() if isinstance(r[7], date) else r[7]
                r[8] = dday_formula(start + i)
                values.append(r)
            self.ws.append_rows(values, value_input_option="USER_ENTERED")
        if seen:
            self.seen_ws.append_rows(seen, value_input_option="RAW")


class ExcelStorage:
    def __init__(self, path: Path, tab: str = "링커리어"):
        from openpyxl import Workbook, load_workbook

        self.path = path
        self.tab = tab
        if path.exists():
            self.wb = load_workbook(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.wb = Workbook()
            self.wb.active.title = tab
        self.ws = self._sheet(tab, HEADER)
        self.seen_ws = self._sheet(SEEN_TAB, ["ID", "처리일", "결과"])

    def _sheet(self, title: str, header: list[str]):
        ws = self.wb[title] if title in self.wb.sheetnames else self.wb.create_sheet(title)
        if ws.max_row == 1 and ws.cell(1, 1).value is None:
            for col, name in enumerate(header, start=1):
                ws.cell(1, col, name)
            ws.freeze_panes = "A2"
        return ws

    def seen_ids(self) -> set[str]:
        ids = set()
        for ws in (self.ws, self.seen_ws):
            for (value,) in ws.iter_rows(min_row=2, max_col=1, values_only=True):
                if value:
                    ids.add(str(value))
        return ids

    def append(self, rows: list[list], seen: list[list]) -> None:
        for r in rows:
            r = list(r)
            row_no = self.ws.max_row + 1
            r[8] = dday_formula(row_no)
            self.ws.append(r)
            self.ws.cell(row_no, 8).number_format = "yyyy-mm-dd"
            self.ws.cell(row_no, 11).hyperlink = r[10]
        for s in seen:
            self.seen_ws.append(s)
        self.wb.save(self.path)
