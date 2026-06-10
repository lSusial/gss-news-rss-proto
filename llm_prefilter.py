"""
glb-news-rss LLM 1차 관문 (Stage 2.5)

키워드 필터를 통과했지만 실제로는 금융·경제와 무관한 기사를 Haiku로 빠르게 제거.

문제 예시:
  "Food bank launches new program"       → bank 키워드로 통과 → LLM이 제거
  "River bank erosion causes flooding"   → bank 키워드로 통과 → LLM이 제거
  "Trade of rare artifacts at museum"    → trade 키워드로 통과 → LLM이 제거
  "Stock of penguins declining rapidly"  → stock 키워드로 통과 → LLM이 제거

동작:
  1. filter_decision='passed', llm_prefilter IS NULL 기사를 20개씩 Haiku 전송
  2. 금융·경제 무관 판정 → llm_prefilter='rejected' (AI 랭킹 대상 제외)
  3. 관련 있는 기사 → llm_prefilter='passed'

사용법:
  python main.py llm-filter             # 전체
  python main.py llm-filter --country IN
  python main.py llm-filter --limit 200
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from typing import Any

import anthropic

from keyword_filter import _clean_text

log = logging.getLogger("llm_prefilter")

MODEL       = "claude-haiku-4-5-20251001"
BATCH_SIZE  = 20   # 1회 API 호출당 기사 수 (Haiku는 빠르므로 20개)
RATE_DELAY  = 0.5  # 호출 간 대기(초) — 429 방지용 (0.3→0.5)

SYSTEM_PROMPT = """\
당신은 금융·경제 뉴스 관련성 분류기입니다.
한국 금융기관의 해외사업 담당자 관점에서, 제공된 뉴스 기사 목록 중 금융·경제와 무관한 기사의 ID를 찾아내세요.

【관련 있는 기사 예시】
- 중앙은행 금리 결정, 통화정책
- 환율, 주가, 채권, 원자재 시세
- GDP, 인플레이션, 경제성장률
- 기업 실적, IPO, M&A, 투자
- 무역협정, 관세, 제재
- 금융 규제, 은행 감독
- 에너지·반도체·공급망 (경제적 영향 중심)

【무관한 기사 예시】
- "food bank" 식품 지원 프로그램
- 스포츠 경기 결과 (경제적 영향 없는 순수 스포츠)
- 연예·문화 소식
- 순수 과학·환경 연구 (정책 무관)
- "trade" 예술품·유물 거래
- "stock" 동물 개체수 감소

아래 기사 목록에서 금융·경제와 무관한 기사를 JSON 형식으로 반환하세요.
관련 있는 기사는 포함하지 마세요. 판단이 애매하면 포함(관련 있음으로 처리)하세요.

