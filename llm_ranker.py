"""
glb-news-rss AI 중요도 분석 및 한글 요약 (Stage 3)

사용법:
  python main.py ai-rank                    # 전체 국가 (국가당 최신 50건 분석)
  python main.py ai-rank --country KH       # 캄보디아만
  python main.py ai-rank --limit 30         # 국가당 30건

동작:
  1. filter_decision='passed' 기사 중 ai_score가 없는 것을 대상으로
  2. Claude API에 10개씩 배치 전송
  3. 중요도 점수(1-5)와 한글 요약을 DB에 저장

환경변수:
  ANTHROPIC_API_KEY  (필수)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import anthropic

from keyword_filter import _clean_text

log = logging.getLogger("llm_ranker")

MODEL        = "claude-haiku-4-5-20251001"
BATCH_SIZE   = 10   # API 1회 호출당 기사 수
RATE_DELAY   = 0.5  # 호출 간 대기(초) — rate limit 방지

# Tier 0 공식소스에서도 걸러낼 무의미 제목 패턴
_NOISE_TITLES = {
    "foreign exchange rates", "exchange rates", "login", "bank indonesia",
    "reserve bank of india", "- rbi", "rbi", "bi", "home", "main page",
    "sitemap", "contact", "about",
}

def _is_noise_title(title: str) -> bool:
    """짧거나 무의미한 제목이면 True (환율공시, 로그인 페이지 등)."""
    t = (title or "").strip().lower()
    if len(t) < 12:
        return True
    if t in _NOISE_TITLES:
        return True
    # "foreign exchange rates - bank indonesia" 등 패턴
    if any(noise in t for noise in ("foreign exchange rate", "login -", "- login")):
        return True
    return False

# ---------------------------------------------------------------------------
# DB 마이그레이션
# ---------------------------------------------------------------------------
def ensure_ai_columns(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(articles_raw)")}
    cols = [
        ("ai_score",   "ALTER TABLE articles_raw ADD COLUMN ai_score   INTEGER"),
        ("summary_ko", "ALTER TABLE articles_raw ADD COLUMN summary_ko TEXT"),
        ("ai_model",   "ALTER TABLE articles_raw ADD COLUMN ai_model   TEXT"),
    ]
    added = []
    for col, sql in cols:
        if col not in existing:
            conn.execute(sql)
            added.append(col)
    if added:
        conn.commit()
        log.info("AI 컬럼 추가: %s", added)

# ---------------------------------------------------------------------------
# 프롬프트 빌더
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
당신은 글로벌 금융·경제·ESG 뉴스 편집장입니다.
다음 기사 목록을 분석하여 JSON 배열로만 응답하세요. 다른 텍스트는 출력하지 마세요.

평가 기준:
- score 5: 시장 핵심 이슈 (중앙은행 금리 결정, 대형 M&A, GDP 쇼크, 시장 급등락)
- score 4: 중요 금융·경제 뉴스 (기업 실적, IPO, 주요 정책 발표, 무역 협상)
- score 3: 관련성 있는 일반 경제 뉴스 (산업 동향, 기업 소식, 규제 변화)
- score 2: 낮은 관련성 (간접적 경제 언급)
- score 1: 금융·경제와 거의 무관

응답 형식 (JSON 배열만, 설명 없음):
[{"id":1,"score":4,"summary_ko":"핵심 내용 1-2문장"},...]
"""

def _build_user_message(articles: list[dict]) -> str:
    lines = []
    for i, art in enumerate(articles, 1):
        title   = art["title"] or ""
        summary = _clean_text(art["summary"] or "")[:300]  # 요약 300자 제한
        lines.append(f'id:{i} 제목: {title}\n     요약: {summary or "(없음)"}')
    return "\n\n".join(lines)

# ---------------------------------------------------------------------------
# Claude API 호출
# ---------------------------------------------------------------------------
def _call_claude(client: anthropic.Anthropic, articles: list[dict]) -> list[dict]:
    """기사 배치를 Claude에 보내고 [{id, score, summary_ko}] 반환."""
    user_msg = _build_user_message(articles)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text.strip()
        # JSON 블록만 추출
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except (json.JSONDecodeError, IndexError, anthropic.APIError) as e:
        log.warning("API 오류: %s — 해당 배치 건너뜀", e)
        return []

