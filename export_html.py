"""
glb-news-rss  Static HTML Snapshot Generator

사용법:
  python export_html.py               # → data/snapshot.html
  python export_html.py --days 7      # 최근 7일 기사 포함
  python export_html.py -o /tmp/out.html
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT    = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "news.db"

CC_META = {
    "US":     {"flag": "🇺🇸", "name": "미국"},
    "CN":     {"flag": "🇨🇳", "name": "중국"},
    "JP":     {"flag": "🇯🇵", "name": "일본"},
    "IN":     {"flag": "🇮🇳", "name": "인도"},
    "ID":     {"flag": "🇮🇩", "name": "인도네시아"},
    "VN":     {"flag": "🇻🇳", "name": "베트남"},
    "KH":     {"flag": "🇰🇭", "name": "캄보디아"},
    "MM":     {"flag": "🇲🇲", "name": "미얀마"},
}

NEWS_LIMIT = 30   # 국가당 최대 기사 수


def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_all_data(days: int) -> dict:
    conn = _open()
    payload: dict = {}

    for cc in CC_META:
        # 브리핑
        row = conn.execute(
            "SELECT * FROM country_briefings WHERE cc = ?", (cc,)
        ).fetchone()
        briefing = None
        if row:
            briefing = {
                "summary":         row["summary"],
                "issues":          json.loads(row["issues"] or "[]"),
                "outlook":         row["outlook"],
                "keywords":        json.loads(row["keywords"] or "[]"),
                "source_articles": json.loads(row["source_articles"] or "[]"),
                "generated_at":    row["generated_at"],
                "article_count":   row["article_count"],
            }

        # 기사 목록
        arts = conn.execute("""
            SELECT a.title, a.link, a.summary_ko, a.ai_score,
                   a.published_at, m.media_name, m.tier
            FROM articles_raw a
            JOIN media_sources m ON m.source_id = a.source_id
            WHERE m.primary_country_code = ?
              AND a.filter_decision = 'passed'
              AND COALESCE(a.published_at, a.fetched_at) >= datetime('now', ? || ' days')
            ORDER BY a.ai_score DESC NULLS LAST, a.published_at DESC NULLS LAST
            LIMIT ?
        """, (cc, f"-{days}", NEWS_LIMIT)).fetchall()

        articles = [dict(a) for a in arts]

        payload[cc] = {
            "meta":     CC_META[cc],
            "briefing": briefing,
            "articles": articles,
        }

    conn.close()
    return payload


def build_html(payload: dict, days: int) -> str:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data_js = json.dumps(payload, ensure_ascii=False, indent=2)
    cc_list  = list(CC_META.keys())

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GLB News — 국가별 경제·금융 동향</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg:      #000000;
    --bg2:     #1c1c1e;
    --bg3:     #2c2c2e;
    --text:    #ffffff;
    --sub:     #8e8e93;
    --accent:  #0a84ff;
    --green:   #30d158;
    --red:     #ff453a;
    --orange:  #ff9f0a;
    --yellow:  #ffd60a;
    --dim:     #3a3a3c;
    --border:  #2c2c2e;
    --brief-bg: #0a1628;
    --brief-border: #1a3a5c;
  }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Apple SD Gothic Neo',
                 sans-serif;
    font-size: 14px;
    line-height: 1.55;
    min-height: 100vh;
  }}

  /* ── 헤더 ── */
  header {{
    padding: 20px 24px 0;
    border-bottom: 1px solid var(--border);
  }}
  .logo {{ font-size: 20px; font-weight: 700; letter-spacing: -.3px; }}
  .logo span {{ color: var(--accent); }}
  .meta {{ font-size: 11px; color: var(--sub); margin-top: 3px; margin-bottom: 14px; }}

  /* ── 국가 탭 ── */
  .tabs {{
    display: flex;
    gap: 8px;
    overflow-x: auto;
    padding-bottom: 0;
    scrollbar-width: none;
  }}
  .tabs::-webkit-scrollbar {{ display: none; }}
  .tab {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    padding: 8px 14px 10px;
    border-radius: 10px 10px 0 0;
    border: 1px solid transparent;
    border-bottom: none;
    cursor: pointer;
    background: transparent;
    color: var(--sub);
    font-size: 11px;
    font-weight: 500;
    white-space: nowrap;
    transition: background .15s, color .15s;
    user-select: none;
    flex-shrink: 0;
  }}
  .tab .flag {{ font-size: 22px; line-height: 1; }}
  .tab:hover {{ background: var(--bg2); color: var(--text); }}
  .tab.active {{
    background: var(--bg2);
    color: var(--text);
    border-color: var(--border);
    font-weight: 600;
  }}

  /* ── 본문 ── */
  main {{
    max-width: 860px;
    margin: 0 auto;
    padding: 24px 20px 60px;
  }}

  /* ── 브리핑 카드 ── */
  .brief-card {{
    background: var(--brief-bg);
    border: 1px solid var(--brief-border);
    border-radius: 14px;
    padding: 20px 22px;
    margin-bottom: 28px;
  }}
  .brief-header {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
  }}
  .brief-badge {{
    background: var(--accent);
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 6px;
    letter-spacing: .5px;
  }}
  .brief-date {{ font-size: 11px; color: var(--sub); }}
  .brief-summary {{
    font-size: 13.5px;
    line-height: 1.65;
    color: #d0e8ff;
    margin-bottom: 18px;
  }}
  .brief-section-title {{
    font-size: 11px;
    font-weight: 700;
    color: var(--sub);
    letter-spacing: .8px;
    text-transform: uppercase;
    margin-bottom: 10px;
  }}
  .issues-list {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 18px; }}
  .issue-item {{
    background: rgba(255,255,255,.04);
    border-left: 3px solid var(--accent);
    border-radius: 0 8px 8px 0;
    padding: 10px 14px;
  }}
  .issue-title {{ font-size: 13px; font-weight: 600; margin-bottom: 5px; }}
  .issue-detail {{ font-size: 12.5px; color: #adc8e8; line-height: 1.6; }}
  .brief-outlook {{
    font-size: 12.5px;
    color: #adc8e8;
    line-height: 1.65;
    margin-bottom: 16px;
  }}
  .keywords {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 16px;
  }}
  .keyword {{
    background: rgba(10,132,255,.18);
    color: #7ab8ff;
    font-size: 11px;
    padding: 3px 10px;
    border-radius: 20px;
    border: 1px solid rgba(10,132,255,.3);
  }}
  .brief-sources {{ margin-top: 14px; }}
  .source-link {{
    display: block;
    font-size: 11.5px;
    color: var(--sub);
    text-decoration: none;
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
    transition: color .15s;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }}
  .source-link:last-child {{ border-bottom: none; }}
  .source-link:hover {{ color: var(--accent); }}
  .source-meta {{ color: #555; margin-right: 6px; }}

  /* ── 뉴스 리스트 ── */
  .section-title {{
    font-size: 12px;
    font-weight: 700;
    color: var(--sub);
    letter-spacing: .8px;
    text-transform: uppercase;
    margin-bottom: 8px;
  }}
  .news-list {{ display: flex; flex-direction: column; }}
  .news-item {{
    display: flex;
    gap: 12px;
    padding: 13px 0;
    border-bottom: 1px solid var(--border);
    text-decoration: none;
    color: var(--text);
    transition: background .1s;
  }}
  .news-item:last-child {{ border-bottom: none; }}
  .news-item:hover .news-title {{ color: var(--accent); }}
  .dot-wrap {{ padding-top: 5px; flex-shrink: 0; }}
  .dot {{
    display: block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
  }}
  .news-body {{ flex: 1; min-width: 0; }}
  .news-title {{
    font-size: 13.5px;
    font-weight: 500;
    line-height: 1.45;
    margin-bottom: 4px;
    transition: color .15s;
  }}
  .news-summary {{
    font-size: 12px;
    color: var(--sub);
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    margin-bottom: 4px;
  }}
  .news-footer {{
    font-size: 11px;
    color: #555;
  }}
  .news-source {{ color: #666; }}

  .empty {{
    text-align: center;
    color: var(--sub);
    padding: 48px 0;
    font-size: 13px;
  }}

  /* ── 범례 ── */
  .legend {{
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    margin-bottom: 16px;
    font-size: 11px;
    color: var(--sub);
    align-items: center;
  }}
  .legend-item {{ display: flex; align-items: center; gap: 5px; }}
  .legend-dot {{ width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }}

  /* ── 반응형 ── */
  @media (max-width: 600px) {{
    main {{ padding: 16px 14px 48px; }}
    .brief-card {{ padding: 16px; }}
  }}
</style>
</head>
<body>

<header>
  <div class="logo">GLB <span>News</span></div>
  <div class="meta">국가별 경제·금융 동향 스냅샷 · 최근 {days}일 기준 · 생성: {now_str}</div>
  <div class="tabs" id="tabs"></div>
</header>

<main id="main"></main>

<script>
const DATA = {data_js};
const CCS  = {json.dumps(cc_list)};

let active = CCS[0];

function dotColor(score, tier) {{
  if (tier === 0) return '#30d158';
  if (score >= 5) return '#ff453a';
  if (score >= 4) return '#ff9f0a';
  if (score >= 3) return '#ffd60a';
  return '#3a3a3c';
}}

function fmtDate(s) {{
  if (!s) return '';
  try {{
    const d = new Date(s);
    return d.toLocaleDateString('ko-KR', {{ month: 'short', day: 'numeric' }});
  }} catch(e) {{ return ''; }}
}}

function renderTabs() {{
  const el = document.getElementById('tabs');
  el.innerHTML = CCS.map(cc => {{
    const m = DATA[cc].meta;
    return `<div class="tab${{cc === active ? ' active' : ''}}" onclick="setTab('${{cc}}')">
      <span class="flag">${{m.flag}}</span>
      <span>${{m.name}}</span>
    </div>`;
  }}).join('');
}}

function renderBriefing(b) {{
  if (!b) return `<p style="color:#555;font-size:13px;padding:20px 0">브리핑 데이터가 없습니다.</p>`;

  const genDate = fmtDate(b.generated_at);
  const issuesHtml = (b.issues || []).map(iss => `
    <div class="issue-item">
      <div class="issue-title">${{iss.title || ''}}</div>
      <div class="issue-detail">${{iss.detail || ''}}</div>
    </div>
  `).join('');

  const kwHtml = (b.keywords || []).map(k =>
    `<span class="keyword">${{k}}</span>`
  ).join('');

  const srcHtml = (b.source_articles || []).slice(0, 8).map(a => {{
    const scoreLabel = a.score ? ` ★${{a.score}}` : '';
    return `<a class="source-link" href="${{a.link || '#'}}" target="_blank" rel="noopener">
      <span class="source-meta">[${{a.source || ''}}${{scoreLabel}}]</span>${{a.title || ''}}
    </a>`;
  }}).join('');

  return `
    <div class="brief-card">
      <div class="brief-header">
        <span class="brief-badge">AI 브리핑</span>
        <span class="brief-date">${{genDate}} 생성 · ${{b.article_count}}건 분석</span>
      </div>
      <div class="brief-summary">${{b.summary || ''}}</div>

      ${{issuesHtml ? `<div class="brief-section-title">주요 이슈</div>
      <div class="issues-list">${{issuesHtml}}</div>` : ''}}

      ${{b.outlook ? `<div class="brief-section-title">전망 및 시사점</div>
      <div class="brief-outlook">${{b.outlook}}</div>` : ''}}

      ${{kwHtml ? `<div class="keywords">${{kwHtml}}</div>` : ''}}

      ${{srcHtml ? `<div class="brief-section-title" style="margin-top:4px">참고 기사</div>
      <div class="brief-sources">${{srcHtml}}</div>` : ''}}
    </div>
  `;
}}

function renderArticles(articles) {{
  if (!articles || articles.length === 0) {{
    return `<div class="empty">수집된 기사가 없습니다.</div>`;
  }}
  const rows = articles.map(a => {{
    const color = dotColor(a.ai_score || 0, a.tier || 1);
    const date  = fmtDate(a.published_at);
    const summary = (a.summary_ko || '').slice(0, 160);
    return `
      <a class="news-item" href="${{a.link || '#'}}" target="_blank" rel="noopener">
        <div class="dot-wrap"><span class="dot" style="background:${{color}}"></span></div>
        <div class="news-body">
          <div class="news-title">${{a.title || ''}}</div>
          ${{summary ? `<div class="news-summary">${{summary}}</div>` : ''}}
          <div class="news-footer">
            <span class="news-source">${{a.media_name || ''}}</span>
            ${{date ? ` · ${{date}}` : ''}}
          </div>
        </div>
      </a>`;
  }}).join('');

  return `<div class="news-list">${{rows}}</div>`;
}}

function render() {{
  const d = DATA[active];
  renderTabs();

  const legendHtml = `
    <div class="legend">
      <span>색상 기준:</span>
      <span class="legend-item"><span class="legend-dot" style="background:#30d158"></span>공식기관</span>
      <span class="legend-item"><span class="legend-dot" style="background:#ff453a"></span>중요도 5</span>
      <span class="legend-item"><span class="legend-dot" style="background:#ff9f0a"></span>중요도 4</span>
      <span class="legend-item"><span class="legend-dot" style="background:#ffd60a"></span>중요도 3</span>
      <span class="legend-item"><span class="legend-dot" style="background:#3a3a3c"></span>기타</span>
    </div>
  `;

  document.getElementById('main').innerHTML =
    renderBriefing(d.briefing) +
    `<div class="section-title">최신 뉴스 (${{(d.articles || []).length}}건)</div>` +
    legendHtml +
    renderArticles(d.articles);
}}

function setTab(cc) {{
  active = cc;
  render();
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
}}

render();
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="GLB News static HTML generator")
    ap.add_argument("--days", type=int, default=3, help="최근 N일 기사 포함 (기본 3)")
    ap.add_argument("-o", "--output", type=str, default=str(ROOT / "data" / "snapshot.html"))
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"❌ DB 없음: {DB_PATH}")
        return

    print(f"📡 데이터 로드 중 (최근 {args.days}일)...")
    payload = load_all_data(args.days)

    total_arts = sum(len(v["articles"]) for v in payload.values())
    briefings  = sum(1 for v in payload.values() if v["briefing"])
    print(f"   국가: {len(payload)}개  브리핑: {briefings}개  기사: {total_arts}건")

    html = build_html(payload, args.days)
    out  = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    size_kb = out.stat().st_size // 1024
    print(f"✅ 생성 완료: {out}  ({size_kb} KB)")
    print()
    print("📤 Netlify Drop 배포 방법:")
    print("   1. https://app.netlify.com/drop 접속")
    print(f"   2. {out} 파일을 드래그 앤 드롭")
    print("   3. 즉시 공개 URL 발급 (예: https://abc123.netlify.app)")


if __name__ == "__main__":
    main()
