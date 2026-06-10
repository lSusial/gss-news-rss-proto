"""
glb-news-rss 국가별 동향 브리핑 생성 (Stage 4)

브리핑 타입:
  daily  : 해당 날짜 하루치 기사 분석 (당일 동향)
  weekly : 최근 7일 기사 종합 분석 (주간 동향)

사용법:
  python main.py brief                           # 주간, 오늘
  python main.py brief --type daily              # 일일, 오늘
  python main.py brief --date 2026-06-07         # 특정 날짜
  python main.py brief --type daily --country US
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from datetime import date as _date, datetime, timedelta, timezone

import anthropic

log = logging.getLogger("briefing")

MODEL      = "claude-sonnet-4-6"
MAX_ARTS   = 20
RATE_DELAY = 1.5

CC_NAME = {
    "US": "미국", "CN": "중국", "JP": "일본", "IN": "인도",
    "ID": "인도네시아", "VN": "베트남", "KH": "캄보디아", "MM": "미얀마",
    "KR": "한국", "GLOBAL": "글로벌",
}

SYSTEM_PROMPT = """\
당신은 글로벌 금융·경제 리서치 수석 애널리스트입니다.
제공된 뉴스 기사들을 바탕으로 해당 국가의 최신 경제·금융 동향을 분석하여 아래 JSON 형식으로만 응답하세요.
다른 텍스트 없이 JSON만 출력하세요.

분석 원칙:
- 모든 내용은 한국어로 작성
- 한국 금융기관(은행, 투자사)의 해외사업 담당자 관점에서 작성
- 단순 사실 나열보다 맥락·배경·시사점 중심으로 서술
- 숫자·날짜·기관명은 구체적으로 포함

{
  "summary": "해당 국가의 최근 경제·금융 상황을 종합한 4-6문장 요약. 핵심 이슈, 시장 분위기, 주요 정책 방향을 포함하여 구체적으로 서술할 것.",
  "issues": [
    {
      "title": "이슈 제목 (15자 이내)",
      "detail": "해당 이슈의 배경, 현황, 영향을 구체적 수치와 함께 3-4문장으로 상세히 서술. 단순 사실이 아닌 맥락과 의미를 포함할 것."
    }
  ],
  "outlook": "향후 3-6개월 전망 및 한국 금융기관에 대한 구체적 시사점을 4-5문장으로 서술. 리스크 요인, 기회 요인, 모니터링 포인트를 포함할 것.",
  "keywords": ["핵심키워드1", ...]
}

