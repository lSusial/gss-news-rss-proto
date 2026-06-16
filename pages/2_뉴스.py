"""GLB News RSS — 뉴스 피드 & 브리핑"""
import html as _html
import json as _json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "news.db"

COUNTRIES = [
    ("🌐", "GLOBAL", "글로벌"),
    ("🇺🇸", "US",    "미국"),
    ("🇨🇳", "CN",    "중국"),
    ("🇯🇵", "JP",    "일본"),
    ("🇮🇳", "IN",    "인도"),
    ("🇮🇩", "ID",    "인도네시아"),
    ("🇻🇳", "VN",    "베트남"),
    ("🇰🇭", "KH",    "캄보디아"),
    ("🇲🇲", "MM",    "미얀마"),
]
CC_FLAG = {c[1]: c[0] for c in COUNTRIES}
CC_NAME = {c[1]: c[2] for c in COUNTRIES}

SCORE_COLOR = {5: "#9E2A22", 4: "#D4590A", 3: "#A8841A"}
SCORE_LABEL = {5: "핵심", 4: "중요", 3: "관련"}

NAVY  = "#54504A"
GOLD  = "#FFBC00"
INK   = "#1A1816"
PAPER = "#F7F5F0"
CARD  = "#FFFFFF"
LINE  = "#E4E0D8"
SUB   = "#8A857C"

st.set_page_config(
    page_title="GLB 뉴스",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(f"""
<style>
@import url('https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/pretendard.css');
* {{ font-family:"Pretendard","Noto Sans KR",-apple-system,sans-serif !important; }}

.stApp,[data-testid="stAppViewContainer"] {{ background:{PAPER} !important; }}
section.main > div {{ background:transparent !important; }}
.block-container {{ max-width:500px !important; padding:0 0 60px !important; margin:auto !important; }}

[data-testid="stSidebarNav"],[data-testid="stSidebar"],
header[data-testid="stHeader"],[data-testid="collapsedControl"],
[data-testid="stMainMenu"],footer {{ display:none !important; }}

[data-testid="stHorizontalBlock"] {{ gap:6px !important; margin:0 !important; }}
[data-testid="stHorizontalBlock"] > div {{ padding:0 !important; }}

.stButton button {{
  background:{CARD} !important; color:{INK} !important;
  border:1.5px solid {LINE} !important; border-radius:8px !important;
  font-weight:700 !important; font-size:12px !important;
}}
.stButton button[kind="primary"] {{
  background:{NAVY} !important; color:{GOLD} !important; border-color:{NAVY} !important;
}}

[data-baseweb="select"] > div {{ background:{CARD} !important; border-color:{LINE} !important; }}
[data-baseweb="select"] * {{ color:{INK} !important; }}

div[data-testid="stDateInput"] input {{
  background:{CARD} !important; color:{INK} !important;
  text-align:center !important; border:1px solid {LINE} !important;
  font-weight:700 !important;
}}

[data-testid="stPageLink"] a {{
  justify-content:center !important; color:{INK} !important;
  background:{CARD} !important; border:1px solid {LINE} !important; border-radius:8px !important;
}}
[data-testid="stPageLink"] p {{
  text-align:center !important; width:100% !important;
  color:{INK} !important; font-size:12px !important; font-weight:700 !important;
}}
[data-testid="stExpander"] {{ border-color:{LINE} !important; background:{CARD} !important; }}
summary p {{ color:{SUB} !important; font-size:0.80em !important; }}

@media (max-width:640px) {{
  .block-container {{ padding-left:0 !important; padding-right:0 !important; }}
}}
input,select,textarea,[data-baseweb="select"] * {{ font-size:16px !important; }}
</style>
""", unsafe_allow_html=True)

# ── DB ────────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c

conn = get_conn()
def _e(t): return _html.escape(str(t or ""))
def _md(h): st.markdown(h, unsafe_allow_html=True)

# ── 쿼리 ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=120)
def load_briefing(code, brief_date, brief_type):
    r = conn.execute(
        "SELECT * FROM country_briefings WHERE cc=? AND briefing_date=? AND briefing_type=?",
        (code, brief_date, brief_type),
    ).fetchone()
    return dict(r) if r else None

@st.cache_data(ttl=120)
def load_available_dates(code, brief_type):
    rows = conn.execute("""
        SELECT DISTINCT briefing_date FROM country_briefings
        WHERE cc=? AND briefing_type=?
        ORDER BY briefing_date DESC LIMIT 30
    """, (code, brief_type)).fetchall()
    return [r["briefing_date"] for r in rows]

@st.cache_data(ttl=60)
def load_feed(code, sel_date=None, limit=40):
    where_cc   = "AND m.primary_country_code = ?" if code != "GLOBAL" else ""
    where_date = "AND DATE(datetime(COALESCE(a.published_at, a.fetched_at), '+9 hours')) = ?" if sel_date else ""
    cc_param   = [code]     if code != "GLOBAL" else []
    date_param = [sel_date] if sel_date         else []
    params     = [*cc_param, *date_param, limit]
    rows = conn.execute(f"""
        SELECT m.media_name, m.tier, m.primary_country_code AS cc,
               a.title, a.link, a.published_at,
               a.ai_score, a.summary_ko, a.topics
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision = 'passed'
          AND a.ai_score IS NOT NULL
          AND a.duplicate_of IS NULL
          {where_cc} {where_date}
        ORDER BY CASE WHEN m.tier=0 THEN 0 ELSE 1 END,
                 a.ai_score DESC, a.published_at DESC NULLS LAST
        LIMIT ?
    """, params).fetchall()
    return [dict(r) for r in rows]

def _latest_date():
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        row = conn.execute("""
            SELECT DATE(datetime(COALESCE(published_at, fetched_at), '+9 hours')) AS d
            FROM articles_raw WHERE filter_decision='passed'
              AND ai_score IS NOT NULL AND duplicate_of IS NULL
              AND DATE(datetime(COALESCE(published_at, fetched_at), '+9 hours')) <= ?
            ORDER BY d DESC LIMIT 1
        """, (yesterday,)).fetchone()
        return row["d"] if row else yesterday
    except Exception:
        return yesterday

def fmt_time(iso):
    if not iso: return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - dt).total_seconds()
        if diff < 3600:  return f"{int(diff//60)}분 전"
        if diff < 86400: return f"{int(diff//3600)}시간 전"
        return dt.strftime("%-m/%-d")
    except Exception:
        return (iso or "")[:10]

