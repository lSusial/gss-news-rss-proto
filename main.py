"""
glb-news-rss CLI

매일 새벽 파이프라인 (전날 기사 기준 브리핑 생성):
  python main.py fetch                    # 피드 수집 (전날 밤~새벽 기사)
  python main.py filter                   # 키워드 필터
  python main.py dedup                    # 중복 기사 클러스터링
  python main.py llm-filter               # LLM 1차 관문 (비관련 기사 제거)
  python main.py ai-rank                  # AI 중요도 분석 (1-5점 + 한글 요약)
  python main.py brief --type daily       # 일일 브리핑 (어제 기사 기준, 기본값)
  python main.py brief --type weekly      # 주간 브리핑 (어제 기준 7일치)

초기화 (최초 1회):
  python main.py init          # DB 생성 + sources.yaml 동기화

개별 커맨드:
  python main.py filter --refilter           # 전체 기사 재필터링
  python main.py dedup --recheck             # 중복 재탐지
  python main.py llm-filter --country IN     # 인도만
  python main.py ai-rank --country KH        # 캄보디아만
  python main.py ai-rank --limit 30          # 국가당 30건
  python main.py brief --type daily           # 일일 브리핑 (오늘)
  python main.py brief --type weekly          # 주간 브리핑 (기본)
  python main.py brief --date 2026-06-07      # 특정 날짜
  python main.py brief --country US --type daily
  python main.py report                      # 매체 가용성 리포트
  python main.py list --limit 20             # 최근 수집 기사 출력
  python main.py export articles.json        # 전체 기사 JSON 덤프
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

import collector
import keyword_filter
import llm_prefilter
import llm_ranker
import briefing

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "news.db"
SCHEMA = ROOT / "schema.sql"
SOURCES = ROOT / "sources.yaml"
REPORT_PATH = DATA_DIR / "availability_report.md"


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def cmd_init(_args):
    conn = collector.init_db(DB_PATH, SCHEMA)
    collector.sync_sources(conn, SOURCES)
    n_sources = conn.execute("SELECT COUNT(*) AS c FROM media_sources").fetchone()["c"]
    n_feeds = conn.execute("SELECT COUNT(*) AS c FROM media_source_feeds").fetchone()["c"]
    print(f"[init] db={DB_PATH}  sources={n_sources}  feeds={n_feeds}")


def cmd_fetch(_args):
    if not DB_PATH.exists():
        print("DB가 없습니다. 먼저 `python main.py init` 실행하세요.", file=sys.stderr)
        sys.exit(1)
    conn = _open()
    # sources.yaml 변경분 반영
    collector.sync_sources(conn, SOURCES)
    run_id = collector.run_fetch_all(conn)
    run = conn.execute("SELECT * FROM fetch_runs WHERE run_id = ?", (run_id,)).fetchone()
    print(f"[fetch] run_id={run_id}  feeds={run['feeds_total']} "
          f"(ok={run['feeds_ok']} fail={run['feeds_failed']})  "
          f"new={run['new_articles']} dup={run['dup_articles']}")


def cmd_report(_args):
    conn = _open()
    text = collector.build_availability_report(conn)
    REPORT_PATH.write_text(text, encoding="utf-8")
    print(f"[report] wrote {REPORT_PATH}")


def cmd_list(args):
    conn = _open()
    rows = conn.execute("""
        SELECT m.media_name, a.title, a.link, a.published_at
        FROM articles_raw a JOIN media_sources m ON m.source_id = a.source_id
        ORDER BY a.fetched_at DESC LIMIT ?
    """, (args.limit,)).fetchall()
    for r in rows:
        print(f"[{r['media_name']}] {r['published_at'] or '-'}  {r['title']}")
        print(f"  {r['link']}")


def cmd_filter(args):
    """2단계 키워드 필터 실행 (v3 — 제목/본문 분리 점수).
    --refilter : 전체 기사 재처리 (키워드·점수 기준 변경 후 사용)
    """
    conn = _open()
    refilter = getattr(args, "refilter", False)
    if refilter:
        print("[filter] 전체 기사 재필터링 중...")
    stats = keyword_filter.run_keyword_filter(conn, refilter_all=refilter)
    t  = stats["total"]
    if t == 0:
        print("[filter] 처리할 기사 없음 (이미 모두 처리됨)")
        return
    p, r = stats["passed"], stats["rejected"]
    print(f"[filter] 처리={t:,}건  통과={p:,}건({p/t*100:.1f}%)  거부={r:,}건({r/t*100:.1f}%)")


def cmd_dedup(args):
    """중복 기사 클러스터링 — 같은 날짜·국가의 유사 제목 기사를 duplicate_of로 표시."""
    conn = _open()
    recheck = getattr(args, "recheck", False)
    keyword_filter.ensure_dedup_column(conn)
    stats = keyword_filter.run_dedup(conn, recheck=recheck)
    c, d = stats["checked"], stats["duplicates"]
    if c == 0:
        print("[dedup] 처리할 기사 없음")
        return
    print(f"[dedup] 검사={c:,}건  중복표시={d:,}건({d/c*100:.1f}%)  → AI 랭킹 제외 예정")


def cmd_llm_filter(args):
    """LLM 1차 관문 — 키워드 통과 기사 중 비관련 기사를 Haiku로 제거."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ 환경변수 ANTHROPIC_API_KEY를 설정하세요.", file=sys.stderr)
        sys.exit(1)
    conn = _open()
    country = getattr(args, "country", None) or None
    limit   = getattr(args, "limit", 500)
    stats = llm_prefilter.run_llm_prefilter(conn, country_code=country, limit=limit)
    t = stats["total"]
    if t == 0:
        print("[llm-filter] 처리할 기사 없음")
        return
    p, r = stats["passed"], stats["rejected"]
    print(f"[llm-filter] 대상={t:,}건  통과={p:,}건({p/t*100:.1f}%)  거부={r:,}건({r/t*100:.1f}%)")