# ---------------------------------------------------------------------------
# 메인 실행
# ---------------------------------------------------------------------------
def run_ai_ranking(
    conn: sqlite3.Connection,
    country_code: str | None = None,
    limit_per_country: int = 50,
) -> dict:
    """
    passed 기사 중 ai_score가 없는 것을 Claude로 분석.

    Returns:
        stats dict {total, analyzed, skipped}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("환경변수 ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    ensure_ai_columns(conn)
    client = anthropic.Anthropic(api_key=api_key)

    # 분석 대상 조회
    where_cc = "AND m.primary_country_code = ?" if country_code else ""
    params: list[Any] = []
    if country_code:
        params.append(country_code)
    params.append(limit_per_country)

    # 국가별 최신 limit_per_country 건, ai_score 없는 것
    # 중복 기사(duplicate_of IS NOT NULL) 및 LLM 관문 거부 기사 제외
    if country_code:
        rows = conn.execute(f"""
            SELECT a.article_id, a.title, a.summary, m.primary_country_code AS cc
            FROM articles_raw a
            JOIN media_sources m ON m.source_id = a.source_id
            WHERE a.filter_decision = 'passed'
              AND a.ai_score IS NULL
              AND a.duplicate_of IS NULL
              AND (a.llm_prefilter IS NULL OR a.llm_prefilter = 'passed')
              {where_cc}
            ORDER BY m.tier ASC, a.published_at DESC NULLS LAST, a.fetched_at DESC
            LIMIT ?
        """, params).fetchall()
    else:
        # 국가별로 각각 limit개씩 가져와 합산
        all_rows = []
        codes = [r["primary_country_code"] for r in conn.execute(
            "SELECT DISTINCT primary_country_code FROM media_sources ORDER BY primary_country_code"
        ).fetchall()]
        for cc in codes:
            batch = conn.execute("""
                SELECT a.article_id, a.title, a.summary, m.primary_country_code AS cc
                FROM articles_raw a
                JOIN media_sources m ON m.source_id = a.source_id
                WHERE a.filter_decision = 'passed'
                  AND a.ai_score IS NULL
                  AND a.duplicate_of IS NULL
                  AND (a.llm_prefilter IS NULL OR a.llm_prefilter = 'passed')
                  AND m.primary_country_code = ?
                ORDER BY m.tier ASC, a.published_at DESC NULLS LAST, a.fetched_at DESC
                LIMIT ?
            """, (cc, limit_per_country)).fetchall()
            all_rows.extend(batch)
        rows = all_rows

    # 노이즈 제목 사전 제거 (Tier 0 환율공시·로그인 페이지 등)
    clean_rows = []
    noise_ids  = []
    for r in rows:
        if _is_noise_title(r["title"]):
            noise_ids.append(r["article_id"])
        else:
            clean_rows.append(r)

    if noise_ids:
        cur = conn.cursor()
        for aid in noise_ids:
            cur.execute(
                "UPDATE articles_raw SET ai_score=1, summary_ko='[자동제외: 무의미 제목]', ai_model=? WHERE article_id=?",
                (MODEL, aid),
            )
        conn.commit()
        log.info("노이즈 제목 자동 제외: %d건", len(noise_ids))

    rows = clean_rows
    stats = dict(total=len(rows), analyzed=0, skipped=0)
    if not rows:
        log.info("분석할 기사 없음 (이미 처리됨 or 통과 기사 없음)")
        return stats

    log.info("AI 분석 시작: %d건 (배치=%d)", len(rows), BATCH_SIZE)

    # 배치 처리
    for batch_start in range(0, len(rows), BATCH_SIZE):
        batch = [dict(r) for r in rows[batch_start: batch_start + BATCH_SIZE]]
        results = _call_claude(client, batch)

        # 결과 저장
        result_map = {r["id"]: r for r in results}
        cur = conn.cursor()
        for i, art in enumerate(batch, 1):
            res = result_map.get(i)
            if not res:
                stats["skipped"] += 1
                continue
            score     = max(1, min(5, int(res.get("score", 3))))
            summary   = (res.get("summary_ko") or "").strip()
            cur.execute(
                """UPDATE articles_raw
                   SET ai_score = ?, summary_ko = ?, ai_model = ?
                   WHERE article_id = ?""",
                (score, summary, MODEL, art["article_id"]),
            )
            stats["analyzed"] += 1

        conn.commit()
        log.info(
            "  배치 %d-%d 완료 (%d/%d)",
            batch_start + 1, batch_start + len(batch),
            stats["analyzed"], len(rows),
        )
        if batch_start + BATCH_SIZE < len(rows):
            time.sleep(RATE_DELAY)

    log.info(
        "AI 분석 완료 — 전체=%d  분석=%d  실패=%d",
        stats["total"], stats["analyzed"], stats["skipped"],
    )
    return stats
