"""
glb-news-rss  Static HTML Export (날짜별 저장)

사용법:
  python export_html.py                        # 어제 일일 + 이번 주 주간
  python export_html.py --date 2026-06-07      # 특정 날짜 일일 + 주간
  python export_html.py --type daily           # 일일만
  python export_html.py --type weekly          # 주간만
  python export_html.py --all                  # DB에 있는 모든 브리핑 날짜

출력:
  data/html/YYYY-MM-DD_daily.html
  data/html/YYYY-MM-DD_weekly.html
  data/html/index.html
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from html import escape as _e

ROOT     = Path(__file__).resolve().parent
DB_PATH  = ROOT / "data" / "news.db"
HTML_DIR = ROOT / "data" / "html"

CC_META = {
    "US": {"flag": "🇺🇸", "name": "미국"},
    "CN": {"flag": "🇨🇳", "name": "중국"},
    "JP": {"flag": "🇯🇵", "name": "일본"},
    "IN": {"flag": "🇮🇳", "name": "인도"},
    "ID": {"flag": "🇮🇩", "name": "인도네시아"},
    "VN": {"flag": "🇻🇳", "name": "베트남"},
    "KH": {"flag": "🇰🇭", "name": "캄보디아"},
    "MM": {"flag": "🇲🇲", "name": "미얀마"},
    "GLOBAL": {"flag": "🌐", "name": "글로벌"},
}


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_briefing(conn, cc: str, brief_date: str, brief_type: str) -> dict | None:
    row = conn.execute("""
        SELECT * FROM country_briefings
        WHERE cc=? AND briefing_date=? AND briefing_type=?
    """, (cc, brief_date, brief_type)).fetchone()
    if not row:
        return None
    return {
        "summary":         row["summary"],
        "issues":          json.loads(row["issues"] or "[]"),
        "outlook":         row["outlook"],
        "keywords":        json.loads(row["keywords"] or "[]"),
        "source_articles": json.loads(row["source_articles"] or "[]"),
        "generated_at":    row["generated_at"],
        "article_count":   row["article_count"],
    }


def load_articles(conn, cc: str, brief_date: str) -> list[dict]:
    where_cc = "AND m.primary_country_code = ?" if cc != "GLOBAL" else ""
    params = [cc, brief_date] if cc != "GLOBAL" else [brief_date]
    rows = conn.execute(f"""
        SELECT a.title, a.link, a.summary_ko, a.ai_score,
               a.published_at, m.media_name, m.tier
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision = 'passed'
          AND a.ai_score IS NOT NULL
          {where_cc}
          AND DATE(COALESCE(a.published_at, a.fetched_at)) = ?
        ORDER BY a.ai_score DESC NULLS LAST, a.published_at DESC NULLS LAST
        LIMIT 40
    """, params).fetchall()
    return [dict(r) for r in rows]


def load_all_dates(conn) -> list[tuple[str, str]]:
    """DB에 있는 모든 (briefing_date, briefing_type) 목록."""
    rows = conn.execute("""
        SELECT DISTINCT briefing_date, briefing_type
        FROM country_briefings
        ORDER BY briefing_date DESC, briefing_type
    """).fetchall()
    return [(r["briefing_date"], r["briefing_type"]) for r in rows]


# ── CSS / 공통 템플릿 ────────────────────────────────────────────────────────
STYLE = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:#000; --bg2:#1c1c1e; --bg3:#2c2c2e;
  --text:#fff; --sub:#8e8e93; --dim:#3a3a3c;
  --accent:#0a84ff; --green:#30d158;
  --red:#ff453a; --orange:#ff9f0a; --yellow:#ffd60a;
  --border:#2c2c2e;
  --brief-bg:#0a1628; --brief-border:#1a3a5c;
}
body {
  background:var(--bg); color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Apple SD Gothic Neo',sans-serif;
  font-size:14px; line-height:1.55; min-height:100vh;
}
a { color:inherit; text-decoration:none; }

/* header */
header { padding:18px 24px 0; border-bottom:1px solid var(--border); }
.logo { font-size:20px; font-weight:700; letter-spacing:-.3px; }
.logo span { color:var(--accent); }
.header-meta { font-size:11px; color:var(--sub); margin:4px 0 12px; }
.back { font-size:11px; color:var(--accent); }

/* tabs */
.tabs { display:flex; gap:6px; overflow-x:auto; scrollbar-width:none; padding-bottom:0; }
.tabs::-webkit-scrollbar { display:none; }
.tab {
  display:flex; flex-direction:column; align-items:center; gap:3px;
  padding:8px 12px 10px; border-radius:10px 10px 0 0;
  border:1px solid transparent; border-bottom:none;
  cursor:pointer; background:transparent; color:var(--sub);
  font-size:11px; font-weight:500; white-space:nowrap;
  transition:background .15s,color .15s; flex-shrink:0;
}
.tab .flag { font-size:20px; line-height:1; }
.tab:hover { background:var(--bg2); color:var(--text); }
.tab.active { background:var(--bg2); color:var(--text); border-color:var(--border); font-weight:600; }

/* main */
main { max-width:860px; margin:0 auto; padding:24px 20px 60px; }

/* briefing */
.brief-card {
  background:var(--brief-bg); border:1px solid var(--brief-border);
  border-radius:14px; padding:20px 22px; margin-bottom:24px;
}
.brief-header { display:flex; align-items:center; gap:10px; margin-bottom:14px; }
.brief-badge {
  background:var(--accent); color:#fff;
  font-size:10px; font-weight:700; padding:2px 8px;
  border-radius:6px; letter-spacing:.5px;
}
.brief-date { font-size:11px; color:var(--sub); }
.brief-summary { font-size:13.5px; line-height:1.7; color:#d0e8ff; margin-bottom:18px; }
.sec-title {
  font-size:11px; font-weight:700; color:var(--sub);
  letter-spacing:.8px; text-transform:uppercase; margin-bottom:10px;
}
.issues-list { display:flex; flex-direction:column; gap:10px; margin-bottom:18px; }
.issue-item {
  background:rgba(255,255,255,.04);
  border-left:3px solid var(--accent);
  border-radius:0 8px 8px 0; padding:10px 14px;
}
.issue-title { font-size:13px; font-weight:600; margin-bottom:5px; }
.issue-detail { font-size:12.5px; color:#adc8e8; line-height:1.6; }
.outlook { font-size:12.5px; color:#adc8e8; line-height:1.7; margin-bottom:16px; }
.keywords { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:16px; }
.kw {
  background:rgba(10,132,255,.15); color:#7ab8ff;
  font-size:11px; padding:3px 10px; border-radius:20px;
  border:1px solid rgba(10,132,255,.3);
}
.src-link {
  display:block; font-size:11.5px; color:var(--sub);
  padding:5px 0; border-bottom:1px solid var(--border);
  overflow:hidden; white-space:nowrap; text-overflow:ellipsis;
}
.src-link:last-child { border-bottom:none; }
.src-link:hover { color:var(--accent); }
.src-meta { color:#555; margin-right:5px; }

/* news */
.news-list { display:flex; flex-direction:column; }
.news-item {
  display:flex; gap:12px; padding:13px 0;
  border-bottom:1px solid var(--border);
}
.news-item:last-child { border-bottom:none; }
.news-item:hover .news-title { color:var(--accent); }
.dot-wrap { padding-top:5px; flex-shrink:0; }
.dot { display:block; width:7px; height:7px; border-radius:50%; }
.news-body { flex:1; min-width:0; }
.news-title { font-size:13.5px; font-weight:500; line-height:1.45; margin-bottom:4px; }
.news-summary {
  font-size:12px; color:var(--sub); line-height:1.5;
  display:-webkit-box; -webkit-line-clamp:2;
  -webkit-box-orient:vertical; overflow:hidden; margin-bottom:4px;
}
.news-footer { font-size:11px; color:#555; }
.empty { text-align:center; color:var(--sub); padding:48px 0; font-size:13px; }

/* index page */
.date-section { margin-bottom:28px; }
.date-heading {
  font-size:13px; font-weight:600; color:var(--sub);
  letter-spacing:.5px; margin-bottom:10px;
  padding-bottom:6px; border-bottom:1px solid var(--border);
}
.link-card {
  display:flex; align-items:center; gap:12px;
  padding:12px 16px; background:var(--bg2);
  border-radius:10px; margin-bottom:8px;
  transition:background .15s;
}
.link-card:hover { background:var(--bg3); }
.link-card .badge {
  font-size:10px; font-weight:700; padding:2px 8px;
  border-radius:6px; white-space:nowrap;
}
.badge-daily  { background:#1a3a1a; color:#30d158; }
.badge-weekly { background:#0a1628; color:#0a84ff; }
.link-label { font-size:13px; font-weight:500; }
.link-meta { font-size:11px; color:var(--sub); margin-left:auto; }
"""


