"""
glb-news-rss 국가별 동향 브리핑 생성 (Stage 4)

사용법:
  python main.py brief              # 전체 국가
  python main.py brief --country US # 미국만
  python main.py brief --days 7     # 최근 7일 기준
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone

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

def _build_prompt(cc: str, articles: list[dict]) -> str:
    name = CC_NAME.get(cc, cc)
    lines = [f"[{name} 최신 경제·금융 뉴스 {len(articles)}건]\n"]
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


def _call_claude(client: anthropic.Anthropic, cc: str, articles: list[dict]) -> dict | None:
    prompt = _build_prompt(cc, articles)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=3000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception as e:
        log.warning("브리핑 API 오류 (%s): %s", cc, e)
        return None


def run_briefing(
    conn: sqlite3.Connection,
    country_code: str | None = None,
    days: int = 3,
) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("환경변수 ANTHROPIC_API_KEY가 설정되지 않았습니다.")

    client = anthropic.Anthropic(api_key=api_key)

    if country_code:
        targets = [country_code]
    else:
        rows = conn.execute(
            "SELECT DISTINCT primary_country_code FROM media_sources ORDER BY primary_country_code"
        ).fetchall()
        targets = [r[0] for r in rows]

    stats = {"total": len(targets), "done": 0, "skipped": 0}

    for cc in targets:
        articles = conn.execute("""
            SELECT a.article_id, a.title, a.summary_ko, a.summary,
                   a.ai_score, a.published_at, a.link,
                   m.media_name, m.tier
            FROM articles_raw a
            JOIN media_sources m ON m.source_id = a.source_id
            WHERE m.primary_country_code = ?
              AND a.filter_decision = 'passed'
              AND a.ai_score >= 3
              AND COALESCE(a.published_at, a.fetched_at) >= datetime('now', ? || ' days')
            ORDER BY a.ai_score DESC, a.published_at DESC NULLS LAST
            LIMIT ?
        """, (cc, f"-{days}", MAX_ARTS)).fetchall()

        if len(articles) < 3:
            log.info("  %s: 기사 부족 (%d건) — 건너뜀", cc, len(articles))
            stats["skipped"] += 1
            continue

        arts_list = [dict(a) for a in articles]
        log.info("  %s: %d건 분석 중...", cc, len(arts_list))
        result = _call_claude(client, cc, arts_list)

        if not result:
            stats["skipped"] += 1
            continue

        # 소스 기사 링크 저장 (title, link, source, score)
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
              (cc, generated_at, summary, issues, outlook, keywords, model, article_count, source_articles)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cc) DO UPDATE SET
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
            datetime.now(timezone.utc).isoformat(),
            result.get("summary", ""),
            json.dumps(result.get("issues", []),    ensure_ascii=False),
            result.get("outlook", ""),
            json.dumps(result.get("keywords", []),  ensure_ascii=False),
            MODEL,
            len(arts_list),
            json.dumps(source_articles, ensure_ascii=False),
        ))
        conn.commit()
        stats["done"] += 1
        log.info("  %s: 브리핑 완료", cc)
        time.sleep(RATE_DELAY)

    return stats
