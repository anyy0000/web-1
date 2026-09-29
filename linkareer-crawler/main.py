"""링커리어 식품/건강 공모전·대외활동 수집기.

사용 예:
  python main.py                 # 설정대로 실행 (SPREADSHEET_ID 있으면 구글시트, 없으면 엑셀)
  python main.py --excel-only    # 엑셀만
  python main.py --dry-run       # 저장하지 않고 결과만 출력
  python main.py --dump --pages 1  # 받은 HTML을 debug/ 에 저장 (구조 확인용)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

from crawler import KST, LinkareerClient
from filters import RelevanceFilter
from storage import ExcelStorage, SheetStorage

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=ROOT / "config.yaml", type=Path)
    parser.add_argument("--pages", type=int, help="목록 페이지 수 (설정값 덮어쓰기)")
    parser.add_argument("--excel", default=ROOT / "output" / "linkareer.xlsx", type=Path)
    parser.add_argument("--excel-only", action="store_true", help="구글시트 연동 없이 엑셀만 저장")
    parser.add_argument("--dry-run", action="store_true", help="저장 없이 결과만 출력")
    parser.add_argument("--dump", action="store_true", help="받은 HTML을 debug/ 폴더에 저장")
    parser.add_argument("--no-detail", action="store_true", help="상세 페이지 요청 생략")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    crawl_cfg = config.get("crawl", {})
    pages = args.pages or crawl_cfg.get("pages_per_list", 5)
    fetch_detail = crawl_cfg.get("fetch_detail", True) and not args.no_detail
    today = datetime.now(KST).date()

    storages = []
    if not args.dry_run:
        sheet_id = os.environ.get("SPREADSHEET_ID")
        if sheet_id and not args.excel_only:
            storages.append(SheetStorage(sheet_id))
        storages.append(ExcelStorage(args.excel))
    seen: set[str] = set().union(*(s.seen_ids() for s in storages)) if storages else set()
    print(f"이미 처리한 공고: {len(seen)}건")

    client = LinkareerClient(
        delay_sec=crawl_cfg.get("request_delay_sec", 1.5),
        dump_dir=ROOT / "debug" if args.dump else None,
    )
    relevance = RelevanceFilter(config)

    rows, seen_rows = [], []
    stats = {"목록": 0, "신규": 0, "마감": 0, "추천": 0, "검토": 0, "제외": 0, "오류": 0}
    for kind, path in crawl_cfg.get("lists", {}).items():
        print(f"[{kind}] 목록 수집 중...")
        try:
            items = client.crawl_list(kind, path, crawl_cfg.get("list_query", ""), pages)
        except Exception as e:  # noqa: BLE001 - 한 목록 실패가 전체를 막지 않도록
            print(f"  ! 목록 수집 실패: {e}", file=sys.stderr)
            stats["오류"] += 1
            continue
        stats["목록"] += len(items)

        for a in items:
            if a.id in seen:
                continue
            seen.add(a.id)
            stats["신규"] += 1
            if fetch_detail and not (a.close_date and a.close_date < today):
                try:
                    client.enrich(a)
                except Exception as e:  # noqa: BLE001
                    print(f"  ! 상세 실패 {a.url}: {e}", file=sys.stderr)
                    stats["오류"] += 1
                    continue  # _seen 에 기록하지 않음 → 다음 실행 때 재시도
            if a.close_date and a.close_date < today:
                stats["마감"] += 1
                seen_rows.append([a.id, today.isoformat(), "마감"])
                continue

            m = relevance.evaluate(a)
            stats[m.label] += 1
            if m.label == "제외":
                seen_rows.append([a.id, today.isoformat(), f"제외({m.score})"])
                continue
            print(f"  + [{m.label} {m.score}] {a.title} / {a.organizer or '-'}")
            rows.append([
                a.id, a.kind, m.label, m.score, a.title, a.organizer,
                ", ".join(a.categories), a.close_date, None,
                " | ".join(m.reasons), a.url, today.isoformat(), "", "",
            ])

    rows.sort(key=lambda r: (-r[3], r[7] or today))
    print("요약:", ", ".join(f"{k} {v}" for k, v in stats.items()))

    for s in storages:
        s.append(rows, seen_rows)
        print(f"저장 완료: {type(s).__name__} ({len(rows)}건 추가)")

    if stats["목록"] == 0:
        print("목록을 하나도 못 읽었습니다. 사이트 구조가 바뀌었거나 접속이 차단됐을 수 있습니다. "
              "--dump 로 HTML을 확인하세요.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
