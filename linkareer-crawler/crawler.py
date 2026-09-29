"""링커리어 목록/상세 페이지 수집.

링커리어는 Next.js + GraphQL(Apollo) 기반이라, HTML 태그보다
<script id="__NEXT_DATA__"> 안의 JSON(Apollo 캐시)을 읽는 쪽이 안정적이다.
JSON 스키마가 바뀌어도 버틸 수 있도록 필드명을 여러 후보로 찾는다.
JSON에서 못 찾으면 HTML의 /activity/{id} 링크 + 상세 페이지 메타태그로 대체한다.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://linkareer.com"
KST = ZoneInfo("Asia/Seoul")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}
ACTIVITY_LINK_RE = re.compile(r"/activity/(\d+)")


@dataclass
class Activity:
    id: str
    kind: str  # 공모전 / 대외활동
    title: str = ""
    organizer: str = ""
    organization_type: str = ""  # 대기업, 공공기관/공기업 등 (상세 페이지에만 있음)
    reward: int | None = None  # 시상 규모 (만원)
    categories: list[str] = field(default_factory=list)
    close_date: date | None = None
    view_count: int | None = None
    description: str = ""
    image_urls: list[str] = field(default_factory=list)  # 포스터 + 본문 이미지

    @property
    def url(self) -> str:
        return f"{BASE_URL}/activity/{self.id}"

    def merge(self, other: "Activity") -> None:
        """비어 있는 필드만 other 값으로 채운다."""
        self.title = self.title or other.title
        self.organizer = self.organizer or other.organizer
        self.organization_type = self.organization_type or other.organization_type
        self.reward = self.reward if self.reward is not None else other.reward
        self.categories = self.categories or other.categories
        self.close_date = self.close_date or other.close_date
        self.view_count = self.view_count if self.view_count is not None else other.view_count
        if len(other.description) > len(self.description):
            self.description = other.description
        self.image_urls = self.image_urls or other.image_urls


class LinkareerClient:
    def __init__(self, delay_sec: float = 1.5, dump_dir: Path | None = None):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay_sec = delay_sec
        self.dump_dir = dump_dir
        self._last_request = 0.0

    def get(self, url: str) -> str:
        wait = self.delay_sec - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        resp = self.session.get(url, timeout=20)
        self._last_request = time.monotonic()
        resp.raise_for_status()
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^\w.-]+", "_", url.replace(BASE_URL, ""))[:120] or "root"
            (self.dump_dir / f"{name}.html").write_text(resp.text, encoding="utf-8")
        return resp.text

    def crawl_list(self, kind: str, path: str, query: str, pages: int) -> list[Activity]:
        found: dict[str, Activity] = {}
        for page in range(1, pages + 1):
            sep = "&" if query else ""
            url = f"{BASE_URL}{path}?{query}{sep}page={page}"
            html = self.get(url)
            items = parse_page(html, kind)
            new_ids = [a.id for a in items if a.id not in found]
            print(f"  [{kind}] page {page}: {len(items)}건 (신규 {len(new_ids)})")
            if not new_ids:  # 페이지 파라미터가 무시되거나 마지막 페이지
                break
            for a in items:
                if a.id in found:
                    found[a.id].merge(a)
                else:
                    found[a.id] = a
        return list(found.values())

    def get_bytes(self, url: str) -> tuple[bytes, str]:
        """이미지 다운로드 (CDN이라 요청 간격 제한 없음)."""
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "")

    def enrich(self, activity: Activity) -> None:
        """상세 페이지에서 주최사/마감일/본문을 보강한다."""
        html = self.get(activity.url)
        detail = parse_detail(html, activity)
        activity.merge(detail)


# ---------------------------------------------------------------- parsing

def parse_page(html: str, kind: str) -> list[Activity]:
    """목록 페이지 HTML → Activity 목록."""
    data = extract_next_data(html)
    items: dict[str, Activity] = {}
    if data is not None:
        for a in activities_from_json(data, kind):
            items[a.id] = a
    if not items:  # JSON에서 못 찾으면 링크라도 수집
        soup = BeautifulSoup(html, "html.parser")
        for link in soup.find_all("a", href=ACTIVITY_LINK_RE):
            aid = ACTIVITY_LINK_RE.search(link["href"]).group(1)
            if aid not in items:
                items[aid] = Activity(id=aid, kind=kind, title=link.get_text(" ", strip=True)[:200])
    return list(items.values())


def parse_detail(html: str, base: Activity) -> Activity:
    result = Activity(id=base.id, kind=base.kind)
    data = extract_next_data(html)
    if data is not None:
        for a in activities_from_json(data, base.kind):
            if a.id == base.id:
                result.merge(a)
    soup = BeautifulSoup(html, "html.parser")
    og_title = soup.find("meta", property="og:title")
    if og_title and not result.title:
        result.title = og_title.get("content", "").split("|")[0].strip()
    meta_desc = soup.find("meta", attrs={"name": "description"})
    meta_text = meta_desc.get("content", "") if meta_desc else ""
    if result.description:
        result.description = f"{meta_text} {result.description}".strip()
    else:
        # 본문을 JSON에서 못 찾은 경우에만 페이지 전체 텍스트 사용
        # (사이드바의 다른 공고 제목 때문에 오탐이 생길 수 있음)
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
            tag.decompose()
        result.description = f"{meta_text} {soup.get_text(' ', strip=True)}"[:8000]
    return result


def extract_next_data(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError:
        return None


def activities_from_json(data: dict, kind: str) -> list[Activity]:
    """JSON 트리 어디에 있든 Activity 객체를 찾아낸다 (Apollo 정규화 캐시 포함)."""
    refs: dict[str, dict] = {}
    for d in _iter_dicts(data):
        for k, v in d.items():
            if isinstance(v, dict) and re.match(r"^[A-Z]\w*:\S+$", k):
                refs[k] = v

    result: dict[str, Activity] = {}
    for d in _iter_dicts(data):
        if d.get("__typename") != "Activity" or "id" not in d:
            continue
        a = _to_activity(d, refs, kind)
        if a.id in result:
            result[a.id].merge(a)
        else:
            result[a.id] = a
    return list(result.values())


def _iter_dicts(obj):
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _resolve(value, refs):
    if isinstance(value, dict) and "__ref" in value:
        return refs.get(value["__ref"], {})
    return value


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, "", []):
            return d[k]
    return None


def _to_activity(d: dict, refs: dict, kind: str) -> Activity:
    org = _first(d, "organizationName", "orgName", "companyName", "hostName")
    if not org:
        org_obj = _resolve(_first(d, "organization", "host", "company") or {}, refs)
        if isinstance(org_obj, dict):
            org = _first(org_obj, "name", "fullName")
    categories = []
    for c in (d.get("categories") or []) + (d.get("interests") or []):
        c = _resolve(c, refs)
        name = c.get("name") if isinstance(c, dict) else c
        if isinstance(name, str) and name not in categories:
            categories.append(name)
    desc = _first(d, "detailText", "text", "content", "description") or ""
    desc = _resolve(desc, refs)
    if isinstance(desc, dict):
        desc = _first(desc, "text", "content") or ""
    images = []
    for f in d.get("files") or []:  # 파일 유형 2 = 포스터
        f = _resolve(f, refs)
        if isinstance(f, dict) and f.get("url") and (f.get("type") or {}).get("__ref", "").endswith(":2"):
            images.append(f["url"])
    if not images:
        thumb = _resolve(d.get("thumbnailImage") or {}, refs)
        if isinstance(thumb, dict) and thumb.get("url"):
            images.append(thumb["url"])
    images += [u for u in re.findall(r'<img[^>]+src="([^"]+)"', str(desc)) if u.startswith("http")]
    views = _first(d, "viewCount", "views")
    reward = d.get("tenThousandUnitOfReward")
    return Activity(
        id=str(d["id"]),
        kind=kind,
        title=str(_first(d, "title", "name") or ""),
        organizer=str(org or ""),
        organization_type=str(d.get("organizationType") or ""),
        reward=int(reward) if isinstance(reward, (int, float)) and reward > 0 else None,
        categories=categories,
        close_date=_to_date(_first(d, "recruitCloseAt", "closeAt", "endAt", "deadline", "dueDate")),
        view_count=int(views) if isinstance(views, (int, float)) else None,
        description=BeautifulSoup(str(desc), "html.parser").get_text(" ", strip=True)[:8000],
        image_urls=images,
    )


def _to_date(value) -> date | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            ts = float(value)
            ts = ts / 1000 if ts > 1e11 else ts  # ms → s
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(KST).date()
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(KST)
        return dt.date()
    except (ValueError, OverflowError, OSError):
        return None