issues는 반드시 3-5개 포함. 각 detail은 최소 3문장 이상.
"""


# ---------------------------------------------------------------------------
# DB 마이그레이션
# ---------------------------------------------------------------------------
def _week_monday(date_str: str) -> str:
    """날짜가 속한 주의 월요일 반환 (YYYY-MM-DD)."""
    d = _date.fromisoformat(date_str)
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")

def _week_sunday(date_str: str) -> str:
    """날짜가 속한 주의 일요일 반환 (YYYY-MM-DD)."""
    d = _date.fromisoformat(date_str)
    monday = d - timedelta(days=d.weekday())
    return (monday + timedelta(days=6)).strftime("%Y-%m-%d")


def ensure_briefings_schema(conn: sqlite3.Connection) -> None:
    """country_briefings 테이블을 날짜·타입별 히스토리 구조로 마이그레이션."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(country_briefings)")}
    if "briefing_date" in cols:
        return

    log.info("마이그레이션: country_briefings → (cc, briefing_date, briefing_type) 히스토리 구조")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS country_briefings_v2 (
            briefing_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            cc              TEXT NOT NULL,
            briefing_date   TEXT NOT NULL,
            briefing_type   TEXT NOT NULL DEFAULT 'weekly',
            generated_at    TEXT,
            summary         TEXT,
            issues          TEXT,
            outlook         TEXT,
            keywords        TEXT,
            model           TEXT,
            article_count   INTEGER,
            source_articles TEXT,
            UNIQUE(cc, briefing_date, briefing_type)
        )
    """)
    # 기존 데이터 → weekly, 날짜는 generated_at에서 추출
    conn.execute("""
        INSERT OR IGNORE INTO country_briefings_v2
          (cc, briefing_date, briefing_type, generated_at, summary,
           issues, outlook, keywords, model, article_count, source_articles)
        SELECT cc,
               COALESCE(SUBSTR(generated_at, 1, 10), date('now')),
               'weekly',
               generated_at, summary, issues, outlook, keywords,
               model, article_count, source_articles
        FROM country_briefings
    """)
    conn.execute("DROP TABLE country_briefings")
    conn.execute("ALTER TABLE country_briefings_v2 RENAME TO country_briefings")
    conn.commit()
    log.info("마이그레이션 완료")


# ---------------------------------------------------------------------------
# Claude 호출
# ---------------------------------------------------------------------------
def _build_prompt(cc: str, articles: list[dict], briefing_type: str, briefing_date: str) -> str:
    name = CC_NAME.get(cc, cc)
    type_label = "당일" if briefing_type == "daily" else "주간"
    lines = [f"[{name} {briefing_date} {type_label} 경제·금융 뉴스 {len(articles)}건]\n"]
    for i, a in enumerate(articles, 1):
        score  = a.get("ai_score", "")
        title  = a.get("title", "")
        sumko  = a.get("summary_ko", "") or ""
        source = a.get("media_name", "")
        lines.append(
            f"{i}. [{source}] [중요도:{score}] {title}\n"
            f"   요약: {sumko[:400] if sumko else '(없음)'}"
        )
    return "\n\n".join(lines)


def _extract_json_object(raw: str) -> dict:
    """Claude 응답에서 JSON 객체 추출. 코드 블록·자유 텍스트 모두 처리."""
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group(0) if m else raw)


def _call_claude(
    client: anthropic.Anthropic,
    cc: str,
    articles: list[dict],
    briefing_type: str,
    briefing_date: str,
) -> dict | None:
    prompt = _build_prompt(cc, articles, briefing_type, briefing_date)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_json_object(resp.content[0].text)
    except Exception as e:
        log.warning("브리핑 API 오류 (%s): %s", cc, e)
        return None


# ---------------------------------------------------------------------------
# 메인 실행
# ---------------------------------------------------------------------------
def run_briefing(
    conn: sqlite3.Connection,
    country_code: str | None = None,
    briefing_type: str = "weekly",   # 'daily' | 'weekly'
    briefing_date: str | None = None,  # YYYY-MM-DD, 기본값: 오늘
    days: int | None = None,
) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("환경변수 ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    ensure_briefings_schema(conn)
    client = anthropic.Anthropic(api_key=api_key)

    if briefing_date is None:
        # 기본값: 어제 — 새벽 실행 시 전날 기사를 기준으로 브리핑 생성
        briefing_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    # 날짜 필터 구성 (파라미터 바인딩 + KST 보정: DB는 UTC, 브리핑 날짜는 KST 기준)
    _kst = "datetime(COALESCE(a.published_at, a.fetched_at), '+9 hours')"
    if briefing_type == "daily":
        date_sql    = f"AND DATE({_kst}) = ?"
        date_params = [briefing_date]
        min_articles = 2
    else:
        week_mon      = _week_monday(briefing_date)
        week_sun      = _week_sunday(briefing_date)
        briefing_date = week_mon   # DB 저장 키를 월요일로 통일
        date_sql    = f"AND DATE({_kst}) BETWEEN ? AND ?"
        date_params = [week_mon, week_sun]
        min_articles = 3

    if country_code:
        targets = [country_code]
    else:
        rows = conn.execute(
            "SELECT DISTINCT primary_country_code FROM media_sources ORDER BY primary_country_code"
        ).fetchall()
        targets = [r[0] for r in rows]

    stats = {"total": len(targets), "done": 0, "skipped": 0}
    if briefing_type == "daily":
        type_label = f"일일/{briefing_date}"
    else:
        type_label = f"주간/{briefing_date}~{_week_sunday(briefing_date)}"

    for cc in targets:
        articles = conn.execute(
            f"""
            SELECT a.article_id, a.title, a.summary_ko, a.summary,
                   a.ai_score, a.published_at, a.link,
                   m.media_name, m.tier
            FROM articles_raw a
            JOIN media_sources m ON m.source_id = a.source_id
            WHERE m.primary_country_code = ?
              AND a.filter_decision = 'passed'
              AND a.ai_score >= 3
              {date_sql}
            ORDER BY a.ai_score DESC, a.published_at DESC NULLS LAST
            LIMIT ?
            """,
            (cc, *date_params, MAX_ARTS),
        ).fetchall()

        if len(articles) < min_articles:
            log.info("  %s [%s] %s: 기사 부족 (%d건) — 건너뜀",
                     cc, type_label, briefing_date, len(articles))
            stats["skipped"] += 1
            continue

        arts_list = [dict(a) for a in articles]
        log.info("  %s [%s] %s: %d건 분석 중...", cc, type_label, briefing_date, len(arts_list))
        result = _call_claude(client, cc, arts_list, briefing_type, briefing_date)

        if not result:
            stats["skipped"] += 1
            continue

        source_articles = [
            {
                "title":  a["title"],
                "link":   a["link"],
                "source": a["media_name"],
                "score":  a["ai_score"],
            }
            for a in arts_list
        ]

        conn.execute("""
            INSERT INTO country_briefings
              (cc, briefing_date, briefing_type, generated_at,
               summary, issues, outlook, keywords, model, article_count, source_articles)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cc, briefing_date, briefing_type) DO UPDATE SET
                generated_at    = excluded.generated_at,
                summary         = excluded.summary,
                issues          = excluded.issues,
                outlook         = excluded.outlook,
                keywords        = excluded.keywords,
                model           = excluded.model,
                article_count   = excluded.article_count,
                source_articles = excluded.source_articles
        """, (
            cc,
            briefing_date,
            briefing_type,
            datetime.now(timezone.utc).isoformat(),
            result.get("summary", ""),
            json.dumps(result.get("issues",   []), ensure_ascii=False),
            result.get("outlook", ""),
            json.dumps(result.get("keywords", []), ensure_ascii=False),
            MODEL,
            len(arts_list),
            json.dumps(source_articles, ensure_ascii=False),
        ))
        conn.commit()
        stats["done"] += 1
        log.info("  %s [%s] %s: 브리핑 완료", cc, type_label, briefing_date)
        time.sleep(RATE_DELAY)

    return stats


# ---------------------------------------------------------------------------
# 조회 헬퍼 (대시보드용)
# ---------------------------------------------------------------------------
def get_briefing(
    conn: sqlite3.Connection,
    cc: str,
    briefing_date: str,
    briefing_type: str = "weekly",
) -> dict | None:
    r = conn.execute("""
        SELECT * FROM country_briefings
        WHERE cc = ? AND briefing_date = ? AND briefing_type = ?
    """, (cc, briefing_date, briefing_type)).fetchone()
    return dict(r) if r else None


def get_available_dates(
    conn: sqlite3.Connection,
    cc: str,
    briefing_type: str = "weekly",
) -> list[str]:
    """해당 국가·타입의 브리핑이 존재하는 날짜 목록 (최신순)."""
    rows = conn.execute("""
        SELECT briefing_date FROM country_briefings
        WHERE cc = ? AND briefing_type = ?
        ORDER BY briefing_date DESC
    """, (cc, briefing_type)).fetchall()
    return [r["briefing_date"] for r in rows]
