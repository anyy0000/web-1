"""Claude가 요약할 수 있도록 공고 내용을 짧게 뽑아 보여준다 (API 없이 Claude Code 세션에서 사용).

사용법:
  python fetch_post.py 353100 353423 ...          # 공고 ID 여러 개
  python fetch_post.py --images-dir /tmp/posters 353100

- 본문은 링커리어 자동 문구를 빼고 최대 2500자까지 보여준다.
- 본문이 거의 없는(포스터 위주) 공고만 포스터 이미지를 저장하고 경로를 출력한다.
  → Claude가 그 이미지를 직접 열어 읽는다. 글이 충분한 공고는 이미지를 받지 않아 사용량을 아낀다.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

from crawler import Activity, LinkareerClient

MAX_BODY = 2500
POSTER_ONLY_CHARS = 300
TEMPLATE_RE = re.compile(r"^.{0,200}?입니다\.\s*(?:혜택으로는[^.]{0,120}?있습니다\.\s*)?(?:[^.!]{0,80}?지원해\s?주세요[!.]\s*)?")


def save_images(client: LinkareerClient, a: Activity, out_dir: Path) -> list[Path]:
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for n, url in enumerate(a.image_urls[:2]):
        try:
            data, _ = client.get_bytes(url)
            im = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            print(f"  (이미지 실패: {type(e).__name__})")
            continue
        w, h = im.size
        step = max(w * 2, 1)  # 세로로 긴 포스터는 1:2 조각으로 (최대 3조각)
        for k, top in enumerate(range(0, h, step)):
            if k >= 3:
                break
            piece = im.crop((0, top, w, min(h, top + step)))
            piece.thumbnail((1200, 1200))
            path = out_dir / f"{a.id}_{n}_{k}.jpg"
            piece.save(path, quality=80)
            paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="+")
    parser.add_argument("--images-dir", type=Path, default=Path("/tmp/linkareer_posters"))
    args = parser.parse_args()

    client = LinkareerClient(delay_sec=1.0)
    for aid in args.ids:
        a = Activity(id=str(aid), kind="")
        try:
            client.enrich(a)
        except Exception as e:  # noqa: BLE001 - 내려간 공고 등
            print(f"=== {aid} | 가져오기 실패: {e}\n")
            continue
        body = TEMPLATE_RE.sub("", a.description, count=1).strip()
        print(f"=== {a.id} | {a.title}")
        print(f"주최: {a.organizer or '-'} | 분야: {', '.join(a.categories) or '-'} | "
              f"대상(링커리어 표기): {', '.join(a.targets) or '-'} | 마감: {a.close_date or '-'}")
        print(f"본문: {body[:MAX_BODY] or '(없음)'}{' …(생략)' if len(body) > MAX_BODY else ''}")
        if len(body) < POSTER_ONLY_CHARS and a.image_urls:
            paths = save_images(client, a, args.images_dir)
            print("포스터 이미지(본문이 짧아 이미지를 직접 읽어야 함): " + ", ".join(map(str, paths)))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
