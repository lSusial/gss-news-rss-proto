"""
glb-news-rss  KB 글로벌 데일리 브리핑 HTML Export

사용법:
  python export_html.py                        # 어제 일일 + 이번 주 주간
  python export_html.py --date 2026-06-08      # 특정 날짜
  python export_html.py --type daily           # 일일만
  python export_html.py --type weekly          # 주간만
  python export_html.py --all                  # DB에 있는 모든 날짜

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
import html as _html_lib
import re as _re_lib

def _e(t):
    t = _html_lib.unescape(str(t or ""))
    t = _re_lib.sub(r"<[^>]+>", "", t)
    return _html_lib.escape(t)

import score_engine

ROOT     = Path(__file__).resolve().parent
DB_PATH  = ROOT / "data" / "news.db"
HTML_DIR = ROOT / "data" / "html"

CC_META = {
    "GLOBAL": {"flag": "🌐", "name": "글로벌"},
    "US":     {"flag": "🇺🇸", "name": "미국"},
    "CN":     {"flag": "🇨🇳", "name": "중국"},
    "JP":     {"flag": "🇯🇵", "name": "일본"},
    "KR":     {"flag": "🇰🇷", "name": "한국"},
    "IN":     {"flag": "🇮🇳", "name": "인도"},
    "ID":     {"flag": "🇮🇩", "name": "인도네시아"},
    "VN":     {"flag": "🇻🇳", "name": "베트남"},
    "KH":     {"flag": "🇰🇭", "name": "캄보디아"},
    "MM":     {"flag": "🇲🇲", "name": "미얀마"},
}

# KB 거점 정보 (정적)
KB_LOCATIONS = [
    {"cc": "US",  "city": "뉴욕",      "flag": "🇺🇸"},
    {"cc": "CN",  "city": "베이징",    "flag": "🇨🇳"},
    {"cc": "JP",  "city": "도쿄",      "flag": "🇯🇵"},
    {"cc": "IN",  "city": "뭄바이",    "flag": "🇮🇳"},
    {"cc": "ID",  "city": "자카르타",  "flag": "🇮🇩"},
    {"cc": "VN",  "city": "하노이",    "flag": "🇻🇳"},
    {"cc": "KH",  "city": "프놈펜",    "flag": "🇰🇭"},
    {"cc": "MM",  "city": "양곤",      "flag": "🇲🇲"},
]

CAT_KO = {
    "Economy": "경제",
    "Finance": "금융",
    "Digital": "디지털",
    "Risk":    "금융사고",
}

LEVEL_COLOR = {
    "높음": "#ff453a",
    "보통": "#0a84ff",
    "낮음": "#30d158",
    "주의": "#ff9f0a",
    "경보": "#ff453a",
}
LEVEL_BG = {
    "높음": "rgba(255,69,58,.15)",
    "보통": "rgba(10,132,255,.13)",
    "낮음": "rgba(48,209,88,.13)",
    "주의": "rgba(255,159,10,.15)",
    "경보": "rgba(255,69,58,.18)",
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
    d = dict(row)
    d["issues"]          = json.loads(d.get("issues") or "[]")
    d["keywords"]        = json.loads(d.get("keywords") or "[]")
    d["source_articles"] = json.loads(d.get("source_articles") or "[]")
    d["key_stat"]        = json.loads(d["key_stat"]) if d.get("key_stat") else None
    return d


def load_top_articles(conn, brief_date: str, limit: int = 5) -> list[dict]:
    """전체 국가 기준 상위 기사 (TOP 카드용)."""
    rows = conn.execute("""
        SELECT a.title, a.link, a.summary_ko, a.ai_score, a.topics,
               a.published_at, m.media_name, m.primary_country_code AS cc
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision = 'passed'
          AND a.ai_score >= 4
          AND DATE(COALESCE(a.published_at, a.fetched_at)) = ?
        ORDER BY a.ai_score DESC, a.published_at DESC NULLS LAST
        LIMIT ?
    """, (brief_date, limit)).fetchall()
    return [dict(r) for r in rows]


def load_articles_by_cc(conn, cc: str, brief_date: str, limit: int = 30) -> list[dict]:
    rows = conn.execute("""
        SELECT a.title, a.link, a.summary_ko, a.ai_score, a.topics,
               a.published_at, m.media_name, m.tier
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision = 'passed'
          AND a.ai_score IS NOT NULL
          AND m.primary_country_code = ?
          AND DATE(COALESCE(a.published_at, a.fetched_at)) = ?
        ORDER BY a.ai_score DESC NULLS LAST, a.published_at DESC NULLS LAST
        LIMIT ?
    """, (cc, brief_date, limit)).fetchall()
    return [dict(r) for r in rows]


def load_all_dates(conn) -> list[tuple[str, str]]:
    rows = conn.execute("""
        SELECT DISTINCT briefing_date, briefing_type
        FROM country_briefings
        ORDER BY briefing_date DESC, briefing_type
    """).fetchall()
    return [(r["briefing_date"], r["briefing_type"]) for r in rows]


def _fmt_pub(iso: str | None) -> str:
    if not iso: return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d.strftime("%-m/%-d %H:%M")
    except Exception:
        return (iso or "")[:10]


def _score_dot(score) -> str:
    if score is None: return "#3a3a3c"
    if score >= 5: return "#ff453a"
    if score >= 4: return "#ff9f0a"
    if score >= 3: return "#ffd60a"
    return "#3a3a3c"


def _cc_flag(cc: str) -> str:
    return CC_META.get(cc, {}).get("flag", "🌐")


def _cc_name(cc: str) -> str:
    return CC_META.get(cc, {}).get("name", cc)


# ── 섹션 렌더러 ────────────────────────────────────────────────────────────────

def render_temp_gauge(scores: dict) -> str:
    items_html = ""
    for cat in ["Economy", "Finance", "Digital", "Risk"]:
        s = scores.get(cat)
        if not s:
            continue
        color = LEVEL_COLOR.get(s.level, "#8e8e93")
        bg    = LEVEL_BG.get(s.level, "rgba(142,142,147,.1)")
        pct   = int(s.score)
        items_html += f"""
        <div class="gauge-item">
          <div class="gauge-label">{CAT_KO[cat]}</div>
          <div class="gauge-bar-wrap">
            <div class="gauge-bar" style="width:{pct}%;background:{color}"></div>
          </div>
          <div class="gauge-badge" style="background:{bg};color:{color}">{s.level}</div>
          <div class="gauge-hint">{' · '.join(s.top_topics[:2]) if s.top_topics else ''}</div>
        </div>"""
    return f"<div class='gauge-grid'>{items_html}</div>"


def render_exec_summary(global_brief: dict | None) -> str:
    if not global_brief:
        return ""
    issues = global_brief.get("issues") or []
    cat_icons = ["📈", "🏦", "💻", "⚠️", "🌏"]
    rows = ""
    for i, issue in enumerate(issues[:4]):
        icon = cat_icons[i % len(cat_icons)]
        rows += f"""
        <div class="exec-row">
          <span class="exec-icon">{icon}</span>
          <div>
            <span class="exec-cat">{_e(issue.get('title',''))}</span>
            <span class="exec-detail">{_e((issue.get('detail','') or '')[:120])}…</span>
          </div>
        </div>"""

    key_stat = global_brief.get("key_stat")
    stat_html = ""
    if key_stat and isinstance(key_stat, dict):
        stat_html = f"""
        <div class="key-stat-box">
          <div class="key-stat-value">{_e(str(key_stat.get('value','')))}</div>
          <div class="key-stat-label">{_e(str(key_stat.get('label','')))}</div>
        </div>"""

    return f"""
    <section class="exec-section">
      <div class="section-header">
        <span class="section-badge">EXECUTIVE SUMMARY</span>
        <span class="section-date" id="brief-date-label"></span>
      </div>
      {rows}
      {stat_html}
    </section>"""


def render_top5(articles: list[dict]) -> str:
    if not articles:
        return ""
    cards = ""
    for i, a in enumerate(articles, 1):
        dot   = _score_dot(a.get("ai_score"))
        flag  = _cc_flag(a.get("cc", ""))
        city  = _cc_name(a.get("cc", ""))
        sumko = _e((a.get("summary_ko") or "")[:120])
        pub   = _fmt_pub(a.get("published_at"))
        cards += f"""
        <a class="top-card" href="{_e(a.get('link','#'))}" target="_blank">
          <div class="top-num">{i:02d}</div>
          <div class="top-body">
            <div class="top-meta">
              <span class="top-flag">{flag} {_e(city)}</span>
              <span class="top-src">{_e(a.get('media_name',''))}</span>
              {f'<span class="top-time">{pub}</span>' if pub else ''}
            </div>
            <div class="top-title">{_e(a.get('title',''))}</div>
            {f'<div class="top-sumko">{sumko}</div>' if sumko else ''}
          </div>
          <div class="top-dot" style="background:{dot}"></div>
        </a>"""
    return f"""
    <section class="top5-section">
      <div class="section-header">
        <span class="section-badge accent">🌐 TODAY'S TOP {len(articles)}</span>
      </div>
      <div class="top-list">{cards}</div>
    </section>"""


def render_category_trend(scores: dict, global_brief: dict | None) -> str:
    if not global_brief:
        return ""
    issues   = global_brief.get("issues") or []
    keywords = global_brief.get("keywords") or []
    kw_html  = "".join(f"<span class='kw-tag'>{_e(k)}</span>" for k in keywords[:10])

    issue_rows = ""
    for issue in issues[:5]:
        issue_rows += f"""
        <div class="cat-issue-row">
          <div class="cat-issue-dot"></div>
          <div>
            <span class="cat-issue-title">{_e(issue.get('title',''))}</span>
            <span class="cat-issue-desc">{_e((issue.get('detail','') or '')[:100])}…</span>
          </div>
        </div>"""

    return f"""
    <section class="cat-section">
      <div class="section-header">
        <span class="section-badge">카테고리별 동향</span>
      </div>
      <div class="kw-cloud">{kw_html}</div>
      <div class="cat-issues">{issue_rows}</div>
    </section>"""


def render_locations(conn, brief_date: str) -> str:
    n_countries = len([cc for cc in CC_META if cc != "GLOBAL"])
    n_briefings = conn.execute(
        "SELECT COUNT(*) FROM country_briefings WHERE briefing_date=? AND briefing_type='daily'",
        (brief_date,)
    ).fetchone()[0]
    n_articles = conn.execute(
        "SELECT COUNT(*) FROM articles_raw WHERE filter_decision='passed' AND ai_score IS NOT NULL AND DATE(COALESCE(published_at, fetched_at))=?",
        (brief_date,)
    ).fetchone()[0]

    loc_cards = ""
    for loc in KB_LOCATIONS:
        cc = loc["cc"]
        art_cnt = conn.execute(
            """SELECT COUNT(*) FROM articles_raw a
               JOIN media_sources m ON m.source_id=a.source_id
               WHERE m.primary_country_code=? AND a.filter_decision='passed'
               AND DATE(COALESCE(a.published_at, a.fetched_at))=?""",
            (cc, brief_date)
        ).fetchone()[0]
        has_brief = conn.execute(
            "SELECT 1 FROM country_briefings WHERE cc=? AND briefing_date=? AND briefing_type='daily'",
            (cc, brief_date)
        ).fetchone()
        status_color = "#30d158" if has_brief else "#ff9f0a"
        loc_cards += f"""
        <div class="loc-card">
          <span class="loc-flag">{loc['flag']}</span>
          <div class="loc-info">
            <div class="loc-city">{loc['city']}</div>
            <div class="loc-cnt">{art_cnt}건</div>
          </div>
          <div class="loc-dot" style="background:{status_color}"></div>
        </div>"""

    return f"""
    <section class="loc-section">
      <div class="section-header">
        <span class="section-badge">실시간 거점 현황</span>
      </div>
      <div class="loc-stats">
        <div class="loc-stat"><span class="loc-stat-num">{n_countries}</span><span class="loc-stat-label">진출국가</span></div>
        <div class="loc-stat"><span class="loc-stat-num">{n_briefings}</span><span class="loc-stat-label">브리핑 완료</span></div>
        <div class="loc-stat"><span class="loc-stat-num">{n_articles}</span><span class="loc-stat-label">수집 기사</span></div>
      </div>
      <div class="loc-grid">{loc_cards}</div>
    </section>"""


def render_country_panel(cc: str, brief: dict | None, articles: list[dict]) -> str:
    if not brief:
        return "<div class='empty-panel'>브리핑 데이터가 없습니다.</div>"

    issues          = brief.get("issues") or []
    keywords        = brief.get("keywords") or []
    outlook         = brief.get("outlook") or ""
    summary         = brief.get("summary") or ""
    source_articles = brief.get("source_articles") or []

    issue_html = ""
    for issue in issues:
        issue_html += f"""
        <div class="c-issue">
          <div class="c-issue-title">{_e(issue.get('title',''))}</div>
          <div class="c-issue-detail">{_e(issue.get('detail',''))}</div>
        </div>"""

    kw_html = "".join(f"<span class='kw-tag'>{_e(k)}</span>" for k in keywords)

    art_html = ""
    for a in articles[:15]:
        dot   = _score_dot(a.get("ai_score"))
        pub   = _fmt_pub(a.get("published_at"))
        sumko = _e((a.get("summary_ko") or "")[:100])
        art_html += f"""
        <a class="art-row" href="{_e(a.get('link','#'))}" target="_blank">
          <span class="art-dot" style="background:{dot}"></span>
          <div class="art-body">
            <div class="art-title">{_e(a.get('title',''))}</div>
            {f'<div class="art-sumko">{sumko}</div>' if sumko else ''}
            <div class="art-meta">{_e(a.get('media_name',''))}{f' · {pub}' if pub else ''}</div>
          </div>
          <span class="art-score">{a.get('ai_score','')}</span>
        </a>"""

    src_html = ""
    for s in source_articles[:8]:
        src_html += f"""
        <a class="src-row" href="{_e(s.get('link','#'))}" target="_blank">
          <span class="src-score">★{s.get('score','')}</span>
          <span class="src-name">{_e(s.get('source',''))}</span>
          <span class="src-title">{_e(s.get('title',''))}</span>
        </a>"""

    return f"""
    <div class="country-panel">
      <div class="cp-summary">{_e(summary)}</div>
      <div class="cp-sub-title">주요 이슈</div>
      <div class="cp-issues">{issue_html}</div>
      {'<div class="cp-sub-title">전망 및 시사점</div><div class="cp-outlook">' + _e(outlook) + '</div>' if outlook else ''}
      {'<div class="cp-keywords">' + kw_html + '</div>' if kw_html else ''}
      {'<div class="cp-sub-title" style="margin-top:20px">오늘의 기사</div><div class="cp-articles">' + art_html + '</div>' if art_html else ''}
      {'<div class="cp-sub-title">분석 소스</div><div class="cp-sources">' + src_html + '</div>' if src_html else ''}
    </div>"""


# ── CSS ────────────────────────────────────────────────────────────────────────

STYLE = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0a0a0f;
  --bg2: #13131a;
  --bg3: #1c1c26;
  --bg4: #232330;
  --text: #f0f0f8;
  --sub: #8888a8;
  --dim: #3a3a52;
  --accent: #4f8ef7;
  --accent2: #7b5cf0;
  --green: #30d158;
  --red: #ff453a;
  --orange: #ff9f0a;
  --yellow: #ffd60a;
  --border: #1e1e2e;
  --radius: 12px;
}
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif;
  font-size: 14px;
  line-height: 1.6;
  min-height: 100vh;
}
a { color: inherit; text-decoration: none; }

/* ── HEADER ── */
.site-header {
  background: linear-gradient(180deg, #0d0d18 0%, #0a0a0f 100%);
  border-bottom: 1px solid var(--border);
  padding: 0 20px;
  position: sticky;
  top: 0;
  z-index: 100;
}
.header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 0 8px;
}
.logo { display: flex; align-items: baseline; gap: 8px; }
.logo-kb { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }
.logo-kb span { color: var(--accent); }
.logo-sub { font-size: 11px; color: var(--sub); }
.header-issue {
  font-size: 11px;
  color: var(--sub);
  background: var(--bg3);
  border: 1px solid var(--dim);
  padding: 3px 10px;
  border-radius: 20px;
}
.back-link { font-size: 12px; color: var(--accent); }

/* ── TABS ── */
.tabs-wrap {
  display: flex;
  gap: 2px;
  overflow-x: auto;
  scrollbar-width: none;
  padding-top: 6px;
}
.tabs-wrap::-webkit-scrollbar { display: none; }
.tab-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 7px 14px 9px;
  border: none;
  border-radius: 10px 10px 0 0;
  background: transparent;
  color: var(--sub);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: background .15s, color .15s;
  border-bottom: 2px solid transparent;
}
.tab-btn .t-flag { font-size: 18px; line-height: 1; }
.tab-btn:hover { background: var(--bg2); color: var(--text); }
.tab-btn.active {
  background: var(--bg2);
  color: var(--text);
  font-weight: 700;
  border-bottom-color: var(--accent);
}

/* ── LAYOUT ── */
.main-wrap { max-width: 900px; margin: 0 auto; padding: 24px 16px 80px; }
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 20px;
}
@media (max-width: 680px) { .two-col { grid-template-columns: 1fr; } }

/* ── SECTION COMMON ── */
.section-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.section-badge {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.8px;
  color: var(--sub);
  background: var(--bg3);
  border: 1px solid var(--dim);
  padding: 3px 10px;
  border-radius: 20px;
  text-transform: uppercase;
}
.section-badge.accent {
  color: var(--accent);
  border-color: rgba(79,142,247,.3);
  background: rgba(79,142,247,.08);
}
.section-date { font-size: 11px; color: var(--sub); }

/* ── GAUGE ── */
.gauge-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px 20px;
  margin-bottom: 20px;
}
.gauge-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px 24px;
}
@media (max-width: 480px) { .gauge-grid { grid-template-columns: 1fr; } }
.gauge-label { font-size: 11px; font-weight: 600; color: var(--sub); margin-bottom: 6px; }
.gauge-bar-wrap {
  height: 5px;
  background: var(--bg4);
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 7px;
}
.gauge-bar { height: 100%; border-radius: 3px; }
.gauge-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 9px;
  border-radius: 20px;
  margin-bottom: 3px;
}
.gauge-hint { font-size: 10px; color: var(--sub); }

/* ── EXEC SUMMARY ── */
.exec-section {
  background: linear-gradient(135deg, #0d1a2e 0%, #0f1520 100%);
  border: 1px solid #1a3a5c;
  border-radius: var(--radius);
  padding: 18px 20px;
  margin-bottom: 20px;
}
.exec-row {
  display: flex;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid rgba(255,255,255,.05);
}
.exec-row:last-of-type { border-bottom: none; }
.exec-icon { font-size: 15px; flex-shrink: 0; padding-top: 1px; }
.exec-cat { font-size: 12.5px; font-weight: 700; color: #a0c4ff; margin-right: 6px; }
.exec-detail { font-size: 12px; color: #7a90b0; line-height: 1.55; }
.key-stat-box {
  background: rgba(79,142,247,.08);
  border: 1px solid rgba(79,142,247,.2);
  border-radius: 10px;
  padding: 14px 18px;
  margin-top: 14px;
  display: flex;
  align-items: baseline;
  gap: 14px;
}
.key-stat-value { font-size: 28px; font-weight: 800; color: var(--accent); letter-spacing: -0.5px; }
.key-stat-label { font-size: 12px; color: var(--sub); }

/* ── TOP 5 ── */
.top5-section {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px 20px;
  margin-bottom: 20px;
}
.top-list { display: flex; flex-direction: column; }
.top-card {
  display: flex;
  gap: 12px;
  padding: 11px 6px;
  border-bottom: 1px solid var(--border);
  border-radius: 8px;
  transition: background .12s;
}
.top-card:last-child { border-bottom: none; }
.top-card:hover { background: var(--bg3); }
.top-num {
  font-size: 20px;
  font-weight: 800;
  color: var(--dim);
  width: 30px;
  flex-shrink: 0;
  line-height: 1.2;
  padding-top: 3px;
}
.top-body { flex: 1; min-width: 0; }
.top-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.top-flag { font-size: 11.5px; color: var(--sub); }
.top-src  { font-size: 11px; color: var(--dim); }
.top-time { font-size: 10.5px; color: var(--dim); }
.top-title { font-size: 13.5px; font-weight: 600; line-height: 1.4; margin-bottom: 3px; }
.top-card:hover .top-title { color: var(--accent); }
.top-sumko { font-size: 11.5px; color: var(--sub); line-height: 1.5; }
.top-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; margin-top: 6px; }

/* ── CATEGORY TREND ── */
.cat-section {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px 20px;
}
.kw-cloud { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
.kw-tag {
  background: rgba(79,142,247,.12);
  color: #7ab8ff;
  font-size: 11px;
  padding: 3px 11px;
  border-radius: 20px;
  border: 1px solid rgba(79,142,247,.25);
}
.cat-issues { display: flex; flex-direction: column; gap: 10px; }
.cat-issue-row { display: flex; gap: 10px; align-items: flex-start; }
.cat-issue-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--accent);
  flex-shrink: 0;
  margin-top: 6px;
}
.cat-issue-title { font-size: 12.5px; font-weight: 700; margin-right: 6px; }
.cat-issue-desc  { font-size: 12px; color: var(--sub); }

/* ── LOCATIONS ── */
.loc-section {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px 20px;
}
.loc-stats {
  display: flex;
  gap: 24px;
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
.loc-stat { text-align: center; }
.loc-stat-num { font-size: 22px; font-weight: 800; display: block; }
.loc-stat-label { font-size: 10px; color: var(--sub); }
.loc-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 7px; }
@media (max-width: 600px) { .loc-grid { grid-template-columns: repeat(2,1fr); } }
.loc-card {
  display: flex;
  align-items: center;
  gap: 7px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 9px;
}
.loc-flag { font-size: 17px; flex-shrink: 0; }
.loc-info { flex: 1; min-width: 0; }
.loc-city { font-size: 11px; font-weight: 600; }
.loc-cnt  { font-size: 10px; color: var(--sub); }
.loc-dot  { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }

/* ── COUNTRY PANEL ── */
.country-panel-wrap {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  margin-top: 4px;
}
.country-panel {}
.cp-summary {
  font-size: 13.5px;
  line-height: 1.75;
  color: #c8d8f0;
  background: rgba(79,142,247,.06);
  border-left: 3px solid var(--accent);
  padding: 12px 16px;
  border-radius: 0 8px 8px 0;
  margin-bottom: 20px;
}
.cp-sub-title {
  font-size: 10px;
  font-weight: 700;
  color: var(--sub);
  letter-spacing: 1px;
  text-transform: uppercase;
  margin-bottom: 10px;
}
.cp-issues { display: flex; flex-direction: column; gap: 10px; margin-bottom: 20px; }
.c-issue {
  background: var(--bg3);
  border-left: 3px solid var(--accent2);
  border-radius: 0 8px 8px 0;
  padding: 10px 14px;
}
.c-issue-title  { font-size: 13px; font-weight: 700; margin-bottom: 5px; }
.c-issue-detail { font-size: 12.5px; color: #9ab4d8; line-height: 1.65; }
.cp-outlook { font-size: 12.5px; color: #9ab4d8; line-height: 1.75; margin-bottom: 14px; }
.cp-keywords { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 20px; }
.cp-articles { display: flex; flex-direction: column; margin-bottom: 20px; }
.art-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.art-row:last-child { border-bottom: none; }
.art-row:hover .art-title { color: var(--accent); }
.art-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; margin-top: 5px; }
.art-body { flex: 1; min-width: 0; }
.art-title { font-size: 13px; font-weight: 500; line-height: 1.4; margin-bottom: 3px; }
.art-sumko { font-size: 11.5px; color: var(--sub); line-height: 1.5; margin-bottom: 3px; }
.art-meta  { font-size: 11px; color: var(--dim); }
.art-score {
  font-size: 12px;
  font-weight: 700;
  color: var(--sub);
  flex-shrink: 0;
  background: var(--bg4);
  padding: 2px 7px;
  border-radius: 6px;
}
.cp-sources { display: flex; flex-direction: column; }
.src-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 5px 0;
  border-bottom: 1px solid var(--border);
  font-size: 11.5px;
  overflow: hidden;
}
.src-row:last-child { border-bottom: none; }
.src-row:hover { color: var(--accent); }
.src-score { color: var(--orange); font-weight: 700; flex-shrink: 0; }
.src-name  { color: var(--sub); flex-shrink: 0; }
.src-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ── INDEX ── */
.idx-date-section { margin-bottom: 24px; }
.idx-date-heading {
  font-size: 12px; font-weight: 700; color: var(--sub);
  letter-spacing: 0.5px;
  margin-bottom: 10px; padding-bottom: 6px;
  border-bottom: 1px solid var(--border);
}
.idx-link-card {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px;
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 10px;
  margin-bottom: 8px;
  transition: background .15s;
}
.idx-link-card:hover { background: var(--bg3); }
.idx-badge { font-size: 10px; font-weight: 700; padding: 3px 9px; border-radius: 20px; white-space: nowrap; }
.badge-daily  { background: rgba(48,209,88,.15); color: #30d158; }
.badge-weekly { background: rgba(79,142,247,.13); color: #4f8ef7; }
.idx-label { font-size: 13px; font-weight: 500; }
.idx-arrow { margin-left: auto; color: var(--dim); }

/* ── MISC ── */
.empty-panel { text-align: center; color: var(--sub); padding: 48px 0; font-size: 13px; }
.footer-note {
  font-size: 10.5px; color: var(--dim);
  text-align: center; margin-top: 40px; line-height: 1.8;
  border-top: 1px solid var(--border); padding-top: 20px;
}
"""