응답 형식 (JSON만, 다른 텍스트 없음):
{"rejected": [{"id": 3, "reason": "food bank 프로그램"}, {"id": 7, "reason": "스포츠 기사"}]}
(무관한 기사 없으면: {"rejected": []})
"""


def ensure_prefilter_column(conn: sqlite3.Connection) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(articles_raw)")}
    for col, ddl in [
        ("llm_prefilter",        "ALTER TABLE articles_raw ADD COLUMN llm_prefilter TEXT"),
        ("llm_reject_reason",    "ALTER TABLE articles_raw ADD COLUMN llm_reject_reason TEXT"),
    ]:
        if col not in existing:
            conn.execute(ddl)
            log.info("마이그레이션 완료: %s 컬럼 추가", col)
    conn.commit()


def _build_user_message(articles: list[dict]) -> str:
    lines = []
    for i, art in enumerate(articles, 1):
        title   = (art["title"] or "").strip()
        summary = _clean_text(art["summary"] or "")[:200]
        lines.append(f"ID:{i} 제목: {title}\n     요약: {summary or '(없음)'}")
    return "\n\n".join(lines)


def _call_claude(client: anthropic.Anthropic, articles: list[dict]) -> list[dict] | None:
    """무관한 기사의 배치 내 인덱스(1-based) + 이유 목록 반환.
    Returns: [{"id": 3, "reason": "..."}, ...]
    """
    user_msg = _build_user_message(articles)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text.strip()
        # JSON 블록만 추출
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            raw = m.group(0)
        elif "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip()
        parsed = json.loads(raw)
        rejected = parsed.get("rejected", [])
        if isinstance(rejected, list):
            result = []
            for item in rejected:
                if isinstance(item, dict) and "id" in item:
                    result.append({"id": int(item["id"]), "reason": str(item.get("reason", ""))[:100]})
                elif isinstance(item, (int, float)):
                    result.append({"id": int(item), "reason": ""})
            return result
        return []
    except Exception as e:
        log.warning("LLM 관문 API 오류: %s — 배치 건너뜀 (다음 실행에서 재처리)", e)
        return None  # None → 호출자가 skip, llm_prefilter=NULL 유지


def run_llm_prefilter(
    conn: sqlite3.Connection,
    country_code: str | None = None,
    limit: int = 500,
) -> dict:
    """
    키워드 필터 통과 기사 중 LLM 관문 미처리 기사를 Haiku로 빠르게 분류.

    Returns:
        stats dict {total, passed, rejected, skipped}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("환경변수 ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    ensure_prefilter_column(conn)

    # Tier 0 공식기관 기사는 LLM 판단 없이 자동 통과 처리
    # (잘못 거부됐던 기사도 복구)
    conn.execute("""
        UPDATE articles_raw
        SET llm_prefilter = 'passed'
        WHERE filter_decision = 'passed'
          AND filter_reason = 'official_source'
          AND (llm_prefilter IS NULL OR llm_prefilter = 'rejected')
    """)
    conn.commit()

    client = anthropic.Anthropic(api_key=api_key)

    where_cc = "AND m.primary_country_code = ?" if country_code else ""
    params: list[Any] = []
    if country_code:
        params.append(country_code)
    params.append(limit)

    rows = conn.execute(f"""
        SELECT a.article_id, a.title, a.summary
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision = 'passed'
          AND a.llm_prefilter IS NULL
          AND a.duplicate_of IS NULL
          AND a.filter_reason != 'official_source'
          {where_cc}
        ORDER BY a.published_at DESC NULLS LAST, a.fetched_at DESC
        LIMIT ?
    """, params).fetchall()

    stats = dict(total=len(rows), passed=0, rejected=0, skipped=0)

    if not rows:
        log.info("LLM 관문: 처리할 기사 없음")
        return stats

    log.info("LLM 관문 시작: %d건 (배치=%d)", len(rows), BATCH_SIZE)

    cur = conn.cursor()
    for batch_start in range(0, len(rows), BATCH_SIZE):
        batch          = [dict(r) for r in rows[batch_start: batch_start + BATCH_SIZE]]
        rejected_items = _call_claude(client, batch)

        if rejected_items is None:
            # API 오류 — llm_prefilter=NULL 유지, 다음 실행에서 재처리
            stats["skipped"] += len(batch)
            log.info("  배치 %d-%d — API 오류로 건너뜀 (%d건)",
                     batch_start + 1, batch_start + len(batch), len(batch))
            if batch_start + BATCH_SIZE < len(rows):
                time.sleep(RATE_DELAY)
            continue

        rejected_map = {item["id"]: item["reason"] for item in rejected_items}

        for i, art in enumerate(batch, 1):
            if i in rejected_map:
                cur.execute(
                    "UPDATE articles_raw SET llm_prefilter = 'rejected', llm_reject_reason = ? WHERE article_id = ?",
                    (rejected_map[i], art["article_id"]),
                )
                stats["rejected"] += 1
            else:
                cur.execute(
                    "UPDATE articles_raw SET llm_prefilter = 'passed' WHERE article_id = ?",
                    (art["article_id"],),
                )
                stats["passed"] += 1

        conn.commit()

        log.info(
            "  배치 %d-%d — 거부 %d건 / %d건",
            batch_start + 1, batch_start + len(batch),
            len(rejected_items), len(batch),
        )

        if batch_start + BATCH_SIZE < len(rows):
            time.sleep(RATE_DELAY)

    log.info(
        "LLM 관문 완료 — 전체=%d  통과=%d  거부=%d",
        stats["total"], stats["passed"], stats["rejected"],
    )
    return stats