def _dot_color(score, tier) -> str:
    if tier == 0: return "#30d158"
    if score and score >= 5: return "#ff453a"
    if score and score >= 4: return "#ff9f0a"
    if score and score >= 3: return "#ffd60a"
    return "#3a3a3c"


def _fmt_date(iso: str | None) -> str:
    if not iso: return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.strftime("%-m/%-d")
    except Exception:
        return (iso or "")[:10]


def _render_briefing(brief: dict | None, badge_label: str) -> str:
    if not brief:
        return "<p style='color:#555;font-size:13px;padding:20px 0'>브리핑 데이터가 없습니다.</p>"

    gen = _fmt_date(brief.get("generated_at", ""))
    cnt = brief.get("article_count", 0)

    issues_html = "".join(
        f"<div class='issue-item'>"
        f"<div class='issue-title'>{_e(i.get('title',''))}</div>"
        f"<div class='issue-detail'>{_e(i.get('detail',''))}</div>"
        f"</div>"
        for i in (brief.get("issues") or [])
    )
    kw_html = "".join(
        f"<span class='kw'>{_e(k)}</span>"
        for k in (brief.get("keywords") or [])
    )
    src_html = "".join(
        f"<a class='src-link' href='{_e(a.get('link','#'))}' target='_blank'>"
        f"<span class='src-meta'>[{_e(a.get('source',''))} ★{a.get('score','')}]</span>"
        f"{_e(a.get('title',''))}</a>"
        for a in (brief.get("source_articles") or [])[:10]
    )

    return f"""
    <div class='brief-card'>
      <div class='brief-header'>
        <span class='brief-badge'>{_e(badge_label)}</span>
        <span class='brief-date'>{gen} 생성 · {cnt}건 분석</span>
      </div>
      <div class='brief-summary'>{_e(brief.get('summary',''))}</div>
      {'<div class="sec-title">주요 이슈</div><div class="issues-list">' + issues_html + '</div>' if issues_html else ''}
      {'<div class="sec-title">전망 및 시사점</div><div class="outlook">' + _e(brief.get('outlook','')) + '</div>' if brief.get('outlook') else ''}
      {'<div class="keywords">' + kw_html + '</div>' if kw_html else ''}
      {'<div class="sec-title" style="margin-top:4px">참고 기사</div>' + src_html if src_html else ''}
    </div>"""