# ── 페이지 빌더 ────────────────────────────────────────────────────────────────

def build_page(conn, brief_date: str, brief_type: str) -> str:
    badge    = "일일 브리핑" if brief_type == "daily" else "주간 브리핑"
    date_lbl = brief_date
    if brief_type == "weekly":
        mon = date.fromisoformat(brief_date)
        sun = mon + timedelta(days=6)
        date_lbl = f"{mon.strftime('%-m/%-d')}~{sun.strftime('%-m/%-d')}"

    d_obj   = date.fromisoformat(brief_date)
    dow_ko  = ["월","화","수","목","금","토","일"][d_obj.weekday()]
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    scores      = score_engine.compute_global_temperature(conn, brief_date, days=3)
    gauge_html  = render_temp_gauge(scores)

    global_brief = load_briefing(conn, "GLOBAL", brief_date, brief_type)
    exec_html    = render_exec_summary(global_brief)
    top_articles = load_top_articles(conn, brief_date, limit=5)
    top5_html    = render_top5(top_articles)
    cat_html     = render_category_trend(scores, global_brief)
    loc_html     = render_locations(conn, brief_date)

    ccs = list(CC_META.keys())
    panels: dict[str, dict] = {}
    for cc in ccs:
        brief    = load_briefing(conn, cc, brief_date, brief_type)
        articles = load_articles_by_cc(conn, cc, brief_date) if brief_type == "daily" else []
        panels[cc] = {
            "meta":    CC_META[cc],
            "html":    render_country_panel(cc, brief, articles),
            "hasData": brief is not None,
        }

    panels_js = json.dumps(panels, ensure_ascii=False)
    ccs_js    = json.dumps(ccs)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>KB 글로벌 데일리 브리핑 — {_e(date_lbl)} ({dow_ko})</title>
