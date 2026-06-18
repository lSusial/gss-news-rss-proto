"""GLB News — 테마별 기사 뷰"""
import html as _html
import json
import re as _re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "news.db"

CC_FLAG = {
    "US":"🇺🇸","CN":"🇨🇳","JP":"🇯🇵","IN":"🇮🇳",
    "ID":"🇮🇩","VN":"🇻🇳","KH":"🇰🇭","MM":"🇲🇲","GLOBAL":"🌐",
}

NAVY  = "#54504A"
GOLD  = "#FFBC00"
INK   = "#1A1816"
PAPER = "#F7F5F0"
CARD  = "#FFFFFF"
LINE  = "#E4E0D8"
SUB   = "#8A857C"

THEMES = {
    "esg": {
        "label": "🌱 ESG",
        "accent": "#1A6E54",
        "bg":     "#EAF4F0",
        "topic_kw": ["ESG","탄소","환경","기후","넷제로","그린","재생에너지","지속가능","green","carbon"],
        "title_kw": ["ESG","탄소","기후","환경오염","그린워싱","carbon","climate","emission",
                     "sustainability","green bond","넷제로","탄소중립"],
        "reason_kw":["esg","carbon","climate","green bond","net zero","emission"],
    },
    "regulation": {
        "label": "⚖️ 규제",
        "accent": "#1E4D8C",
        "bg":     "#EEF3FB",
        "topic_kw": ["규제","제재","감독","컴플라이언스","반독점","법안","규정","금융감독"],
        "title_kw": ["규제","제재","감독","금지","법안","규정","단속","조사","처벌",
                     "regulation","sanction","compliance","antitrust","ban","crackdown"],
        "reason_kw":["regulation","sanction","antitrust","compliance","deregulation"],
    },
    "risk": {
        "label": "⚠️ 리스크",
        "accent": "#9E2A22",
        "bg":     "#FAECEA",
        "topic_kw": ["리스크","위기","급락","폭락","붕괴","불안","쇼크","위험","충격"],
        "title_kw": ["리스크","위기","급락","폭락","붕괴","불안","충격","쇼크","위협",
                     "risk","crisis","crash","collapse","plunge","tumble","shock","warning","alert"],
        "reason_kw":["recession","default","bankruptcy","sovereign debt"],
    },
}