def cmd_filter_report(_args):
    """필터 결과 리포트 생성 → data/filter_report.md"""
    conn = _open()
    text = keyword_filter.build_filter_report(conn)
    out  = DATA_DIR / "filter_report.md"
    out.write_text(text, encoding="utf-8")
    print(f"[filter-report] wrote {out}")


def cmd_ai_rank(args):
    """AI 중요도 분석 및 한글 요약 생성 (ANTHROPIC_API_KEY 필요)."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ 환경변수 ANTHROPIC_API_KEY를 설정하세요.", file=sys.stderr)
        print("   export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(1)
    conn = _open()
    country = getattr(args, "country", None) or None
    limit   = getattr(args, "limit", 50)
    stats = llm_ranker.run_ai_ranking(conn, country_code=country, limit_per_country=limit)
    t = stats["total"]
    if t == 0:
        print("[ai-rank] 분석할 기사 없음 (이미 처리됨)")
        return
    a, s = stats["analyzed"], stats["skipped"]
    print(f"[ai-rank] 대상={t:,}건  완료={a:,}건  실패={s:,}건")


def cmd_retag(args):
    """기존 분석 기사에 토픽 태그 추가 (ANTHROPIC_API_KEY 필요)."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ 환경변수 ANTHROPIC_API_KEY를 설정하세요.", file=sys.stderr)
        sys.exit(1)
    conn    = _open()
    days    = getattr(args, "days", 7)
    country = getattr(args, "country", None) or None
    stats   = llm_ranker.run_ai_tagging(conn, days=days, country_code=country)
    t = stats["total"]
    if t == 0:
        print("[retag] 태깅할 기사 없음")
        return
    print(f"[retag] 대상={t:,}건  태깅={stats['tagged']:,}건  실패={stats['skipped']:,}건")


