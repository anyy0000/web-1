"""식품/건강 관련성 판정.

점수는 내부 판정용이고 시트에는 추천도(추천/검토)와 사람이 읽을 수 있는 추천이유만 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from crawler import Activity

ORG_SCORE = 4          # 관심 기업/기관이 주최
CATEGORY_SCORE = 4     # 링커리어 관심분야가 일치 (예: 요리/식품) → 단독으로 추천
SUB_CATEGORY_SCORE = 2  # 참고분야 일치 (예: 의료/보건) → 단독으로는 검토
ORG_KEYWORD_SCORE = 3  # 주최사 이름에 식품·제약 등 업종 단어
TITLE_KEYWORD_SCORE = 2
BODY_KEYWORD_SCORE = 1
BODY_SCORE_CAP = 3
EXCLUDE_SCORE = -3


@dataclass
class Match:
    score: int
    reasons: list[str]
    label: str  # 추천 / 검토 / 제외


class RelevanceFilter:
    def __init__(self, config: dict, extra: dict[str, list[str]] | None = None):
        """config: config.yaml, extra: 스프레드시트 '설정' 탭에서 읽은 추가 항목."""
        extra = extra or {}
        orgs = list(config.get("organizations", [])) + extra.get("관심기업", [])
        self.org_patterns = [_compile(p) for p in orgs]
        self.blocked_orgs = [_compile(p) for p in extra.get("제외기업", [])]
        self.categories = [c.strip() for c in list(config.get("categories", [])) + extra.get("관심분야", [])]
        self.sub_categories = [c.strip() for c in
                               list(config.get("sub_categories", [])) + extra.get("참고분야", [])]
        self.org_keywords = [k.lower() for k in config.get("organizer_keywords", [])]
        self.content_keywords = _dedupe(
            [k.lower() for k in list(config.get("content_keywords", [])) + extra.get("키워드", [])])
        self.exclude_keywords = _dedupe(
            [k.lower() for k in list(config.get("exclude_keywords", [])) + extra.get("제외키워드", [])])
        scoring = config.get("scoring", {})
        self.recommend = scoring.get("recommend_threshold", 4)
        self.review = scoring.get("review_threshold", 2)

    def evaluate(self, a: Activity) -> Match:
        score, reasons = 0, []
        organizer = a.organizer.strip()

        if organizer and any(p.search(organizer) for p in self.blocked_orgs):
            return Match(-99, [f"제외기업: {organizer}"], "제외")

        if organizer and any(p.search(organizer) for p in self.org_patterns):
            score += ORG_SCORE
            reasons.append(f"관심 기업·기관 주최({organizer})")
        else:
            kw = next((k for k in self.org_keywords if k in organizer.lower()), None)
            if kw:
                score += ORG_KEYWORD_SCORE
                reasons.append(f"주최사 이름에 '{kw}'")
            else:
                title_org = next((p for p in self.org_patterns if p.search(a.title)), None)
                if title_org:
                    score += ORG_SCORE
                    reasons.append("제목에 관심 기업·기관명")

        cat_hits = [c for c in a.categories if c in self.categories]
        if cat_hits:
            score += CATEGORY_SCORE
            reasons.append("링커리어 분야 " + "·".join(cat_hits))
        else:
            sub_hits = [c for c in a.categories if c in self.sub_categories]
            if sub_hits:
                score += SUB_CATEGORY_SCORE
                reasons.append("참고 분야 " + "·".join(sub_hits))

        title = a.title.lower()
        title_hits = [k for k in self.content_keywords if k in title]
        if title_hits:
            score += TITLE_KEYWORD_SCORE * min(len(title_hits), 2)
            reasons.append("제목 키워드 " + ", ".join(title_hits[:3]))

        body = a.description.lower()
        body_hits = [k for k in self.content_keywords if k in body and k not in title_hits]
        if body_hits:
            score += min(len(body_hits) * BODY_KEYWORD_SCORE, BODY_SCORE_CAP)
            reasons.append("본문 키워드 " + ", ".join(body_hits[:4]))

        excl = [k for k in self.exclude_keywords if k in f"{title} {body}"]
        if excl:
            score += EXCLUDE_SCORE
            reasons.append("감점: " + ", ".join(excl[:3]))

        if score >= self.recommend:
            label = "추천"
        elif score >= self.review:
            label = "검토"
        else:
            label = "제외"
        return Match(score, reasons, label)


def _compile(entry: str) -> re.Pattern:
    entry = entry.strip()
    if entry.startswith("re:"):
        return re.compile(entry[3:])
    return re.compile(re.escape(entry), re.IGNORECASE)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i.strip() for i in items if i.strip()))
