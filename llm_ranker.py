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
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
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
        ("topics",     "ALTER TABLE articles_raw ADD COLUMN topics      TEXT"),
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

topics 기준 (3-5개, 한국어, 10자 이내):
- 구체적인 경제·금융 주제어 (예: "금리인상", "BOJ통화정책", "달러강세", "OPEC증산")
- 기업명·지수명 포함 가능 (예: "SpaceXIPO", "닛케이급락", "루피아약세")
- 일반적인 단어 지양 (예: "경제", "금융", "뉴스" 사용 금지)

응답 형식 (JSON 배열만, 설명 없음):
[{"id":1,"score":4,"summary_ko":"핵심 내용 1-2문장","topics":["금리인상","BOJ","엔화약세"]},...]
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
def _extract_json_list(raw: str) -> list:
    """Claude 응답에서 JSON 배열 추출. 코드 블록·자유 텍스트 모두 처리."""
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    return json.loads(m.group(0) if m else raw)


def _call_claude(client: anthropic.Anthropic, articles: list[dict]) -> list[dict]:
    """기사 배치를 Claude에 보내고 [{id, score, summary_ko, topics}] 반환."""
    user_msg = _build_user_message(articles)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return _extract_json_list(resp.content[0].text)
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
    # 중복 기사(duplicate_of IS NOT NULL) 및 LLM 관문 거부 기사 제외
    _base_where = """
        a.filter_decision = 'passed'
        AND a.ai_score IS NULL
        AND a.duplicate_of IS NULL
        AND (a.llm_prefilter IS NULL OR a.llm_prefilter = 'passed')
    """
    if country_code:
        rows = conn.execute(f"""
            SELECT a.article_id, a.title, a.summary, m.primary_country_code AS cc
            FROM articles_raw a
            JOIN media_sources m ON m.source_id = a.source_id
            WHERE {_base_where}
              AND m.primary_country_code = ?
            ORDER BY m.tier ASC, a.published_at DESC NULLS LAST, a.fetched_at DESC
            LIMIT ?
        """, (country_code, limit_per_country)).fetchall()
    else:
        # window function으로 국가별 최신 N건을 한 번의 쿼리로 가져옴
        rows = conn.execute(f"""
            SELECT article_id, title, summary, cc FROM (
                SELECT a.article_id, a.title, a.summary,
                       m.primary_country_code AS cc,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.primary_country_code
                           ORDER BY m.tier ASC, a.published_at DESC NULLS LAST, a.fetched_at DESC
                       ) AS rn
                FROM articles_raw a
                JOIN media_sources m ON m.source_id = a.source_id
                WHERE {_base_where}
            ) WHERE rn <= ?
        """, (limit_per_country,)).fetchall()

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
            score   = max(1, min(5, int(res.get("score", 3))))
            summary = (res.get("summary_ko") or "").strip()
            topics  = json.dumps(res.get("topics") or [], ensure_ascii=False)
            cur.execute(
                """UPDATE articles_raw
                   SET ai_score = ?, summary_ko = ?, ai_model = ?, topics = ?
                   WHERE article_id = ?""",
                (score, summary, MODEL, topics, art["article_id"]),
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


# ---------------------------------------------------------------------------
# 토픽 태깅 (이미 분석된 기사에 topics만 추가)
# ---------------------------------------------------------------------------
RETAG_PROMPT = """\
당신은 글로벌 금융·경제 뉴스 태거입니다.
다음 기사 목록에 대해 토픽 태그만 JSON 배열로 응답하세요.

topics 기준 (3-5개, 한국어, 10자 이내):
- 구체적인 경제·금융 주제어 (예: "금리인상", "BOJ통화정책", "달러강세", "OPEC증산")
- 기업명·지수명 포함 가능 (예: "SpaceXIPO", "닛케이급락", "루피아약세")
- 일반적인 단어 지양 ("경제", "금융", "뉴스" 금지)

응답 형식 (JSON 배열만):
[{"id":1,"topics":["금리인상","BOJ","엔화약세"]},...]
"""


def run_ai_tagging(
    conn: sqlite3.Connection,
    days: int = 7,
    country_code: str | None = None,
) -> dict:
    """
    ai_score는 있지만 topics가 없는 기사에 토픽 태그 추가.
    최근 N일 기사 대상.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("환경변수 ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    ensure_ai_columns(conn)
    client = anthropic.Anthropic(api_key=api_key)

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    where_cc = "AND m.primary_country_code = ?" if country_code else ""
    params: list[Any] = []
    if country_code:
        params.append(country_code)
    params.append(since)

    rows = conn.execute(f"""
        SELECT a.article_id, a.title, a.summary, a.summary_ko
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision = 'passed'
          AND a.ai_score IS NOT NULL
          AND (a.topics IS NULL OR a.topics = '[]' OR a.topics = '')
          AND a.duplicate_of IS NULL
          {where_cc}
          AND DATE(COALESCE(a.published_at, a.fetched_at)) >= ?
        ORDER BY a.published_at DESC NULLS LAST
    """, params).fetchall()

    stats = dict(total=len(rows), tagged=0, skipped=0)
    if not rows:
        log.info("태깅할 기사 없음")
        return stats

    log.info("토픽 태깅 시작: %d건 (배치=%d, 최근 %d일)", len(rows), BATCH_SIZE, days)

    cur = conn.cursor()
    for batch_start in range(0, len(rows), BATCH_SIZE):
        batch = [dict(r) for r in rows[batch_start: batch_start + BATCH_SIZE]]

        # 프롬프트 빌드 (summary_ko 우선, 없으면 영문 summary)
        lines = []
        for i, art in enumerate(batch, 1):
            text = (art.get("summary_ko") or _clean_text(art.get("summary") or ""))[:200]
            lines.append(f'id:{i} 제목: {art["title"] or ""}\n     내용: {text or "(없음)"}')
        user_msg = "\n\n".join(lines)

        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=800,
                system=RETAG_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            results    = _extract_json_list(resp.content[0].text)
            result_map = {r["id"]: r for r in results}
        except Exception as e:
            log.warning("태깅 API 오류: %s — 배치 건너뜀", e)
            stats["skipped"] += len(batch)
            continue

        for i, art in enumerate(batch, 1):
            res = result_map.get(i)
            if not res:
                stats["skipped"] += 1
                continue
            topics = json.dumps(res.get("topics") or [], ensure_ascii=False)
            cur.execute(
                "UPDATE articles_raw SET topics = ? WHERE article_id = ?",
                (topics, art["article_id"]),
            )
            stats["tagged"] += 1

        conn.commit()
        log.info("  배치 %d-%d 완료 (%d/%d)",
                 batch_start + 1, batch_start + len(batch),
                 stats["tagged"] + stats["skipped"], len(rows))

        if batch_start + BATCH_SIZE < len(rows):
            time.sleep(RATE_DELAY)

    log.info("토픽 태깅 완료 — 전체=%d  태깅=%d  실패=%d",
             stats["total"], stats["tagged"], stats["skipped"])
    return stats