def cmd_brief(args):
    """국가별 동향 브리핑 생성 (ANTHROPIC_API_KEY 필요)."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ 환경변수 ANTHROPIC_API_KEY를 설정하세요.", file=sys.stderr)
        sys.exit(1)
    conn         = _open()
    country      = getattr(args, "country", None) or None
    brief_type   = getattr(args, "type",    "weekly")
    brief_date   = getattr(args, "date",    None)
    days         = getattr(args, "days",    None)
    stats = briefing.run_briefing(
        conn,
        country_code   = country,
        briefing_type  = brief_type,
        briefing_date  = brief_date,
        days           = days,
    )
    type_label = "일일" if brief_type == "daily" else "주간"
    print(f"[brief/{type_label}] 국가={stats['total']}  완료={stats['done']}  건너뜀={stats['skipped']}")


def cmd_export(args):
    conn = _open()
    rows = conn.execute("""
        SELECT a.article_id, m.media_name, m.primary_country_code AS country,
               f.feed_section, a.title, a.link, a.summary,
               a.published_at, a.fetched_at
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        JOIN media_source_feeds f ON f.feed_id = a.feed_id
        ORDER BY a.published_at DESC NULLS LAST, a.fetched_at DESC
    """).fetchall()
    payload = [dict(r) for r in rows]
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[export] {len(payload)} articles → {args.output}")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    p = argparse.ArgumentParser(description="glb-news-rss prototype CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init",          help="DB 초기화 및 sources.yaml 동기화")
    sub.add_parser("fetch",         help="전체 활성 피드 1회 수집")
    sub.add_parser("report",        help="매체 가용성 리포트 생성")
    flt = sub.add_parser("filter",   help="2단계 키워드 필터 실행")
    flt.add_argument("--refilter", action="store_true",
                     help="전체 기사 재처리 (키워드 사전 변경 후 사용)")
    sub.add_parser("filter-report", help="필터 결과 리포트 생성 → data/filter_report.md")
    air = sub.add_parser("ai-rank",  help="AI 중요도 분석 및 한글 요약 (ANTHROPIC_API_KEY 필요)")
    air.add_argument("--country", type=str, default=None, help="국가 코드 (예: KH, US, CN)")
    air.add_argument("--limit",   type=int, default=50,   help="국가당 분석 건수 (기본 50)")
    brf = sub.add_parser("brief", help="국가별 동향 브리핑 생성 (ANTHROPIC_API_KEY 필요)")
    brf.add_argument("--country", type=str, default=None,
                     help="국가 코드 (예: US, IN)")
    brf.add_argument("--type",    type=str, default="weekly",
                     choices=["daily", "weekly"],
                     help="브리핑 타입: daily(당일) | weekly(주간 7일, 기본)")
    brf.add_argument("--date",    type=str, default=None,
                     help="기준 날짜 YYYY-MM-DD (기본: 오늘)")
    brf.add_argument("--days",    type=int, default=None,
                     help="주간 브리핑 기간 (기본 7일)")
    lst = sub.add_parser("list", help="최근 수집 기사 출력")
    lst.add_argument("--limit", type=int, default=20)
    exp = sub.add_parser("export", help="JSON 덤프")
    exp.add_argument("output", type=str)

    # ── dedup ──────────────────────────────────────────────────
    ded = sub.add_parser("dedup", help="중복 기사 클러스터링 (AI 랭킹 전 실행 권장)")
    ded.add_argument("--recheck", action="store_true",
                     help="이미 표시된 중복 포함 전체 재탐지")

    # ── llm-filter ─────────────────────────────────────────────
    llf = sub.add_parser("llm-filter", help="LLM 1차 관문 (Haiku, 비관련 기사 제거)")
    llf.add_argument("--country", type=str, default=None, help="국가 코드 (예: IN, ID)")
    llf.add_argument("--limit",   type=int, default=500,  help="처리 최대 건수 (기본 500)")

    # ── retag ──────────────────────────────────────────────────
    rtg = sub.add_parser("retag", help="기존 분석 기사에 토픽 태그 추가")
    rtg.add_argument("--days",    type=int, default=7,   help="최근 N일 기사 (기본 7)")
    rtg.add_argument("--country", type=str, default=None, help="국가 코드")

    args = p.parse_args()
    {"init": cmd_init, "fetch": cmd_fetch, "report": cmd_report,
     "filter": cmd_filter, "filter-report": cmd_filter_report,
     "dedup": cmd_dedup, "llm-filter": cmd_llm_filter,
     "ai-rank": cmd_ai_rank, "retag": cmd_retag, "brief": cmd_brief,
     "list": cmd_list, "export": cmd_export}[args.cmd](args)


if __name__ == "__main__":
    main()