<style>{STYLE}</style>
</head>
<body>

<header class="site-header">
  <div class="header-top">
    <div class="logo">
      <div class="logo-kb">KB <span>✱</span></div>
      <div class="logo-sub">글로벌 데일리 브리핑</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px">
      <span class="header-issue">{_e(badge)} · {_e(date_lbl)} {dow_ko}</span>
      <a class="back-link" href="index.html">← 목록</a>
    </div>
  </div>
  <div class="tabs-wrap" id="tabs"></div>
</header>

<div class="main-wrap">

  <div class="gauge-card">
    <div class="section-header">
      <span class="section-badge">📡 글로벌 온도 {_e(brief_date)} 기준</span>
      <span class="section-date">{now_str}</span>
    </div>
    {gauge_html}
  </div>

  {exec_html}
  {top5_html}

  <div class="two-col">
    {cat_html}
    {loc_html}
  </div>

  <div class="country-panel-wrap">
    <div class="section-header" style="margin-bottom:16px">
      <span class="section-badge" id="panel-cc-label">국가별 상세</span>
    </div>
    <div id="country-panel"></div>
  </div>

  <div class="footer-note">
    전 세계 12개 거점 · KB금융그룹 글로벌<br>
    본 브리핑은 AI 기반 글로벌 뉴스 플랫폼이 각국 중앙은행·금융당국 및 주요 언론 자료를 수집·요약해 자동 생성한 자료입니다.<br>
    수치와 기사는 참고용이며, 투자 판단의 근거로 사용할 수 없습니다.
  </div>

