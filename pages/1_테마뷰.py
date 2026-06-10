"""
GLB News — 테마별 기사 뷰
ESG/환경 · 규제 · 리스크 기사를 전국가 통합으로 표시
"""
import html as _html
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "news.db"

CC_FLAG = {
    "US":"🇺🇸","CN":"🇨🇳","JP":"🇯🇵","IN":"🇮🇳",
    "ID":"🇮🇩","VN":"🇻🇳","KH":"🇰🇭","MM":"🇲🇲","GLOBAL":"🌐","KR":"🇰🇷",
}

st.set_page_config(
    page_title="GLB News — 테마뷰",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.stApp,[data-testid="stAppViewContainer"],
[data-testid="stHeader"],[data-testid="stToolbar"] {
  background:#000 !important;
}
section.main > div { background:#000 !important; }
.block-container { max-width:1300px; padding:0 1.2rem 3rem; margin:auto; }
[data-testid="stSidebarNav"],[data-testid="stSidebar"],
header[data-testid="stHeader"],
[data-testid="collapsedControl"],
[data-testid="stMainMenu"],
footer { display:none !important; }

/* 네비 버튼 중앙 정렬 */
[data-testid="stPageLink"] a { justify-content:center !important; }
[data-testid="stPageLink"] p { text-align:center !important; width:100% !important; }

hr { border-color:#1c1c1e !important; }
div[data-testid="stHorizontalBlock"] { gap:6px !important; margin-bottom:0 !important; }
div[data-testid="stHorizontalBlock"] > div { padding:0 !important; }

/* 모바일: 컬럼 세로 쌓임 방지 + 자동 확대 방지 + 가로 스크롤 방지 */
@media (max-width: 640px) {
  [data-testid="stHorizontalBlock"] {
    flex-direction:row !important; flex-wrap:nowrap !important;
  }
  [data-testid="stHorizontalBlock"] > div {
    min-width:0 !important; flex:1 !important;
  }
  html, body, .stApp,
  [data-testid="stAppViewContainer"],
  [data-testid="stMain"] {
    overflow-x:hidden !important; width:100% !important; max-width:100vw !important;
  }
  .block-container {
    padding-left:0.8rem !important; padding-right:0.8rem !important;
  }
}
input, select, textarea, [data-baseweb="select"] * { font-size:16px !important; }
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

# ── 테마 정의 ─────────────────────────────────────────────────────────────────
THEMES = {
    "esg": {
        "label": "🌱 ESG / 환경",
        "color": "#30d158",
        "bg":    "#0d1a0d",
        "border":"#1a3a1a",
        "topic_kw": ["ESG","탄소","환경","기후","넷제로","그린","재생에너지","지속가능","green","carbon"],
        "title_kw":  ["ESG","탄소","기후","환경오염","그린워싱","carbon","climate","emission",
                      "sustainability","green bond","넷제로","탄소중립"],
        "reason_kw": ["esg","carbon","climate","green bond","net zero","emission"],
    },
    "regulation": {
        "label": "⚖️ 규제",
        "color": "#ff9f0a",
        "bg":    "#1a1400",
        "border":"#3a2e00",
        "topic_kw": ["규제","제재","감독","컴플라이언스","반독점","법안","규정","금융감독"],
        "title_kw":  ["규제","제재","감독","금지","법안","규정","단속","조사","처벌",
                      "regulation","sanction","compliance","antitrust","ban","crackdown"],
        "reason_kw": ["regulation","sanction","antitrust","compliance","deregulation"],
    },
    "risk": {
        "label": "⚠️ 리스크",
        "color": "#ff453a",
        "bg":    "#1a0d0d",
        "border":"#3a1a1a",
        "topic_kw": ["리스크","위기","급락","폭락","붕괴","불안","쇼크","위험","충격"],
        "title_kw":  ["리스크","위기","급락","폭락","붕괴","불안","충격","쇼크","위협",
                      "risk","crisis","crash","collapse","plunge","tumble","shock","warning","alert"],
        "reason_kw": ["recession","default","bankruptcy","sovereign debt"],
    },
}

# ── 쿼리 ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=120)
def load_theme(theme_key: str, since_date: str, min_score: int, limit: int) -> list[dict]:
    th = THEMES[theme_key]

    # topics JSON 배열 또는 title/summary_ko 에서 키워드 매칭
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


def render_articles(articles: list[dict], theme_color: str) -> str:
    if not articles:
        return "<div style='color:#48484a;font-size:0.83em;padding:24px 0;text-align:center'>해당 기사 없음</div>"

    rows = ""
    for a in articles:
        score  = a.get("ai_score") or 0
        tier   = a.get("tier", 1)
        cc     = a.get("cc", "")
        flag   = CC_FLAG.get(cc, "")
        pub    = fmt_time(a.get("published_at"))
        title  = _e(a.get("title") or "")
        sumko  = _e((a.get("summary_ko") or "")[:120])
        href   = _e(a.get("link") or "#")
        media  = _e((a.get("media_name") or "").upper())

        # 색상 점
        if tier == 0:       dot = "#30d158"
        elif score >= 5:    dot = "#ff453a"
        elif score >= 4:    dot = "#ff9f0a"
        elif score >= 3:    dot = "#ffd60a"
        else:               dot = "#3a3a3c"

        # 토픽 태그
        try:
            topics = json.loads(a.get("topics") or "[]")[:4]
        except Exception:
            topics = []
        tags_html = "".join(
            f"<span style='background:#2c2c2e;color:#ebebf5;font-size:0.62em;"
            f"padding:2px 7px;border-radius:99px;margin-right:4px'>{_e(t)}</span>"
            for t in topics
        )

        sumko_html = (
            f"<div style='font-size:0.78em;color:#8e8e93;line-height:1.5;margin-bottom:5px'>{sumko}</div>"
            if sumko else ""
        )
        rows += (
            f"<a href='{href}' target='_blank' style='text-decoration:none;display:block'>"
            f"<div style='display:flex;gap:10px;padding:11px 0;border-bottom:1px solid #1c1c1e'>"
            f"<div style='padding-top:4px;flex-shrink:0'>"
            f"<span style='display:block;width:6px;height:6px;border-radius:50%;background:{dot}'></span>"
            f"</div>"
            f"<div style='flex:1;min-width:0'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:2px'>"
            f"<span style='font-size:0.63em;color:#636366'>{flag} {media}</span>"
            f"<span style='font-size:0.63em;color:#48484a'>{pub}</span>"
            f"</div>"
            f"<div style='font-size:0.88em;font-weight:500;color:#fff;line-height:1.45;margin-bottom:4px'>{title}</div>"
            f"{sumko_html}"
            f"{'<div>' + tags_html + '</div>' if tags_html else ''}"
            f"</div></div></a>"
        )
    return f"<div>{rows}</div>"


# ── 헤더 ──────────────────────────────────────────────────────────────────────
h2, h3 = st.columns(2)
with h2:
    st.page_link("dashboard_stocks.py", label="📰 뉴스", use_container_width=True)
with h3:
    st.page_link("pages/1_테마뷰.py", label="🎯 테마", use_container_width=True)

# ── 필터 컨트롤 ───────────────────────────────────────────────────────────────
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

st.html("<div style='height:6px;border-top:1px solid #1c1c1e;margin-top:6px'></div>")

# ── 3개 컬럼 테마 ─────────────────────────────────────────────────────────────
col_esg, col_reg, col_risk = st.columns(3)

for col, theme_key in zip([col_esg, col_reg, col_risk], ["esg", "regulation", "risk"]):
    th = THEMES[theme_key]
    articles = load_theme(theme_key, since_date, min_score, limit)
    with col:
        st.html(
            f"<div style='background:{th['bg']};border:1px solid {th['border']};"
            f"border-radius:12px;padding:12px 16px;margin-bottom:12px'>"
            f"<span style='font-size:0.95em;font-weight:700;color:{th['color']}'>{th['label']}</span>"
            f"<span style='font-size:0.72em;color:#48484a;margin-left:8px'>{len(articles)}건</span>"
            f"</div>"
        )
        st.html(render_articles(articles, th["color"]))

# ── 새로고침 ──────────────────────────────────────────────────────────────────
st.html("<div style='height:8px'></div>")
st.divider()
if st.button("↻  새로고침", use_container_width=False):
    st.cache_data.clear()
    st.rerun()
