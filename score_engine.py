"""
glb-news-rss 글로벌 온도 스코어 엔진

articles_raw의 topics + ai_score를 기반으로 4대 카테고리별 점수를 집계.
  - Economy   : 거시경제, 성장, 무역, 환율, 인플레이션
  - Finance   : 금융시장, 금리, 주가, IPO, M&A, 채권
  - Digital   : 핀테크, 암호화폐, AI, 디지털 금융
  - Risk      : 지정학, 금융사고, 사기, 규제 위반

점수 산출 방식:
  - 카테고리에 매핑된 기사의 ai_score 평균 → 1~5
  - 정규화: (avg - 1) / 4 * 100 → 0~100
  - 레벨: HIGH(65+) / MID(40+) / LOW(<40) | Risk: 경보(60+) / 주의(35+)
  - 볼륨 가중: 기사 수가 많을수록 신뢰도 증가
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import NamedTuple


# ── 카테고리별 키워드 매핑 ────────────────────────────────────────────────────
# topics 태그에 아래 키워드가 포함되면 해당 카테고리로 분류
# 하나의 기사가 여러 카테고리에 중복 집계될 수 있음

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Economy": [
        "경제", "gdp", "성장", "인플레이션", "물가", "무역", "관세", "수출", "수입",
        "환율", "달러", "엔화", "위안화", "루피아", "루피", "동화", "킵",
        "소비", "고용", "실업", "재정", "세금", "예산", "무역협상", "공급망",
        "에너지가격", "유가", "원자재", "상품", "imf", "세계은행", "oecd",
        "경제성장", "경기침체", "스태그플레이션", "인플레이션", "디플레이션",
        "무역정책", "통상협상", "fta", "통화", "외환",
    ],
    "Finance": [
        "금리", "통화정책", "중앙은행", "연준", "fed", "boj", "rbi", "ecb", "boe",
        "채권", "주가", "주식", "증시", "ipo", "상장", "m&a", "인수합병",
        "월스트리트", "나스닥", "코스피", "ihsg", "닛케이", "항셍",
        "금융", "은행", "대출", "부채", "신용", "펀드", "etf", "자산",
        "외국인투자", "자금조달", "기업공개", "투자", "채권시장",
        "금융규제", "sebi", "금융감독", "기준금리", "양적완화",
    ],
    "Digital": [
        "핀테크", "암호화폐", "비트코인", "이더리움", "cbdc", "디지털화폐",
        "ai", "인공지능", "블록체인", "디지털", "빅테크", "플랫폼",
        "테크", "스타트업", "유니콘", "데이터", "사이버", "클라우드",
        "전자결제", "모바일결제", "간편결제", "qr결제", "오픈뱅킹",
        "엔비디아", "반도체", "ai기술", "ai투자", "ai기업",
    ],
    "Risk": [
        "지정학", "전쟁", "분쟁", "제재", "긴장", "위기", "충돌",
        "사기", "횡령", "비리", "금융사고", "해킹", "사이버공격",
        "규제위반", "벌금", "과징금", "소송", "수사", "체포",
        "중동", "이란", "이스라엘", "러시아", "우크라이나", "대만",
        "호르무즈", "리스크", "위험", "불안", "변동성",
        "금융감시", "aml", "자금세탁", "테러자금",
    ],
}

LEVEL_HIGH = "높음"
LEVEL_MID  = "보통"
LEVEL_LOW  = "낮음"
LEVEL_WARN = "주의"   # Risk 카테고리 전용

# Risk 카테고리는 레벨 표현이 다름
RISK_LEVELS = {
    "high": "경보",
    "mid":  "주의",
    "low":  "낮음",
}


class CategoryScore(NamedTuple):
    category: str
    level: str       # 높음 / 보통 / 낮음 / 주의 / 경보
    score: float     # 0~100
    count: int       # 분석 기사 수
    top_topics: list[str]  # 상위 topics


def _normalize(avg_score: float) -> float:
    """ai_score 1~5 평균 → 0~100 정규화."""
    return max(0.0, min(100.0, (avg_score - 1) / 4 * 100))


def _to_level(norm: float, is_risk: bool = False) -> str:
    if is_risk:
        if norm >= 60: return RISK_LEVELS["high"]
        if norm >= 35: return RISK_LEVELS["mid"]
        return RISK_LEVELS["low"]
    else:
        if norm >= 65: return LEVEL_HIGH
        if norm >= 40: return LEVEL_MID
        return LEVEL_LOW


def _topic_matches(topics_json: str | None, keywords: list[str]) -> bool:
    if not topics_json:
        return False
    try:
        tags = json.loads(topics_json)
    except Exception:
        return False
    tags_lower = " ".join(tags).lower()
    return any(kw in tags_lower for kw in keywords)


def compute_global_temperature(
    conn: sqlite3.Connection,
    target_date: str | None = None,
    days: int = 3,
) -> dict[str, CategoryScore]:
    """
    최근 N일 기사의 topics + ai_score를 집계해 4대 카테고리 점수 반환.

    Args:
        conn: SQLite 연결
        target_date: 기준 날짜 (YYYY-MM-DD), None이면 오늘
        days: 집계 기간 (기본 3일, 일일 브리핑 기준)

    Returns:
        {"Economy": CategoryScore, "Finance": ..., "Digital": ..., "Risk": ...}
    """
    if target_date is None:
        target_date = date.today().isoformat()

    from_date = (date.fromisoformat(target_date) - timedelta(days=days - 1)).isoformat()

    rows = conn.execute("""
        SELECT a.ai_score, a.topics
        FROM articles_raw a
        WHERE a.filter_decision = 'passed'
          AND a.ai_score IS NOT NULL
          AND a.topics IS NOT NULL
          AND DATE(COALESCE(a.published_at, a.fetched_at)) BETWEEN ? AND ?
    """, (from_date, target_date)).fetchall()

    from collections import defaultdict, Counter
    cat_scores: dict[str, list[float]] = defaultdict(list)
    cat_topics: dict[str, Counter] = defaultdict(Counter)

    for row in rows:
        score = row[0]
        topics_json = row[1]
        try:
            tags = json.loads(topics_json) if topics_json else []
        except Exception:
            tags = []
        tags_lower = " ".join(tags).lower()

        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in tags_lower for kw in keywords):
                cat_scores[cat].append(score)
                for t in tags:
                    cat_topics[cat][t] += 1

    result: dict[str, CategoryScore] = {}
    for cat in ["Economy", "Finance", "Digital", "Risk"]:
        scores = cat_scores.get(cat, [])
        if not scores:
            result[cat] = CategoryScore(cat, LEVEL_LOW, 0.0, 0, [])
            continue
        avg = sum(scores) / len(scores)
        norm = _normalize(avg)
        level = _to_level(norm, is_risk=(cat == "Risk"))
        top = [t for t, _ in cat_topics[cat].most_common(3)]
        result[cat] = CategoryScore(cat, level, round(norm, 1), len(scores), top)

    return result


def compute_country_temperature(
    conn: sqlite3.Connection,
    cc: str,
    target_date: str | None = None,
    days: int = 3,
) -> dict[str, CategoryScore]:
    """특정 국가의 4대 카테고리 점수."""
    if target_date is None:
        target_date = date.today().isoformat()

    from_date = (date.fromisoformat(target_date) - timedelta(days=days - 1)).isoformat()

    rows = conn.execute("""
        SELECT a.ai_score, a.topics
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision = 'passed'
          AND a.ai_score IS NOT NULL
          AND a.topics IS NOT NULL
          AND m.primary_country_code = ?
          AND DATE(COALESCE(a.published_at, a.fetched_at)) BETWEEN ? AND ?
    """, (cc, from_date, target_date)).fetchall()

    from collections import defaultdict, Counter
    cat_scores: dict[str, list[float]] = defaultdict(list)
    cat_topics: dict[str, Counter] = defaultdict(Counter)

    for row in rows:
        score = row[0]
        topics_json = row[1]
        try:
            tags = json.loads(topics_json) if topics_json else []
        except Exception:
            tags = []
        tags_lower = " ".join(tags).lower()

        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in tags_lower for kw in keywords):
                cat_scores[cat].append(score)
                for t in tags:
                    cat_topics[cat][t] += 1

    result: dict[str, CategoryScore] = {}
    for cat in ["Economy", "Finance", "Digital", "Risk"]:
        scores = cat_scores.get(cat, [])
        if not scores:
            result[cat] = CategoryScore(cat, LEVEL_LOW, 0.0, 0, [])
            continue
        avg = sum(scores) / len(scores)
        norm = _normalize(avg)
        level = _to_level(norm, is_risk=(cat == "Risk"))
        top = [t for t, _ in cat_topics[cat].most_common(3)]
        result[cat] = CategoryScore(cat, level, round(norm, 1), len(scores), top)

    return result


def get_key_stat(briefing_issues: list[dict]) -> dict | None:
    """
    브리핑 issues에서 핵심 수치를 추출.
    숫자+단위 패턴이 있는 첫 번째 issue의 detail에서 추출 시도.
    없으면 None 반환.
    """
    import re
    # 수치 패턴: +$23, -3.5%, 5.4%, ↓0.3%p, +$1.2B 등
    pattern = re.compile(
        r'([+\-↑↓]?\$?[\d,]+\.?\d*\s*(?:%p?|배럴|억|조|bp|bps|만|달러|원|엔)?[^\s,\.]{0,4})'
    )
    for issue in (briefing_issues or []):
        detail = issue.get("detail", "") or ""
        title = issue.get("title", "") or ""
        matches = pattern.findall(detail)
        # 의미 있는 수치만 (최소 2자 이상, 숫자 포함)
        valid = [m.strip() for m in matches if any(c.isdigit() for c in m) and len(m) >= 2]
        if valid:
            return {
                "value": valid[0],
                "label": title,
                "detail": detail[:80],
            }
    return None