</div>

<script>
const PANELS = {panels_js};
const CCS    = {ccs_js};
let active   = CCS[0];

function renderTabs() {{
  document.getElementById('tabs').innerHTML = CCS.map(cc => {{
    const m = PANELS[cc].meta;
    return `<button class="tab-btn${{cc===active?' active':''}}"
              style="${{PANELS[cc].hasData?'':'opacity:.4'}}"
              onclick="setTab('${{cc}}')" type="button">
      <span class="t-flag">${{m.flag}}</span>
      <span>${{m.name}}</span>
    </button>`;
  }}).join('');
}}

function renderPanel() {{
  document.getElementById('country-panel').innerHTML = PANELS[active].html;
  const lbl = document.getElementById('panel-cc-label');
  if (lbl) lbl.textContent = PANELS[active].meta.flag + ' ' + PANELS[active].meta.name + ' 상세';
}}

function setTab(cc) {{
  active = cc;
  renderTabs();
  renderPanel();
}}

renderTabs();
renderPanel();

const dl = document.getElementById('brief-date-label');
if (dl) dl.textContent = '{_e(brief_date)}';
</script>
</body>
</html>"""


def build_index(entries: list[tuple[str, str, str]]) -> str:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_date: dict[str, list] = {}
    for bd, bt, fn in entries:
        by_date.setdefault(bd, []).append((bt, fn))

    rows_html = ""
    for bd in sorted(by_date, reverse=True):
        d   = date.fromisoformat(bd)
        dow = ["월","화","수","목","금","토","일"][d.weekday()]
        rows_html += f"<div class='idx-date-section'><div class='idx-date-heading'>{bd} ({dow})</div>"
        for bt, fn in sorted(by_date[bd]):
            lbl       = f"일일 브리핑 · {bd}" if bt == "daily" else f"주간 브리핑 · {bd} 주"
            badge_cls = "badge-daily" if bt == "daily" else "badge-weekly"
            badge_txt = "일일" if bt == "daily" else "주간"
            rows_html += (
                f"<a class='idx-link-card' href='{_e(fn)}'>"
                f"<span class='idx-badge {badge_cls}'>{badge_txt}</span>"
                f"<span class='idx-label'>{_e(lbl)}</span>"
                f"<span class='idx-arrow'>→</span>"
                f"</a>"
            )
        rows_html += "</div>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>KB 글로벌 데일리 브리핑 — 아카이브</title>
<style>{STYLE}</style>
</head>
<body>
<header class="site-header">
  <div class="header-top">
    <div class="logo">
      <div class="logo-kb">KB <span>✱</span></div>
      <div class="logo-sub">글로벌 데일리 브리핑</div>
    </div>
    <span class="header-issue">브리핑 아카이브</span>
  </div>
</header>
<div class="main-wrap">
  <div class="section-header" style="margin-bottom:20px">
    <span class="section-badge">전체 브리핑 목록</span>
    <span class="section-date">{now_str}</span>
  </div>
  {rows_html}
  <div class="footer-note">KB금융그룹 글로벌 · AI 자동 생성 브리핑</div>
</div>
</body>
</html>"""


