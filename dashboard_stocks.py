"""
GLB News RSS — Apple Stocks 스타일 대시보드 v3
실행: streamlit run dashboard_stocks.py
"""
import html as _html
import json as _json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

DB_PATH = Path(__file__).parent / "data" / "news.db"

COUNTRIES = [
    ("🌐", "GLOBAL", "All"),
    ("🇺🇸", "US",    "USA"),
    ("🇨🇳", "CN",    "CHN"),
    ("🇯🇵", "JP",    "JPN"),
    ("🇮🇳", "IN",    "IND"),
    ("🇮🇩", "ID",    "IDN"),
    ("🇻🇳", "VN",    "VNM"),
    ("🇰🇭", "KH",    "KHM"),
    ("🇲🇲", "MM",    "MMR"),
]
CC_FLAG = {c[1]: c[0] for c in COUNTRIES}
CC_NAME = {c[1]: c[2] for c in COUNTRIES}

SCORE_COLOR = {5: "#ff453a", 4: "#ff9f0a", 3: "#ffd60a"}
SCORE_LABEL = {5: "핵심", 4: "중요", 3: "관련"}

st.set_page_config(
    page_title="GLB News",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.stApp,[data-testid="stAppViewContainer"],
[data-testid="stHeader"],[data-testid="stToolbar"] {
  background:#000 !important;
}
section.main > div { background:#000 !important; }
.block-container { max-width:1040px; padding:0 1.4rem 3rem; margin:auto; }

/* pill 버튼 — 국기(위) + 국가명(아래) 2줄 */
div[data-testid="stHorizontalBlock"] > div > div > div > button {
  border-radius:14px !important;
  border:1px solid #2c2c2e !important;
  background:#1c1c1e !important;
  color:#ebebf5 !important;
  font-size:0.62em !important;
  padding:6px 4px !important;
  font-weight:500 !important;
  height:auto !important; min-height:50px !important;
  white-space:pre-line !important;
  line-height:1.5 !important;
}
/* 첫째 줄(국기 이모지)은 크게, 둘째 줄(국가명)은 위 font-size 그대로 */
div[data-testid="stHorizontalBlock"] > div > div > div > button::first-line {
  font-size:1.65em !important;
}
div[data-testid="stHorizontalBlock"] > div > div > div > button:hover {
  background:#2c2c2e !important;
}

/* metric */
[data-testid="metric-container"] {
  background:#111 !important; border-radius:12px !important;
  padding:12px 16px !important;
}
[data-testid="stMetricLabel"] { color:#636366 !important; font-size:0.70em !important; }
[data-testid="stMetricValue"] { color:#fff !important; font-size:1.25em !important; font-weight:700 !important; }
[data-testid="stMetricDelta"] { display:none !important; }

[data-testid="stSidebarNav"],[data-testid="stSidebar"] { display:none !important; }
hr { border-color:#1c1c1e !important; }
summary p { color:#636366 !important; font-size:0.80em !important; }

/* 카드 hover */
.news-card { transition: opacity 0.15s ease; }
.news-card:hover { opacity: 0.85; }

/* 카드 행 세로 간격 압축 */
[data-testid="stHorizontalBlock"] [data-testid="stVerticalBlockBorderWrapper"] {
  padding-bottom:0 !important;
  margin-bottom:0 !important;
}
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {
  padding-bottom:2px !important;
}
</style>
""", unsafe_allow_html=True)

# ── DB ────────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

conn = get_conn()
def _e(t): return _html.escape(str(t or ""))

# ── 쿼리 ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=120)
def load_briefing(code: str, brief_date: str, brief_type: str) -> dict | None:
    r = conn.execute(
        "SELECT * FROM country_briefings WHERE cc=? AND briefing_date=? AND briefing_type=?",
        (code, brief_date, brief_type),
    ).fetchone()
    return dict(r) if r else None

@st.cache_data(ttl=120)
def load_available_dates(code: str, brief_type: str) -> list[str]:
    """해당 국가·타입의 브리핑이 있는 날짜 목록 (최신순)."""
    rows = conn.execute("""
        SELECT DISTINCT briefing_date FROM country_briefings
        WHERE cc = ? AND briefing_type = ?
        ORDER BY briefing_date DESC
        LIMIT 30
    """, (code, brief_type)).fetchall()
    return [r["briefing_date"] for r in rows]

@st.cache_data(ttl=60)
def load_feed(code: str, sel_date: str | None = None, limit: int = 40) -> list[dict]:
    where_cc   = "AND m.primary_country_code = ?" if code != "GLOBAL" else ""
    if sel_date:
        where_date = f"AND DATE(COALESCE(a.published_at, a.fetched_at)) = '{sel_date}'"
    else:
        where_date = ""
    params = [code, limit] if code != "GLOBAL" else [limit]
    rows = conn.execute(f"""
        SELECT m.media_name, m.tier, m.primary_country_code AS cc,
               a.title, a.link, a.published_at, a.fetched_at,
               a.ai_score, a.summary_ko, a.filter_reason
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision = 'passed'
          AND a.ai_score IS NOT NULL
          {where_cc}
          {where_date}
        ORDER BY
          CASE WHEN m.tier = 0 THEN 0 ELSE 1 END ASC,
          a.ai_score DESC,
          a.published_at DESC NULLS LAST
        LIMIT ?
    """, params).fetchall()
    return [dict(r) for r in rows]

@st.cache_data(ttl=60)
def load_admin_stats(code: str) -> dict:
    where = "AND m.primary_country_code = ?" if code != "GLOBAL" else ""
    params = [code] if code != "GLOBAL" else []
    r = conn.execute(f"""
        SELECT
          COUNT(*) FILTER (WHERE a.filter_decision='passed')        AS passed,
          COUNT(*) FILTER (WHERE a.filter_decision='rejected')      AS rejected,
          COUNT(*) FILTER (WHERE a.ai_score IS NOT NULL)            AS ai_done,
          COUNT(*) FILTER (WHERE a.ai_score IS NULL
                           AND a.filter_decision='passed')          AS ai_pending,
          COUNT(*) FILTER (WHERE a.ai_score = 5)                    AS s5,
          COUNT(*) FILTER (WHERE a.ai_score = 4)                    AS s4,
          COUNT(*) FILTER (WHERE a.ai_score = 3)                    AS s3,
          COUNT(*) FILTER (WHERE a.filter_reason='official_source') AS official
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE 1=1 {where}
    """, params).fetchone()
    return dict(r) if r else {}

@st.cache_data(ttl=60)
def load_source_stats(code: str) -> list[dict]:
    where = "AND m.primary_country_code = ?" if code != "GLOBAL" else ""
    params = [code] if code != "GLOBAL" else []
    rows = conn.execute(f"""
        SELECT m.media_name, m.tier, m.primary_country_code AS cc,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE a.filter_decision='passed') AS passed
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE 1=1 {where}
        GROUP BY m.media_name, m.tier, m.primary_country_code
        ORDER BY m.tier ASC, passed DESC
    """, params).fetchall()
    return [dict(r) for r in rows]

def fmt_time(iso: str | None) -> str:
    if not iso: return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - dt).total_seconds()
        if diff < 3600:  return f"{int(diff//60)}분 전"
        if diff < 86400: return f"{int(diff//3600)}시간 전"
        return dt.strftime("%-m/%-d")
    except Exception:
        return (iso or "")[:10]

# ── 세션 ──────────────────────────────────────────────────────────────────────
today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
if "sel"        not in st.session_state: st.session_state.sel        = "US"
if "brief_type" not in st.session_state: st.session_state.brief_type = "weekly"
if "sel_date"   not in st.session_state: st.session_state.sel_date   = yesterday_str

# ── 헤더 ──────────────────────────────────────────────────────────────────────
today_disp = datetime.now(timezone.utc).strftime("%Y. %-m. %-d.")
st.html(f"""
<div style="padding:14px 0 6px;display:flex;
            justify-content:space-between;align-items:center">
  <span style="font-size:1.35em;font-weight:700;color:#fff;letter-spacing:-0.5px">
    GLB News
  </span>
  <span style="font-size:0.72em;color:#636366">{today_disp}</span>
</div>
""")

# ── Row 1: 뷰 타입 + 날짜 네비게이션 (최상단) ────────────────────────────────
sel_date   = st.session_state.sel_date
brief_type = st.session_state.brief_type

is_realtime   = (brief_type == "realtime")
daily_active  = (brief_type == "daily")
weekly_active = (brief_type == "weekly")

# 1주 모드: sel_date를 해당 주 월요일로 스냅
if weekly_active:
    d = date.fromisoformat(sel_date)
    monday = d - timedelta(days=d.weekday())
    if monday.isoformat() != sel_date:
        st.session_state.sel_date = monday.isoformat()
        sel_date = monday.isoformat()

# date_picker 위젯 상태를 sel_date 기준으로 단방향 동기화
# (value= 파라미터 충돌 방지: session_state만 사용)
if daily_active:
    _sd = date.fromisoformat(sel_date)
    if st.session_state.get("date_picker") != _sd:
        st.session_state["date_picker"] = _sd

# [일일][1주][실시간] | [◀] [날짜] [▶]
t1, t2, t3, gap, n1, n2, n3 = st.columns([1, 1, 1, 0.3, 1, 2, 1])

with t1:
    if st.button("일일", key="btn_daily", use_container_width=True,
                 type="primary" if daily_active else "secondary"):
        st.session_state.brief_type = "daily"
        st.session_state.sel_date   = yesterday_str   # 항상 전날로 이동
        st.cache_data.clear()
        st.rerun()

with t2:
    if st.button("1주", key="btn_weekly", use_container_width=True,
                 type="primary" if weekly_active else "secondary"):
        st.session_state.brief_type = "weekly"
        st.cache_data.clear()
        st.rerun()

with t3:
    if st.button("실시간", key="btn_realtime", use_container_width=True,
                 type="primary" if is_realtime else "secondary"):
        st.session_state.brief_type = "realtime"
        st.cache_data.clear()
        st.rerun()

nav_step = timedelta(days=7) if weekly_active else timedelta(days=1)

with n1:
    if st.button("◀", key="btn_prev",
                 use_container_width=True, disabled=is_realtime):
        prev = date.fromisoformat(sel_date) - nav_step
        st.session_state.sel_date = prev.isoformat()
        st.cache_data.clear()
        st.rerun()

with n2:
    if is_realtime:
        st.html(
            "<div style='text-align:center;padding:8px 0;"
            "color:#30d158;font-size:0.82em;font-weight:600'>📡 실시간</div>"
        )
    elif weekly_active:
        # 주간: 월~일 범위 표시
        mon = date.fromisoformat(sel_date)
        sun = mon + timedelta(days=6)
        week_label = f"{mon.strftime('%-m/%-d')}(월) ~ {sun.strftime('%-m/%-d')}(일)"
        st.html(
            f"<div style='text-align:center;padding:8px 0;"
            f"color:#ebebf5;font-size:0.80em;font-weight:500'>{week_label}</div>"
        )
    else:
        # value= 미사용 — session_state["date_picker"]로만 제어 (충돌 방지)
        picked = st.date_input(
            "날짜",
            label_visibility="collapsed",
            key="date_picker"
        )
        picked_str = picked.isoformat()
        if picked_str != sel_date:
            st.session_state.sel_date = picked_str
            st.cache_data.clear()
            st.rerun()

with n3:
    if st.button("▶", key="btn_next",
                 use_container_width=True, disabled=is_realtime):
        nxt = date.fromisoformat(sel_date) + nav_step
        if nxt.isoformat() <= yesterday_str:
            st.session_state.sel_date = nxt.isoformat()
            st.cache_data.clear()
            st.rerun()

st.html("<div style='height:8px;border-top:1px solid #1c1c1e;margin-top:6px'></div>")

# ── Row 2: 국가 pill ──────────────────────────────────────────────────────────
sel = st.session_state.sel

pill_cols = st.columns(len(COUNTRIES))
for col, (flag, code, name) in zip(pill_cols, COUNTRIES):
    with col:
        btn_type = "primary" if code == sel else "secondary"
        if st.button(f"{flag}\n{name}", key=f"p_{code}",
                     use_container_width=True, type=btn_type):
            st.session_state.sel = code
            st.cache_data.clear()
            st.rerun()

st.markdown("""
<style>
div[data-testid="stHorizontalBlock"] {
  gap:6px !important; margin-bottom:0 !important;
}
div[data-testid="stHorizontalBlock"] > div { padding:0 !important; }
</style>
""", unsafe_allow_html=True)

st.html("<div style='height:10px;border-top:1px solid #1c1c1e;margin-top:8px'></div>")

# ── 동향 브리핑 카드 ──────────────────────────────────────────────────────────
if is_realtime:
    # 실시간 모드: 브리핑 없이 뉴스만
    st.html(
        "<div style='background:#0d1a0d;border:1px solid #1a3a1a;border-radius:12px;"
        "padding:12px 18px;margin-bottom:12px;display:flex;align-items:center;gap:10px'>"
        "<span style='color:#30d158;font-size:1em'>📡</span>"
        "<div>"
        "<span style='color:#30d158;font-size:0.78em;font-weight:700;"
        "letter-spacing:1px'>실시간 뉴스</span>"
        "<span style='color:#48484a;font-size:0.72em;margin-left:10px'>"
        "오늘 수집 기사 · 브리핑 없음</span>"
        "</div>"
        "</div>"
    )
    brief = None
else:
    type_label  = "일일 브리핑" if brief_type == "daily" else "주간 브리핑"
    avail_dates = load_available_dates(sel, brief_type)
    brief = load_briefing(sel, sel_date, brief_type)
    if not brief:
        if avail_dates:
            latest = avail_dates[0]
            st.html(
                f"<div style='background:#1c1c1e;border-radius:12px;padding:14px 18px;"
                f"margin-bottom:12px;color:#8e8e93;font-size:0.85em'>"
                f"📭 {sel_date} {type_label}가 없습니다. "
                f"가장 최근 브리핑: <b style='color:#ebebf5'>{latest}</b></div>"
            )
        else:
            st.html(
                f"<div style='background:#1c1c1e;border-radius:12px;padding:14px 18px;"
                f"margin-bottom:12px;color:#8e8e93;font-size:0.85em'>"
                f"📭 {type_label}가 아직 없습니다. "
                f"<code>python main.py brief --type {brief_type}</code>를 실행하세요.</div>"
            )

if brief and brief.get("summary"):
    gen_time = fmt_time(brief.get("generated_at", ""))
    summary  = _e(brief.get("summary", ""))
    outlook  = _e(brief.get("outlook", ""))
    art_cnt  = brief.get("article_count", 0)

    try: issues   = _json.loads(brief.get("issues", "[]"))
    except Exception: issues = []
    try: keywords = _json.loads(brief.get("keywords", "[]"))
    except Exception: keywords = []
    try: src_arts = _json.loads(brief.get("source_articles", "[]"))
    except Exception: src_arts = []

    # 주요 이슈 — 제목 + 상세내용 세로 나열
    issues_html = "".join(
        f"<div style='margin-bottom:14px;padding-bottom:14px;"
        f"border-bottom:1px solid #1a3a5c'>"
        f"<div style='color:#fff;font-size:0.88em;font-weight:600;"
        f"margin-bottom:5px'>{_e(iss.get('title',''))}</div>"
        f"<div style='color:#8e8e93;font-size:0.83em;line-height:1.7'>"
        f"{_e(iss.get('detail',''))}</div>"
        f"</div>"
        for iss in issues
    )

    # 키워드
    kw_html = " ".join(
        f"<span style='background:#2c2c2e;color:#ebebf5;font-size:0.70em;"
        f"padding:3px 10px;border-radius:99px'>{_e(k)}</span>"
        for k in keywords
    )

    # 관련 기사 링크 (score 내림차순, 최대 10개)
    src_sorted = sorted(src_arts, key=lambda x: x.get("score", 0), reverse=True)[:10]
    SCORE_DOT = {5: "#ff453a", 4: "#ff9f0a", 3: "#ffd60a"}
    links_html = "".join(
        f"<div style='display:flex;align-items:baseline;gap:8px;padding:6px 0;"
        f"border-bottom:1px solid #111'>"
        f"<span style='color:{SCORE_DOT.get(a.get('score',0), '#48484a')};"
        f"font-size:0.7em;flex-shrink:0'>●</span>"
        f"<div>"
        f"<a href='{_e(a.get('link','#'))}' target='_blank' "
        f"style='color:#ebebf5;font-size:0.83em;font-weight:400;line-height:1.5;"
        f"text-decoration:none'>{_e(a.get('title',''))}</a>"
        f"<span style='color:#48484a;font-size:0.72em;margin-left:8px'>"
        f"{_e(a.get('source',''))}</span>"
        f"</div>"
        f"</div>"
        for a in src_sorted
    )

    # 섹션 조립
    issues_sec  = (
        f"<div style='margin-bottom:16px'>{issues_html}</div>"
    ) if issues_html else ""
    outlook_sec = (
        f"<div style='background:#0d1f38;border-radius:10px;"
        f"padding:12px 16px;margin-bottom:16px'>"
        f"<div style='color:#0a84ff;font-size:0.72em;font-weight:700;"
        f"letter-spacing:0.8px;text-transform:uppercase;margin-bottom:6px'>"
        f"전망 &amp; 시사점</div>"
        f"<div style='font-size:0.85em;line-height:1.75;color:#ebebf5'>{outlook}</div>"
        f"</div>"
    ) if outlook else ""
    kw_sec = (
        f"<div style='display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px'>{kw_html}</div>"
    ) if kw_html else ""
    links_sec = (
        f"<div style='margin-top:4px'>"
        f"<div style='color:#636366;font-size:0.68em;font-weight:700;"
        f"letter-spacing:1px;text-transform:uppercase;margin-bottom:8px'>"
        f"참고 기사</div>"
        f"{links_html}</div>"
    ) if links_html else ""

    st.html(
        f"<div style='background:#0a1628;border:1px solid #1a3a5c;"
        f"border-radius:16px;padding:22px 24px;margin-bottom:16px'>"
        # 헤더
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:center;margin-bottom:16px'>"
        f"<div style='display:flex;align-items:center;gap:10px'>"
        f"<span style='font-size:0.68em;font-weight:700;color:#0a84ff;"
        f"letter-spacing:1.2px;text-transform:uppercase'>{_e(type_label)}</span>"
        f"<span style='font-size:0.62em;color:#48484a'>· {sel_date} · {art_cnt}건 분석</span>"
        f"</div>"
        f"<span style='font-size:0.62em;color:#48484a'>{gen_time} 생성</span>"
        f"</div>"
        # 종합 요약
        f"<div style='font-size:0.88em;line-height:1.8;color:#ebebf5;"
        f"margin-bottom:18px;padding-bottom:16px;border-bottom:1px solid #1a3a5c'>"
        f"{summary}</div>"
        # 주요 이슈
        f"{issues_sec}"
        # 전망
        f"{outlook_sec}"
        # 키워드
        f"{kw_sec}"
        # 관련 기사 링크
        f"{links_sec}"
        f"</div>"
    )

# ── 뉴스 리스트 (주간 모드는 브리핑만, 뉴스 목록 불필요) ─────────────────────
if weekly_active:
    articles = []
else:
    feed_date = today_str if is_realtime else sel_date
    articles  = load_feed(sel, sel_date=feed_date, limit=40)

if not articles:
    st.html("""
    <div style="text-align:center;padding:80px 0">
      <div style="font-size:3em;margin-bottom:12px">📭</div>
      <div style="font-size:1em;font-weight:500;color:#8e8e93">표시할 기사가 없습니다</div>
      <div style="font-size:0.82em;margin-top:8px;color:#636366">
        python main.py fetch → filter → ai-rank 순서로 실행하세요
      </div>
    </div>
    """)
else:
    show_flag = (sel == "GLOBAL")
    rows_html = ""
    for art in articles:
        score  = art.get("ai_score") or 0
        is_off = (art.get("tier") == 0)
        media  = _e((art.get("media_name") or "").upper())
        title  = _e(art.get("title") or "")
        href   = _e(art.get("link") or "#")
        sumko  = _e(art.get("summary_ko") or "")
        pub    = fmt_time(art.get("published_at") or art.get("fetched_at"))
        cc     = art.get("cc", "")

        # 앞머리 색상 점: 공식=초록, 점수별
        if is_off:       dot_color = "#30d158"
        elif score == 5: dot_color = "#ff453a"
        elif score == 4: dot_color = "#ff9f0a"
        elif score == 3: dot_color = "#ffd60a"
        else:            dot_color = "#3a3a3c"

        cc_tag   = f"<span style='margin-right:5px'>{CC_FLAG.get(cc,'')}</span>" if show_flag else ""
        sumko_bl = (
            f"<div style='margin-top:5px;font-size:0.82em;line-height:1.65;color:#8e8e93'>"
            f"{sumko}</div>"
        ) if sumko else ""

        rows_html += (
            f"<a href='{href}' target='_blank' style='text-decoration:none;display:block'>"
            f"<div style='display:flex;gap:12px;padding:13px 0;"
            f"border-bottom:1px solid #1c1c1e;"
            f"transition:opacity 0.12s'"
            f"onmouseover=\"this.style.opacity='0.75'\""
            f"onmouseout=\"this.style.opacity='1'\">"
            # 왼쪽 색상 점
            f"<div style='padding-top:4px;flex-shrink:0'>"
            f"<span style='display:block;width:7px;height:7px;border-radius:50%;"
            f"background:{dot_color}'></span>"
            f"</div>"
            # 본문
            f"<div style='flex:1;min-width:0'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;margin-bottom:3px'>"
            f"<span style='font-size:0.65em;font-weight:600;color:#636366;"
            f"letter-spacing:0.4px'>{cc_tag}{media}</span>"
            f"<span style='font-size:0.65em;color:#48484a;margin-left:8px;"
            f"white-space:nowrap'>{pub}</span>"
            f"</div>"
            f"<div style='font-size:0.92em;font-weight:500;color:#fff;line-height:1.5'>"
            f"{title}</div>"
            f"{sumko_bl}"
            f"</div>"
            f"</div>"
            f"</a>"
        )

    st.html(f"<div>{rows_html}</div>")

# ── 하단 ──────────────────────────────────────────────────────────────────────
st.html("<div style='height:8px'>")
st.divider()

c1, _ = st.columns([1, 5])
with c1:
    if st.button("↻  새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── 관리자 통계 ───────────────────────────────────────────────────────────────
with st.expander("🔧 관리자 통계", expanded=False):
    st.html("<div style='height:4px'>")
    stat = load_admin_stats(sel)

    m = st.columns(5)
    m[0].metric("수집 기사",  stat.get("passed",    0))
    m[1].metric("필터 제외",  stat.get("rejected",  0))
    m[2].metric("AI 분석",   stat.get("ai_done",   0))
    m[3].metric("분석 대기",  stat.get("ai_pending",0))
    m[4].metric("공식 발표",  stat.get("official",  0))

    st.html("<div style='height:10px'>")
    st.markdown("**점수 분포**")
    total_ai = max(stat.get("ai_done", 0) or 0, 1)
    d = st.columns(3)
    for col, (sc, lbl) in zip(d, [(5,"핵심"),(4,"중요"),(3,"관련")]):
        cnt = stat.get(f"s{sc}", 0) or 0
        col.metric(f"score {sc} {lbl}", cnt, delta=f"{cnt/total_ai*100:.0f}%")

    st.html("<div style='height:10px'>")
    st.markdown("**매체별 현황**")
    sources = load_source_stats(sel)
    if sources:
        rows_html = "".join(
            f"<tr>"
            f"<td style='padding:5px 10px;color:#8e8e93;font-size:0.78em'>"
            f"{'🟢 ' if s['tier']==0 else ''}{CC_FLAG.get(s.get('cc',''),'')} {_e(s['media_name'])}"
            f"</td>"
            f"<td style='padding:5px 10px;text-align:right;color:#636366;font-size:0.78em'>T{s['tier']}</td>"
            f"<td style='padding:5px 10px;text-align:right;color:#fff;font-size:0.78em'>"
            f"{s.get('passed',0)}/{s.get('total',0)}</td>"
            f"<td style='padding:5px 10px;width:120px'>"
            f"<div style='background:#111;border-radius:3px;height:4px'>"
            f"<div style='width:{(s.get('passed',0) or 0)/(s.get('total',1) or 1)*100:.0f}%;"
            f"height:100%;background:#48484a;border-radius:3px'></div></div></td>"
            f"</tr>"
            for s in sources
        )
        st.html(f"""
        <table style="width:100%;border-collapse:collapse">
          <thead><tr style="border-bottom:1px solid #2c2c2e">
            <th style="padding:5px 10px;text-align:left;color:#636366;font-size:0.72em">매체</th>
            <th style="padding:5px 10px;text-align:right;color:#636366;font-size:0.72em">Tier</th>
            <th style="padding:5px 10px;text-align:right;color:#636366;font-size:0.72em">통과/전체</th>
            <th style="padding:5px 10px;color:#636366;font-size:0.72em">비율</th>
          </tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
        """)
