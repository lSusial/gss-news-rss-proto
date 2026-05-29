"""
glb-news-rss CLI

사용법:
  python main.py init                        # DB 생성 + sources.yaml 동기화
  python main.py fetch                       # 전체 활성 피드 1회 수집
  python main.py filter [--refilter]         # 키워드 필터 실행
  python main.py ai-rank                     # AI 중요도 분석 (전체, 국가당 50건)
  python main.py ai-rank --country KH        # 캄보디아만
  python main.py ai-rank --limit 30          # 국가당 30건
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
import llm_ranker

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
    """2단계 키워드 필터 실행.
    --refilter : 전체 기사 재처리 (키워드 사전 변경 후 사용)
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
    lst = sub.add_parser("list", help="최근 수집 기사 출력")
    lst.add_argument("--limit", type=int, default=20)
    exp = sub.add_parser("export", help="JSON 덤프")
    exp.add_argument("output", type=str)

    args = p.parse_args()
    {"init": cmd_init, "fetch": cmd_fetch, "report": cmd_report,
     "filter": cmd_filter, "filter-report": cmd_filter_report,
     "ai-rank": cmd_ai_rank,
     "list": cmd_list, "export": cmd_export}[args.cmd](args)


if __name__ == "__main__":
    main()