# ── 렌더러 ────────────────────────────────────────────────────────────────────
def render_page_header(kst_str):
    _md(f"""
<div style='background:{NAVY};padding:12px 20px 10px;position:relative;overflow:hidden'>
  <div style='position:absolute;right:-8px;top:-8px;font-size:72px;font-weight:900;
    color:rgba(255,255,255,.04);line-height:1;pointer-events:none'>뉴스</div>
  <div style='display:flex;align-items:center;justify-content:space-between'>
    <span style='color:{GOLD};font-size:13px;font-weight:900;letter-spacing:.08em'>GLB NEWS</span>
    <span style='color:#8A9BB4;font-size:11px;font-weight:600'>{kst_str}</span>
  </div>
  <h1 style='color:#fff;font-size:20px;font-weight:900;letter-spacing:-.02em;
    line-height:1.2;margin:6px 0 2px'>뉴스 피드</h1>
  <div style='color:#7A8FA8;font-size:10.5px;font-weight:600'>브리핑 · 뉴스 피드 · 실시간</div>
</div>
""")

def render_news_card(a, show_flag=True):
    score = a.get("ai_score") or 0
    tier  = a.get("tier", 1)
    cc    = a.get("cc", "")
    pub   = fmt_time(a.get("published_at"))
    title = _e(a.get("title") or "")
    sumko = _e((a.get("summary_ko") or "")[:120])
    href  = _e(a.get("link") or "#")
    media = _e((a.get("media_name") or "").upper())

    bar_color = SCORE_COLOR.get(score, NAVY)
    tier_badge = (
        f"<span style='font-size:10px;font-weight:700;background:{NAVY};color:{GOLD};"
        f"border-radius:3px;padding:2px 6px'>공식</span>"
    ) if tier == 0 else ""
    flag_html = f"<span style='font-size:12px'>{CC_FLAG.get(cc,'')}</span>" if show_flag else ""
    sc_c = SCORE_COLOR.get(score, "")
    sc_l = SCORE_LABEL.get(score, "")
    sc_badge = (
        f"<span style='font-size:11px;font-weight:700;border-radius:3px;padding:2px 8px;"
        f"background:{sc_c}22;color:{sc_c}'>{sc_l}</span>"
    ) if sc_c else ""

    try:    topics = _json.loads(a.get("topics") or "[]")[:3]
    except: topics = []
    topics_html = "".join(
        f"<span style='font-size:10.5px;font-weight:600;background:#EFEDE8;color:{NAVY};"
        f"border-radius:3px;padding:2px 8px;margin-right:4px'>{_e(t)}</span>"
        for t in topics
    )

    body_row   = (
        f"<div style='font-size:12.5px;color:#44413B;line-height:1.65;margin-top:6px'>{sumko}</div>"
    ) if sumko else ""
    topics_row = f"<div style='margin-top:7px'>{topics_html}</div>" if topics_html else ""

    return (
        f"<a href='{href}' target='_blank' style='text-decoration:none;color:inherit;display:block'>"
        f"<div style='background:{CARD};border:1px solid {LINE};border-left:4px solid {bar_color};"
        f"border-radius:8px;padding:14px 15px;margin-bottom:10px;"
        f"box-shadow:0 1px 4px rgba(26,24,22,.05)'>"
        f"<div style='display:flex;gap:6px;align-items:center;margin-bottom:8px;flex-wrap:wrap'>"
        f"{tier_badge}{flag_html}"
        f"<span style='font-size:11px;color:{SUB}'>{media}</span>"
        f"{sc_badge}"
        f"<span style='font-size:11px;color:{SUB};margin-left:auto'>{pub}</span>"
        f"</div>"
        f"<div style='font-size:14.5px;font-weight:800;line-height:1.4;color:{INK}'>{title}</div>"
        f"{body_row}{topics_row}"
        f"</div></a>"
    )