def _render_articles(articles: list[dict], show_flag: bool = False) -> str:
    if not articles:
        return "<div class='empty'>해당 날짜 기사가 없습니다.</div>"
    rows = ""
    for a in articles:
        color = _dot_color(a.get("ai_score"), a.get("tier", 1))
        dt    = _fmt_date(a.get("published_at"))
        sumko = _e((a.get("summary_ko") or "")[:160])
        rows += (
            f"<a class='news-item' href='{_e(a.get('link','#'))}' target='_blank'>"
            f"<div class='dot-wrap'><span class='dot' style='background:{color}'></span></div>"
            f"<div class='news-body'>"
            f"<div class='news-title'>{_e(a.get('title',''))}</div>"
            f"{'<div class=news-summary>' + sumko + '</div>' if sumko else ''}"
            f"<div class='news-footer'>{_e(a.get('media_name',''))}{' · ' + dt if dt else ''}</div>"
            f"</div></a>"
        )
    return f"<div class='news-list'>{rows}</div>"


def build_page(
    conn,
    brief_date: str,
    brief_type: str,
) -> str:
    """국가별 탭 + 브리핑 + 기사 목록을 담은 단일 HTML 페이지."""
    badge    = "일일 브리핑" if brief_type == "daily" else "주간 브리핑"
    date_lbl = brief_date
    if brief_type == "weekly":
        mon = date.fromisoformat(brief_date)
        sun = mon + timedelta(days=6)
        date_lbl = f"{mon.strftime('%-m/%-d')}(월) ~ {sun.strftime('%-m/%-d')}(일)"

    now_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ccs      = list(CC_META.keys())

    # 각 국가 데이터 수집
    sections: dict[str, dict] = {}
    for cc in ccs:
        brief    = load_briefing(conn, cc, brief_date, brief_type)
        articles = load_articles(conn, cc, brief_date) if brief_type == "daily" else []
        sections[cc] = {"brief": brief, "articles": articles}

    # 탭 데이터 JS
    tabs_js = json.dumps({
        cc: {
            "meta":     CC_META[cc],
            "briefHtml": _render_briefing(sections[cc]["brief"], badge),
            "artsHtml":  _render_articles(sections[cc]["articles"], cc == "GLOBAL"),
            "hasData":   sections[cc]["brief"] is not None,
        }
        for cc in ccs
    }, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>GLB News — {_e(badge)} {_e(date_lbl)}</title>
<style>{STYLE}</style>
</head>
<body>
<header>
  <div style="display:flex;justify-content:space-between;align-items:center">
    <div class="logo">GLB <span>News</span></div>
    <a class="back" href="index.html">← 목록</a>
  </div>
  <div class="header-meta">{_e(badge)} · {_e(date_lbl)} · 생성: {now_str}</div>
  <div class="tabs" id="tabs"></div>
</header>
<main id="main"></main>
<script>
const DATA = {tabs_js};
const CCS  = {json.dumps(ccs)};
const IS_WEEKLY = {'true' if brief_type == 'weekly' else 'false'};
let active = CCS[0];

function renderTabs() {{
  document.getElementById('tabs').innerHTML = CCS.map(cc => {{
    const m = DATA[cc].meta;
    const dot = DATA[cc].hasData ? '' : ' style="opacity:.4"';
    return `<div class="tab${{cc===active?' active':''}}" onclick="setTab('${{cc}}')"${{dot}}>
      <span class="flag">${{m.flag}}</span><span>${{m.name}}</span></div>`;
  }}).join('');
}}

function render() {{
  renderTabs();
  const d = DATA[active];
  document.getElementById('main').innerHTML =
    d.briefHtml +
    (IS_WEEKLY ? '' :
      `<div class="sec-title" style="margin-top:8px">뉴스 (${{d.artsHtml.includes('news-item') ? '' : '0'}}건)</div>` + d.artsHtml
    );
}}

function setTab(cc) {{
  active = cc;
  render();
  window.scrollTo({{top:0,behavior:'smooth'}});
}}

render();
</script>
</body>
</html>"""


def build_index(entries: list[tuple[str, str, str]]) -> str:
    """entries: [(brief_date, brief_type, filename), ...]"""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # 날짜별로 그룹핑
    by_date: dict[str, list] = {}
    for bd, bt, fn in entries:
        by_date.setdefault(bd, []).append((bt, fn))

    rows_html = ""
    for bd in sorted(by_date, reverse=True):
        d = date.fromisoformat(bd)
        dow = ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]
        rows_html += f"<div class='date-section'><div class='date-heading'>{bd} ({dow})</div>"
        for bt, fn in by_date[bd]:
            if bt == "weekly":
                sun = d + timedelta(days=6)
                lbl = f"주간 브리핑 · {d.strftime('%-m/%-d')}~{sun.strftime('%-m/%-d')}"
                badge_cls = "badge-weekly"
                badge_txt = "주간"
            else:
                lbl = f"일일 브리핑 · {bd}"
                badge_cls = "badge-daily"
                badge_txt = "일일"
            rows_html += (
                f"<a class='link-card' href='{_e(fn)}'>"
                f"<span class='badge {badge_cls}'>{badge_txt}</span>"
                f"<span class='link-label'>{_e(lbl)}</span>"
                f"<span class='link-meta'>→</span>"
                f"</a>"
            )
        rows_html += "</div>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>GLB News — 브리핑 목록</title>
<style>{STYLE}</style>
</head>
<body>
<header>
  <div class="logo">GLB <span>News</span></div>
  <div class="header-meta">브리핑 아카이브 · {now_str}</div>
</header>
<main>
  <div style="margin-bottom:20px">
    <div class="sec-title">브리핑 목록</div>
  </div>
  {rows_html}
</main>
</body>
</html>"""


def export_one(conn, brief_date: str, brief_type: str) -> Path:
    html     = build_page(conn, brief_date, brief_type)
    filename = f"{brief_date}_{brief_type}.html"
    out      = HTML_DIR / filename
    out.write_text(html, encoding="utf-8")
    size_kb  = out.stat().st_size // 1024
    print(f"  ✅ {filename}  ({size_kb} KB)")
    return out


def main():
    ap = argparse.ArgumentParser(description="GLB News 날짜별 HTML 내보내기")
    ap.add_argument("--date", type=str, default=None, help="기준 날짜 YYYY-MM-DD (기본: 어제)")
    ap.add_argument("--type", type=str, default=None, choices=["daily", "weekly"],
                    help="브리핑 타입 (기본: 둘 다)")
    ap.add_argument("--all",  action="store_true", help="DB에 있는 모든 날짜 내보내기")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"❌ DB 없음: {DB_PATH}")
        return

    HTML_DIR.mkdir(parents=True, exist_ok=True)
    conn = _open()

    if args.all:
        dates = load_all_dates(conn)
        print(f"📦 전체 내보내기: {len(dates)}개")
    else:
        brief_date = args.date or (
            (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        )
        types = [args.type] if args.type else ["daily", "weekly"]
        dates = [(brief_date, t) for t in types]
        print(f"📦 내보내기: {brief_date} {types}")

    # 페이지 생성
    entries = []
    for bd, bt in dates:
        out = export_one(conn, bd, bt)
        entries.append((bd, bt, out.name))

    # index.html 재생성 (전체 목록 기준)
    all_dates = load_all_dates(conn)
    all_entries = [(bd, bt, f"{bd}_{bt}.html") for bd, bt in all_dates]
    # 방금 생성한 것도 포함 (이미 DB에 있으면 중복 제거)
    seen = {(bd, bt) for bd, bt, _ in all_entries}
    for bd, bt, fn in entries:
        if (bd, bt) not in seen:
            all_entries.append((bd, bt, fn))

    idx = HTML_DIR / "index.html"
    idx.write_text(build_index(all_entries), encoding="utf-8")
    print(f"  ✅ index.html  ({idx.stat().st_size//1024} KB)")

    conn.close()
    print()
    print(f"📁 출력 폴더: {HTML_DIR}")
    print()
    print("📤 Netlify Drop 배포:")
    print("   1. https://app.netlify.com/drop 접속")
    print(f"   2. {HTML_DIR} 폴더를 드래그 앤 드롭")
    print("   3. 즉시 공개 URL 발급")


if __name__ == "__main__":
    main()