st.set_page_config(
    page_title="GLB 테마뷰",
    page_icon="🎯",
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

[data-testid="stPageLink"] a {{
  justify-content:center !important; color:{INK} !important;
  background:{CARD} !important; border:1px solid {LINE} !important; border-radius:8px !important;
}}
[data-testid="stPageLink"] p {{
  text-align:center !important; width:100% !important;
  color:{INK} !important; font-size:12px !important; font-weight:700 !important;
}}

hr {{ border-color:{LINE} !important; }}

@media (max-width:640px) {{
  .block-container {{ padding-left:0 !important; padding-right:0 !important; }}
  [data-testid="stHorizontalBlock"] {{
    flex-direction:row !important; flex-wrap:nowrap !important;
  }}
  [data-testid="stHorizontalBlock"] > div {{ min-width:0 !important; flex:1 !important; }}
}}
input,select,textarea,[data-baseweb="select"] * {{ font-size:16px !important; }}
</style>
""", unsafe_allow_html=True)

# ── DB ────────────────────────────────────────────────────────────────────────
@st.cache_resource
def get_conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

conn = get_conn()
def _e(t):
    t = _html.unescape(str(t or ""))
    t = _re.sub(r"<[^>]+>", "", t)
    return _html.escape(t)
def _md(h): st.markdown(h, unsafe_allow_html=True)

# ── 쿼리 ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=120)
def load_theme(theme_key: str, since_date: str, min_score: int, limit: int) -> list[dict]:
    th = THEMES[theme_key]
    topic_conds  = " OR ".join(f"a.topics LIKE ?" for _ in th["topic_kw"])
    title_conds  = " OR ".join(f"a.title LIKE ?" for _ in th["title_kw"])
    reason_conds = " OR ".join(f"a.filter_reason LIKE ?" for _ in th["reason_kw"])
    params = (
        [f"%{k}%" for k in th["topic_kw"]] +
        [f"%{k}%" for k in th["title_kw"]] +
        [f"%{k}%" for k in th["reason_kw"]] +
        [since_date, min_score, limit]
    )
    rows = conn.execute(f"""
        SELECT a.article_id, a.title, a.link, a.summary_ko, a.ai_score,
               a.published_at, a.topics,
               m.media_name, m.tier, m.primary_country_code AS cc
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision = 'passed'
          AND a.duplicate_of IS NULL
          AND (a.llm_prefilter IS NULL OR a.llm_prefilter = 'passed')
          AND ({topic_conds} OR {title_conds} OR {reason_conds})
          AND DATE(datetime(COALESCE(a.published_at, a.fetched_at), '+9 hours')) >= ?
          AND a.ai_score >= ?
        ORDER BY a.ai_score DESC, a.published_at DESC NULLS LAST
        LIMIT ?
    """, params).fetchall()
    return [dict(r) for r in rows]

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

def render_article_card(a, accent):
    score = a.get("ai_score") or 0
    cc    = a.get("cc","")
    flag  = CC_FLAG.get(cc,"")
    pub   = fmt_time(a.get("published_at"))
    title = _e(a.get("title") or "")
    sumko = _e((a.get("summary_ko") or "")[:100])
    href  = _e(a.get("link") or "#")
    media = _e((a.get("media_name") or "").upper())
    tier  = a.get("tier", 1)

    score_color = {5:"#9E2A22", 4:"#D4590A", 3:"#A8841A"}.get(score, SUB)
    score_label = {5:"핵심", 4:"중요", 3:"관련"}.get(score, "")

    tier_badge = (
        f"<span style='font-size:10px;font-weight:700;background:{NAVY};color:{GOLD};"
        f"border-radius:3px;padding:2px 6px'>공식</span>"
    ) if tier == 0 else ""
    sc_badge = (
        f"<span style='font-size:10px;font-weight:700;background:{score_color}22;"
        f"color:{score_color};border-radius:3px;padding:2px 6px'>{score_label}</span>"
    ) if score_label else ""

    try:    topics = json.loads(a.get("topics") or "[]")[:3]
    except: topics = []
    tags = "".join(
        f"<span style='font-size:10px;font-weight:600;background:#EFEDE8;color:{NAVY};"
        f"border-radius:3px;padding:2px 7px;margin-right:4px'>{_e(t)}</span>"
        for t in topics
    )

    sumko_html = (
        f"<div style='font-size:12px;color:{SUB};line-height:1.55;margin-top:5px'>{sumko}</div>"
    ) if sumko else ""
    tags_row = f"<div style='margin-top:6px'>{tags}</div>" if tags else ""

    return (
        f"<a href='{href}' target='_blank' style='text-decoration:none;color:inherit;display:block'>"
        f"<div style='background:{CARD};border:1px solid {LINE};border-left:3px solid {accent};"
        f"border-radius:8px;padding:12px 14px;margin-bottom:8px;"
        f"box-shadow:0 1px 3px rgba(26,24,22,.04)'>"
        f"<div style='display:flex;gap:5px;align-items:center;margin-bottom:7px;flex-wrap:wrap'>"
        f"{tier_badge}"
        f"<span style='font-size:11px'>{flag}</span>"
        f"<span style='font-size:11px;color:{SUB}'>{media}</span>"
        f"{sc_badge}"
        f"<span style='font-size:10.5px;color:{SUB};margin-left:auto'>{pub}</span>"
        f"</div>"
        f"<div style='font-size:13.5px;font-weight:800;line-height:1.4;color:{INK}'>{title}</div>"
        f"{sumko_html}{tags_row}"
        f"</div></a>"
    )

# ══════════════════════════════════════════════════════
#  헤더
# ══════════════════════════════════════════════════════
_md(f"""
<div style='background:{NAVY};padding:12px 20px 10px;position:relative;overflow:hidden'>
  <div style='position:absolute;right:-8px;top:-8px;font-size:72px;font-weight:900;
    color:rgba(255,255,255,.04);line-height:1;pointer-events:none'>테마</div>
  <div style='display:flex;align-items:center;justify-content:space-between'>
    <h1 style='color:#fff;font-size:20px;font-weight:900;letter-spacing:-.02em;margin:0'>테마 뷰</h1>
    <span style='color:#7A8FA8;font-size:10.5px;font-weight:600'>ESG · 규제 · 리스크</span>
  </div>
</div>
""")

_md("<div style='height:10px'></div>")
h1, h2, h3 = st.columns(3)
with h1: st.page_link("dashboard_stocks.py", label="🏠 대시보드", use_container_width=True)
with h2: st.page_link("pages/2_뉴스.py",      label="📰 뉴스",    use_container_width=True)
with h3: st.page_link("pages/1_테마뷰.py",    label="🎯 테마",    use_container_width=True)

# ══════════════════════════════════════════════════════
#  필터
# ══════════════════════════════════════════════════════
fc1, fc2, fc3 = st.columns([2, 1, 1])
with fc1:
    days_opt = st.select_slider(
        "기간", options=[1, 3, 7, 14, 30], value=7,
        label_visibility="collapsed",
        format_func=lambda x: f"최근 {x}일"
    )
with fc2:
    min_score = st.selectbox(
        "최소 점수", [3, 4, 5], index=0,
        label_visibility="collapsed",
        format_func=lambda x: f"★{x} 이상"
    )
with fc3:
    limit = st.selectbox(
        "표시 건수", [20, 40, 80], index=1,
        label_visibility="collapsed",
        format_func=lambda x: f"최대 {x}건"
    )

since_date = (datetime.now(timezone.utc) - timedelta(days=days_opt)).strftime("%Y-%m-%d")

_md(f"<div style='height:1px;background:{LINE};margin:8px 0'></div>")

# ══════════════════════════════════════════════════════
#  테마 선택 탭
# ══════════════════════════════════════════════════════
if "tema_sel" not in st.session_state:
    st.session_state.tema_sel = "esg"

tema_keys   = list(THEMES.keys())
tema_labels = [THEMES[k]["label"] for k in tema_keys]

b1, b2, b3 = st.columns(3)
for col, key in zip([b1, b2, b3], tema_keys):
    with col:
        if st.button(
            THEMES[key]["label"], key=f"tema_{key}",
            use_container_width=True,
            type="primary" if st.session_state.tema_sel == key else "secondary",
        ):
            st.session_state.tema_sel = key
            st.rerun()

# ══════════════════════════════════════════════════════
#  선택된 테마 기사
# ══════════════════════════════════════════════════════
sel_key  = st.session_state.tema_sel
th       = THEMES[sel_key]
articles = load_theme(sel_key, since_date, min_score, limit)

_md(
    f"<div style='background:{th['bg']};border:1px solid {th['accent']}33;"
    f"border-radius:8px;padding:10px 14px;margin:10px 0 8px'>"
    f"<span style='font-size:13px;font-weight:900;color:{th['accent']}'>{th['label']}</span>"
    f"<span style='font-size:11px;font-weight:700;color:{SUB};margin-left:6px'>{len(articles)}건</span>"
    f"</div>"
)

if articles:
    cards_html = "".join(render_article_card(a, th["accent"]) for a in articles)
    _md(cards_html)
else:
    _md(
        f"<div style='text-align:center;padding:40px 0;color:{SUB};font-size:13px'>"
        f"해당 기사 없음</div>"
    )

# ══════════════════════════════════════════════════════
#  새로고침
# ══════════════════════════════════════════════════════
_md(f"<div style='height:1px;background:{LINE};margin:16px 0 12px'></div>")
if st.button("↻ 새로고침", use_container_width=False):
    st.cache_data.clear()
    st.rerun()