# ── 실행 ──────────────────────────────────────────────────────────────────────

def export_one(conn, brief_date: str, brief_type: str) -> Path:
    html     = build_page(conn, brief_date, brief_type)
    filename = f"{brief_date}_{brief_type}.html"
    out      = HTML_DIR / filename
    out.write_text(html, encoding="utf-8")
    print(f"  ✅ {filename}  ({out.stat().st_size//1024} KB)")
    return out


def main():
    ap = argparse.ArgumentParser(description="KB 글로벌 브리핑 HTML 내보내기")
    ap.add_argument("--date", type=str, default=None)
    ap.add_argument("--type", type=str, default=None, choices=["daily","weekly"])
    ap.add_argument("--all",  action="store_true")
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

    entries = []
    for bd, bt in dates:
        out = export_one(conn, bd, bt)
        entries.append((bd, bt, out.name))

    all_dates   = load_all_dates(conn)
    all_entries = [(bd, bt, f"{bd}_{bt}.html") for bd, bt in all_dates]
    seen = {(bd, bt) for bd, bt, _ in all_entries}
    for bd, bt, fn in entries:
        if (bd, bt) not in seen:
            all_entries.append((bd, bt, fn))

    idx = HTML_DIR / "index.html"
    idx.write_text(build_index(all_entries), encoding="utf-8")
    print(f"  ✅ index.html  ({idx.stat().st_size//1024} KB)")
    conn.close()
    print(f"\n📁 출력 폴더: {HTML_DIR}")


if __name__ == "__main__":
    main()
