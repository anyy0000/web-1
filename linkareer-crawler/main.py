"""링커리어 식품/건강 공모전·대외활동 수집기.

사용 예:
  python main.py                 # SPREADSHEET_ID 있으면 구글시트, 없으면 엑셀(output/linkareer.xlsx)
  python main.py --excel-only    # 엑셀만
  python main.py --dry-run       # 저장하지 않고 결과만 출력
  python main.py --dump --pages 1  # 받은 HTML을 debug/ 에 저장 (구조 확인용)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

from crawler import KST, Activity, LinkareerClient
from filters import RelevanceFilter
from storage import ExcelStorage, SheetStorage, merge_rows, to_date
from summarizer import Summarizer
import ocr

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
    primary = storages[0] if storages else None  # 구글시트가 있으면 시트가 원본
    existing = primary.load_rows() if primary else []
    if isinstance(primary, SheetStorage):
        primary.seed_settings(config)
    settings = primary.load_settings() if primary else None
    seen: set[str] = set().union(*(s.seen_ids() for s in storages)) if storages else set()
    print(f"기존 목록 {len(existing)}건, 이미 확인한 공고 {len(seen)}건")
    if settings is not None:
        print("설정 탭 조건:", ", ".join(f"{k} {len(v)}개" for k, v in settings.items()) or "없음")

    # 설정(검색 조건)이 바뀌었으면, 예전 조건으로 '제외'됐던 공고 중 아직 모집 중인 것을 다시 검사한다
    fingerprint = hashlib.md5(json.dumps(settings if settings is not None else config, sort_keys=True,
                                         ensure_ascii=False, default=str).encode()).hexdigest()[:12]
    recheck: set[str] = set()
    if primary and primary.get_fingerprint() != fingerprint:
        recheck = {i for i, res in primary.seen_results().items() if res.startswith("제외")}
        if recheck:
            print(f"검색 조건이 바뀌어, 이전에 제외된 공고 {len(recheck)}건 중 모집 중인 것을 다시 검사합니다")

    client = LinkareerClient(
        delay_sec=crawl_cfg.get("request_delay_sec", 1.5),
        dump_dir=ROOT / "debug" if args.dump else None,
    )
    relevance = RelevanceFilter(config, settings)
    summarizer = Summarizer(client, config)
    use_ocr = config.get("crawl", {}).get("poster_ocr", True) and ocr.available()
    print(f"요약 방식: {summarizer.mode}, 포스터 OCR: {'사용' if use_ocr else '미설치/꺼짐'}")

    new_rows, seen_rows = [], []
    stats = {"목록": 0, "신규": 0, "재검사": 0, "추천": 0, "검토": 0, "제외": 0, "오류": 0}
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
            rechecking = a.id in recheck
            if a.id in seen and not rechecking:
                continue
            seen.add(a.id)
            recheck.discard(a.id)  # 두 목록에 같은 공고가 있어도 한 번만
            stats["재검사" if rechecking else "신규"] += 1
            if fetch_detail:
                try:
                    client.enrich(a)
                except Exception as e:  # noqa: BLE001
                    print(f"  ! 상세 실패 {a.url}: {e}", file=sys.stderr)
                    stats["오류"] += 1
                    continue  # _seen 에 기록하지 않음 → 다음 실행 때 재시도
                if use_ocr and ocr.needs_ocr(a):
                    print(f"  · 포스터 OCR: {a.title[:30]} ({ocr.read_posters(client, a)}자)")
            if a.close_date and a.close_date < today:
                seen_rows.append([a.id, today.isoformat(), "마감"])
                continue
            m = relevance.evaluate(a)
            stats[m.label] += 1
            if m.label == "제외":
                if not rechecking:  # 재검사에서 또 제외면 _seen 에 중복 기록하지 않음
                    seen_rows.append([a.id, today.isoformat(), f"제외({m.score})"])
                continue
            if rechecking:
                print(f"  ↺ 재검사로 새로 포함: {a.title}")
            summary, prepare, target = summarizer.summarize(a)
            print(f"  + [{m.label}] {a.title} / {a.organizer or '-'}")
            new_rows.append(to_row(a, m, summary, prepare, target, today))

    backfill(existing, client, relevance, summarizer, config, use_ocr)
    print("요약:", ", ".join(f"{k} {v}" for k, v in stats.items()))

    rows = merge_rows(existing, new_rows)
    # 마감일이 지난 공고는 목록에서 뺀다 (_seen 에 남겨 다시 들어오지 않게)
    expired = [r for r in rows if (d := to_date(r.get("마감일"))) and d < today]
    if expired:
        expired_ids = {r["ID"] for r in expired}
        rows = [r for r in rows if r["ID"] not in expired_ids]
        seen_rows += [[r["ID"], today.isoformat(), "마감(목록에서 삭제)"] for r in expired]
        print(f"마감 지난 공고 {len(expired)}건 삭제")
    for s in storages:
        s.save(rows, seen_rows)
        print(f"저장 완료: {type(s).__name__} (신규 {len(new_rows)}건, 전체 {len(rows)}건)")
    if primary and stats["목록"] and not stats["오류"]:
        primary.set_fingerprint(fingerprint)  # 목록을 끝까지 다 본 실행에서만 갱신

    if stats["목록"] == 0:
        print("목록을 하나도 못 읽었습니다. 사이트 구조가 바뀌었거나 접속이 차단됐을 수 있습니다. "
              "--dump 로 HTML을 확인하세요.", file=sys.stderr)
        return 1
    return 0


def to_row(a: Activity, m, summary: str, prepare: str, target: str, today) -> dict:
    return {
        "ID": a.id, "구분": a.kind, "추천도": m.label, "제목": a.title, "주최": a.organizer,
        "기관유형": a.organization_type, "분야": ", ".join(a.categories), "마감일": a.close_date,
        "내용요약": summary, "준비할 것": prepare, "참가대상": target, "추천이유": " / ".join(m.reasons),
        "시상(만원)": a.reward or "", "링크": a.url, "수집일": today.isoformat(),
    }


def backfill(rows: list[dict], client, relevance, summarizer, config, use_ocr: bool) -> None:
    """요약이 비어 있는 기존 행을 채우고, 추천이유를 현재 기준 문구로 갱신한다 (추천도는 유지)."""
    limit = config.get("summary", {}).get("backfill_per_run", 30)
    today = datetime.now(KST).date()
    def needs(r):  # 요약이 없거나, 무료 발췌만 있는데 지금은 AI 요약을 쓸 수 있는 경우
        text = str(r.get("내용요약", "")).strip()
        return not text or (summarizer.uses_llm and text.startswith(("(발췌)", "(포스터 OCR)")))

    targets = [r for r in rows if needs(r) and (to_date(r.get("마감일")) or today) >= today][:limit]
    if not targets:
        return
    print(f"기존 행 요약 보강: {len(targets)}건")
    for r in targets:
        a = Activity(id=r["ID"], kind=str(r.get("구분", "")))
        try:
            client.enrich(a)
        except Exception as e:  # noqa: BLE001 - 마감돼 내려간 공고 등
            print(f"  ! 상세 실패 {a.url}: {e}", file=sys.stderr)
            continue
        if use_ocr and ocr.needs_ocr(a):
            ocr.read_posters(client, a)
        m = relevance.evaluate(a)
        summary, prepare, target = summarizer.summarize(a)
        for key, value in (("내용요약", summary), ("준비할 것", prepare), ("참가대상", target)):
            if value:  # 새 값이 비면 기존 값을 지우지 않는다
                r[key] = value
        r["추천이유"] = " / ".join(m.reasons)
        for key, value in (("기관유형", a.organization_type), ("분야", ", ".join(a.categories)),
                           ("시상(만원)", a.reward or "")):
            if value and not r.get(key):
                r[key] = value


if __name__ == "__main__":
    sys.exit(main())