def render_briefing(brief, cc, sel_date):
    if not brief or not brief.get("summary"): return
    summary  = _e(brief.get("summary",""))
    outlook  = _e(brief.get("outlook",""))
    art_cnt  = brief.get("article_count", 0)
    flag     = CC_FLAG.get(cc,"")
    cname    = CC_NAME.get(cc, cc)
    gen      = fmt_time(brief.get("generated_at",""))
    try:    issues   = _json.loads(brief.get("issues","[]"))
    except: issues   = []
    try:    keywords = _json.loads(brief.get("keywords","[]"))
    except: keywords = []

    cat_bg = {"경제":"#EEF3FB","금융":"#EEF6F1","디지털":"#F0EBFB","금융사고":"#FAECEA","ESG":"#EAF4F0"}
    cat_cl = {"경제":"#1E4D8C","금융":"#1F6142","디지털":"#4E328A","금융사고":"#9E2A22","ESG":"#1A6E54"}

    issues_html = ""
    for iss in issues:
        cat  = _e(iss.get("category","") or "이슈")
        c_bg = cat_bg.get(cat, "#F5F4F0")
        c_cl = cat_cl.get(cat, NAVY)
        issues_html += (
            f"<div style='background:{CARD};border:1px solid {LINE};border-left:4px solid {c_cl};"
            f"border-radius:8px;padding:14px 15px;margin-bottom:10px'>"
            f"<div style='margin-bottom:8px'>"
            f"<span style='font-size:11px;font-weight:700;background:{c_bg};color:{c_cl};"
            f"border-radius:3px;padding:2px 8px'>{cat}</span></div>"
            f"<div style='font-size:14px;font-weight:800;line-height:1.4;color:{INK};margin-bottom:6px'>"
            f"{_e(iss.get('title',''))}</div>"
            f"<div style='font-size:12.5px;color:#44413B;line-height:1.65'>"
            f"{_e(iss.get('detail',''))}</div>"
            f"</div>"
        )

    kw_html = " ".join(
        f"<span style='background:#EFEDE8;color:{NAVY};font-size:10.5px;"
        f"padding:3px 10px;border-radius:99px;font-weight:600'>{_e(k)}</span>"
        for k in keywords
    )
    outlook_html = (
        f"<div style='background:#FFF8E5;border:1px solid #F2E2AE;border-radius:8px;"
        f"padding:14px;margin-bottom:14px'>"
        f"<div style='font-size:10px;font-weight:900;color:#A8841A;letter-spacing:.08em;margin-bottom:8px'>전망 및 시사점</div>"
        f"<div style='font-size:13px;color:#6B5500;line-height:1.7'>{outlook}</div>"
        f"</div>"
    ) if outlook else ""
    kw_row = f"<div style='margin-bottom:12px'>{kw_html}</div>" if kw_html else ""

    _md(f"""
<div style='padding:14px 20px 0'>
  <div style='background:{NAVY};border-radius:10px;padding:16px;margin-bottom:14px'>
    <div style='color:{GOLD};font-size:10px;font-weight:800;letter-spacing:.1em;margin-bottom:8px'>
      {flag} {cname} · {sel_date} · {art_cnt}건 분석</div>
    <div style='color:rgba(255,255,255,.88);font-size:13px;line-height:1.7'>{summary}</div>
  </div>
  <div style='margin-bottom:14px'>{issues_html}</div>
  {outlook_html}{kw_row}
  <div style='font-size:11px;color:{SUB};text-align:right;margin-bottom:8px'>생성: {gen}</div>
</div>
""")

