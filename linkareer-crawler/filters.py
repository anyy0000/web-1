"""식품/건강 관련성 점수 계산."""

from __future__ import annotations

import re
from dataclasses import dataclass

from crawler import Activity

ORG_SCORE = 4
ORG_KEYWORD_SCORE = 3
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
    def __init__(self, config: dict):
        self.org_patterns = [_compile(p) for p in config.get("organizations", [])]
        self.org_keywords = [k.lower() for k in config.get("organizer_keywords", [])]
        self.content_keywords = [k.lower() for k in config.get("content_keywords", [])]
        self.exclude_keywords = [k.lower() for k in config.get("exclude_keywords", [])]
        scoring = config.get("scoring", {})
        self.recommend = scoring.get("recommend_threshold", 4)
        self.review = scoring.get("review_threshold", 2)

    def evaluate(self, a: Activity) -> Match:
        score, reasons = 0, []
        organizer = a.organizer.strip()

        org_hit = next((p for p in self.org_patterns if organizer and p.search(organizer)), None)
        if org_hit:
            score += ORG_SCORE
            reasons.append(f"주최:{organizer}")
        else:
            kw = next((k for k in self.org_keywords if k in organizer.lower()), None)
            if kw:
                score += ORG_KEYWORD_SCORE
                reasons.append(f"주최키워드:{kw}")
            else:
                # 주최사 필드를 못 얻었을 때 대비: 제목에 기관명이 들어간 경우
                title_org = next((p for p in self.org_patterns if p.search(a.title)), None)
                if title_org:
                    score += ORG_SCORE
                    reasons.append(f"제목내기관:{title_org.pattern}")

        head = f"{a.title} {' '.join(a.categories)}".lower()
        title_hits = [k for k in self.content_keywords if k in head]
        if title_hits:
            score += TITLE_KEYWORD_SCORE * min(len(title_hits), 2)
            reasons.append("제목:" + ",".join(title_hits[:3]))

        body = a.description.lower()
        body_hits = [k for k in self.content_keywords if k in body and k not in title_hits]
        if body_hits:
            score += min(len(body_hits) * BODY_KEYWORD_SCORE, BODY_SCORE_CAP)
            reasons.append("본문:" + ",".join(body_hits[:5]))

        text = f"{head} {body}"
        excl = [k for k in self.exclude_keywords if k in text]
        if excl:
            score += EXCLUDE_SCORE
            reasons.append("감점:" + ",".join(excl[:3]))

        if score >= self.recommend:
            label = "추천"
        elif score >= self.review:
            label = "검토"
        else:
            label = "제외"
        return Match(score, reasons, label)


def _compile(entry: str) -> re.Pattern:
    if entry.startswith("re:"):
        return re.compile(entry[3:])
    return re.compile(re.escape(entry), re.IGNORECASE)