# ── 세션 ──────────────────────────────────────────────────────────────────────
today_str     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
_today        = datetime.now(timezone.utc).date()
_last_monday  = (_today - timedelta(days=_today.weekday()) - timedelta(weeks=1)).isoformat()

if "news_sel"        not in st.session_state: st.session_state.news_sel        = "US"
if "news_type"       not in st.session_state: st.session_state.news_type       = "daily"
if "news_date"       not in st.session_state: st.session_state.news_date       = _latest_date()
if "news_datepicker" not in st.session_state: st.session_state.news_datepicker = date.fromisoformat(st.session_state.news_date)

sel        = st.session_state.news_sel
brief_type = st.session_state.news_type
sel_date   = st.session_state.news_date

is_realtime  = (brief_type == "realtime")
daily_active = (brief_type == "daily")
weekly_active= (brief_type == "weekly")

if weekly_active:
    d = date.fromisoformat(sel_date)
    monday = (d - timedelta(days=d.weekday())).isoformat()
    if monday != sel_date:
        st.session_state.news_date = monday
        sel_date = monday

_sd = date.fromisoformat(sel_date)
if st.session_state.get("news_datepicker") != _sd:
    st.session_state["news_datepicker"] = _sd

# ══════════════════════════════════════════════════════
#  1. 페이지 헤더
# ══════════════════════════════════════════════════════
try:
    kst_now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Seoul"))
    day_ko  = ["월","화","수","목","금","토","일"][kst_now.weekday()]
    kst_str = kst_now.strftime(f"%Y.%m.%d {day_ko}")
except Exception:
    kst_str = today_str

render_page_header(kst_str)
_md("<div style='height:10px'></div>")

# ══════════════════════════════════════════════════════
#  2. NAV (3 탭)
# ══════════════════════════════════════════════════════
n1, n2, n3 = st.columns(3)
with n1: st.page_link("dashboard_stocks.py", label="🏠 대시보드", use_container_width=True)
with n2: st.page_link("pages/2_뉴스.py",      label="📰 뉴스",    use_container_width=True)
with n3: st.page_link("pages/1_테마뷰.py",    label="🎯 테마",    use_container_width=True)

# ══════════════════════════════════════════════════════
#  3. 뷰 타입 버튼
# ══════════════════════════════════════════════════════
t1, t2, t3 = st.columns(3)
with t1:
    if st.button("주간 브리핑", key="btn_weekly", use_container_width=True,
                 type="primary" if weekly_active else "secondary"):
        st.session_state.news_type = "weekly"; st.cache_data.clear(); st.rerun()
with t2:
    if st.button("뉴스 피드", key="btn_daily", use_container_width=True,
                 type="primary" if daily_active else "secondary"):
        st.session_state.news_type = "daily"
        st.session_state.news_date = yesterday_str
        st.cache_data.clear(); st.rerun()
with t3:
    if st.button("실시간", key="btn_realtime", use_container_width=True,
                 type="primary" if is_realtime else "secondary"):
        st.session_state.news_type = "realtime"; st.cache_data.clear(); st.rerun()

# ══════════════════════════════════════════════════════
#  4. 날짜 네비게이션
# ══════════════════════════════════════════════════════
if not is_realtime:
    nav_step = timedelta(weeks=1) if weekly_active else timedelta(days=1)
    n1, n2, n3 = st.columns([1, 4, 1])
    with n1:
        if st.button("◀", key="btn_prev", use_container_width=True):
            st.session_state.news_date = (date.fromisoformat(sel_date) - nav_step).isoformat()
            st.rerun()
    with n2:
        picked = st.date_input("날짜", key="news_datepicker",
                               label_visibility="collapsed", format="YYYY-MM-DD")
        if picked and picked.isoformat() != sel_date:
            st.session_state.news_date = picked.isoformat(); st.rerun()
    with n3:
        if st.button("▶", key="btn_next", use_container_width=True):
            nxt = (date.fromisoformat(sel_date) + nav_step).isoformat()
            if nxt <= yesterday_str:
                st.session_state.news_date = nxt; st.rerun()

# ══════════════════════════════════════════════════════
#  5. 국가 선택
# ══════════════════════════════════════════════════════
country_labels = [f"{f} {n}" for f, _, n in COUNTRIES]
country_codes  = [c for _, c, _ in COUNTRIES]
cur_idx = country_codes.index(sel) if sel in country_codes else 0
chosen  = st.selectbox("국가", country_labels, index=cur_idx, label_visibility="collapsed")
chosen_code = country_codes[country_labels.index(chosen)]
if chosen_code != sel:
    st.session_state.news_sel = chosen_code
    st.cache_data.clear(); st.rerun()

_md(f"<div style='height:1px;background:{LINE};margin:8px 20px'></div>")

# ══════════════════════════════════════════════════════
#  6. 브리핑 + 뉴스 피드
# ══════════════════════════════════════════════════════
feed_date = today_str if is_realtime else sel_date

if is_realtime:
    _md(
        f"<div style='background:#EEF6F1;border-left:4px solid #1F6142;"
        f"border-radius:8px;padding:12px 16px;margin:0 20px 12px'>"
        f"<span style='color:#1F6142;font-size:13px;font-weight:700'>"
        f"📡 실시간 뉴스 — 오늘 수집 기사</span></div>"
    )
else:
    type_label  = "일일 브리핑" if brief_type == "daily" else "주간 브리핑"
    avail_dates = load_available_dates(chosen_code, brief_type)
    brief = load_briefing(chosen_code, sel_date, brief_type)

    if brief:
        render_briefing(brief, chosen_code, sel_date)
    elif avail_dates:
        _md(
            f"<div style='background:{PAPER};border:1px solid {LINE};"
            f"border-radius:8px;padding:14px 18px;margin:0 20px 12px;"
            f"color:{SUB};font-size:13px'>📭 {sel_date} {type_label}가 없습니다. "
            f"가장 최근: <b style='color:{INK}'>{avail_dates[0]}</b></div>"
        )
    else:
        _md(
            f"<div style='background:{PAPER};border:1px solid {LINE};"
            f"border-radius:8px;padding:14px 18px;margin:0 20px 12px;"
            f"color:{SUB};font-size:13px'>📭 {type_label}가 아직 없습니다.</div>"
        )

articles = load_feed(chosen_code, feed_date, 40)
if articles:
    _md(
        f"<div style='font-size:11px;font-weight:900;letter-spacing:.06em;color:{SUB};"
        f"padding:12px 20px 4px'>뉴스 목록 · {len(articles)}건</div>"
    )
    cards_html = "".join(render_news_card(a, show_flag=(chosen_code=="GLOBAL")) for a in articles)
    _md(f"<div style='padding:4px 20px'>{cards_html}</div>")
else:
    _md(
        f"<div style='text-align:center;padding:40px 20px;color:{SUB};font-size:14px'>"
        f"해당 날짜 기사가 없습니다.</div>"
    )

_md(
    f"<div style='text-align:center;padding:20px;border-top:1px solid {LINE};margin-top:8px'>"
    f"<span style='font-size:10px;font-weight:800;letter-spacing:.2em;color:{NAVY};opacity:.3'>"
    f"GLB NEWS RSS PROTOTYPE</span></div>"
)
