"""GLB News RSS — 대시보드"""
import html as _html
import json as _json
import score_engine
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

DB_PATH = Path(__file__).parent / "data" / "news.db"

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

# 타임바 — GLOBAL은 UTC
TIMEBAR = [
    ("🌐", "UTC",    "UTC"),
    ("🇺🇸", "뉴욕",  "America/New_York"),
    ("🇨🇳", "베이징","Asia/Shanghai"),
    ("🇯🇵", "도쿄",  "Asia/Tokyo"),
    ("🇮🇳", "뭄바이","Asia/Kolkata"),
    ("🇮🇩", "자카르타","Asia/Jakarta"),
    ("🇻🇳", "하노이","Asia/Ho_Chi_Minh"),
    ("🇰🇭", "프놈펜","Asia/Phnom_Penh"),
    ("🇲🇲", "양곤",  "Asia/Rangoon"),
]

LEVEL_COLOR = {
    "높음": "#9E2A22", "경보": "#9E2A22",
    "보통": "#1E4D8C", "주의": "#D4590A",
    "낮음": "#1F6142",
}
GAUGE_META = {
    "Economy": ("경제",   "#1E4D8C"),
    "Finance": ("금융",   "#1F6142"),
    "Digital": ("디지털", "#4E328A"),
    "Risk":    ("리스크", "#9E2A22"),
}
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
    page_title="GLB 대시보드",
    page_icon="🏠",
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

[data-testid="metric-container"] {{
  background:{CARD} !important; border-radius:10px !important;
  padding:10px 14px !important; border:1px solid {LINE} !important;
}}
[data-testid="stMetricLabel"] {{ color:{SUB} !important; font-size:0.68em !important; }}
[data-testid="stMetricValue"] {{ color:{INK} !important; font-size:1.1em !important; font-weight:800 !important; }}
[data-testid="stMetricDelta"] {{ display:none !important; }}

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
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA cache_size=-8000")
    return c

conn = get_conn()
def _e(t): return _html.escape(str(t or ""))
def _md(h): st.markdown(h, unsafe_allow_html=True)

# ── 쿼리 ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_temperature(cc, target_date):
    try:
        if cc == "GLOBAL":
            return score_engine.compute_global_temperature(conn, target_date=target_date)
        return score_engine.compute_country_temperature(conn, cc, target_date=target_date)
    except Exception:
        return {}

@st.cache_data(ttl=120)
def load_briefing_latest(cc):
    r = conn.execute("""
        SELECT * FROM country_briefings
        WHERE cc=? AND briefing_type='daily'
        ORDER BY briefing_date DESC LIMIT 1
    """, (cc,)).fetchone()
    return dict(r) if r else None

@st.cache_data(ttl=120)
def load_top_articles(sel_date, cc="GLOBAL", limit=3):
    where_cc = "AND m.primary_country_code = ?" if cc != "GLOBAL" else ""
    cc_param = [cc] if cc != "GLOBAL" else []
    rows = conn.execute(f"""
        SELECT a.title, a.link, a.summary_ko, a.ai_score,
               a.published_at, m.media_name, m.primary_country_code AS cc
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision='passed' AND a.ai_score >= 4
          AND a.duplicate_of IS NULL
          AND DATE(datetime(COALESCE(a.published_at, a.fetched_at), '+9 hours')) = ?
          {where_cc}
        ORDER BY a.ai_score DESC, a.published_at DESC NULLS LAST
        LIMIT ?
    """, (sel_date, *cc_param, limit)).fetchall()
    return [dict(r) for r in rows]

@st.cache_data(ttl=60)
def load_admin_stats(code):
    where = "AND m.primary_country_code = ?" if code != "GLOBAL" else ""
    params = [code] if code != "GLOBAL" else []
    r = conn.execute(f"""
        SELECT
          COUNT(*) FILTER (WHERE a.filter_decision='passed')              AS passed,
          COUNT(*) FILTER (WHERE a.ai_score IS NOT NULL)                  AS ai_done,
          COUNT(*) FILTER (WHERE a.ai_score=5)                            AS s5,
          COUNT(*) FILTER (WHERE a.ai_score IS NULL AND a.filter_decision='passed') AS ai_pending
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE 1=1 {where}
    """, params).fetchone()
    return dict(r) if r else {}

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
def render_masthead(kst_str):
    _md(f"""
<div style='background:{NAVY};padding:12px 20px 10px;position:relative;overflow:hidden'>
  <div style='position:absolute;right:-8px;top:-8px;font-size:72px;font-weight:900;
    color:rgba(255,255,255,.04);line-height:1;pointer-events:none;letter-spacing:-.04em'>GLB</div>
  <div style='display:flex;align-items:center;justify-content:space-between'>
    <span style='color:{GOLD};font-size:13px;font-weight:900;letter-spacing:.08em'>GLB NEWS</span>
    <span style='color:#8A9BB4;font-size:11px;font-weight:600'>{kst_str}</span>
  </div>
  <h1 style='color:#fff;font-size:20px;font-weight:900;letter-spacing:-.02em;
    line-height:1.2;margin:6px 0 2px'>글로벌 대시보드</h1>
  <div style='color:#7A8FA8;font-size:10.5px;font-weight:600'>9개 대상국 · AI 뉴스 모니터링</div>
</div>
""")

def render_pulse(scores):
    if not scores: return
    rows = ""
    for cat, (label, color) in GAUGE_META.items():
        s = scores.get(cat)
        if not s: continue
        lv_color = LEVEL_COLOR.get(s.level, SUB)
        rows += (
            f"<div style='display:flex;align-items:center;gap:10px'>"
            f"<span style='font-size:11px;font-weight:700;color:{INK};width:46px;flex-shrink:0'>{label}</span>"
            f"<div style='flex:1;background:{PAPER};border-radius:10px;height:7px;overflow:hidden'>"
            f"<div style='height:100%;width:{int(s.score)}%;background:{color};border-radius:10px'></div></div>"
            f"<span style='font-size:10.5px;font-weight:800;color:{lv_color};"
            f"width:28px;text-align:right;flex-shrink:0'>{s.level}</span>"
            f"</div>"
        )
    _md(f"""
<div style='background:{CARD};padding:14px 20px;border-bottom:1px solid {LINE}'>
  <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>
    <span style='font-size:11px;font-weight:900;letter-spacing:.06em;color:{INK}'>GLOBAL PULSE</span>
    <span style='font-size:10px;font-weight:700;color:{SUB};background:{PAPER};
      border-radius:3px;padding:2px 7px'>최근 3일 평균</span>
  </div>
  <div style='display:flex;flex-direction:column;gap:7px'>{rows}</div>
</div>
""")

def render_key_stat(brief):
    if not brief: return
    try:
        ks = _json.loads(brief.get("key_stat") or "null")
    except Exception:
        ks = None
    if not ks or not isinstance(ks, dict) or not ks.get("value"): return
    val   = _e(str(ks.get("value", "")))
    label = _e(str(ks.get("label", "")))
    bdate = _e(brief.get("briefing_date",""))
    _md(f"""
<div style='margin:14px 20px 0;background:{CARD};border-radius:10px;padding:16px;
  border:1px solid {LINE};position:relative;overflow:hidden;
  box-shadow:0 2px 8px rgba(26,24,22,.06)'>
  <div style='position:absolute;left:0;top:0;bottom:0;width:4px;background:{GOLD}'></div>
  <div style='padding-left:8px'>
    <div style='font-size:10px;font-weight:900;letter-spacing:.1em;color:{SUB};margin-bottom:8px'>이 날의 숫자 · {bdate}</div>
    <div style='font-size:36px;font-weight:900;color:{NAVY};letter-spacing:-.03em;line-height:1;margin-bottom:6px'>{val}</div>
    <div style='font-size:12.5px;font-weight:700;color:{INK}'>{label}</div>
  </div>
</div>
""")

def render_top3(articles):
    if not articles: return
    a0     = articles[0]
    title0 = _e(a0.get("title") or "")
    sumko0 = _e((a0.get("summary_ko") or "")[:100])
    href0  = _e(a0.get("link") or "#")
    flag0  = CC_FLAG.get(a0.get("cc",""), "")
    cname0 = CC_NAME.get(a0.get("cc",""), a0.get("cc",""))
    media0 = _e((a0.get("media_name") or "").upper())
    sc0    = a0.get("ai_score") or 0
    sc_c0  = SCORE_COLOR.get(sc0, SUB)
    sc_l0  = SCORE_LABEL.get(sc0, "")

    lead_html = (
        f"<p style='font-size:12.5px;color:{SUB};line-height:1.5;margin:0'>{sumko0}</p>"
    ) if sumko0 else ""
    sc_badge = (
        f"<span style='font-size:11px;font-weight:700;border-radius:3px;padding:2px 8px;"
        f"background:{sc_c0}22;color:{sc_c0}'>{sc_l0}</span>"
    ) if sc_l0 else ""

    first_html = (
        f"<a href='{href0}' target='_blank' style='text-decoration:none;color:inherit;display:block'>"
        f"<div style='background:{CARD};border:1px solid {LINE};border-left:4px solid {NAVY};"
        f"border-radius:8px;padding:15px 15px 13px;margin-bottom:8px;position:relative;overflow:hidden'>"
        f"<div style='position:absolute;bottom:-10px;right:8px;font-size:80px;font-weight:900;"
        f"color:rgba(0,0,0,.04);line-height:1;pointer-events:none'>1</div>"
        f"<div style='display:flex;align-items:center;gap:7px;margin-bottom:9px;flex-wrap:wrap'>"
        f"<span style='font-size:12px'>{flag0}</span>"
        f"<span style='font-size:11px;font-weight:700;color:{SUB}'>{cname0} · {media0}</span>"
        f"{sc_badge}</div>"
        f"<div style='font-size:16px;font-weight:900;line-height:1.3;color:{INK};"
        f"margin-bottom:7px;letter-spacing:-.02em'>{title0}</div>"
        f"{lead_html}</div></a>"
    )

    side_html = ""
    if len(articles) > 1:
        cards = ""
        for i, a in enumerate(articles[1:3], 2):
            t_n    = _e(a.get("title") or "")
            h_n    = _e(a.get("link") or "#")
            fl_n   = CC_FLAG.get(a.get("cc",""), "")
            cn_n   = CC_NAME.get(a.get("cc",""), a.get("cc",""))
            sumko_n = _e((a.get("summary_ko") or "")[:55])
            extra  = f"border-right:1px solid {LINE};" if i == 2 else ""
            sumko_html = (
                f"<div style='font-size:11px;color:{SUB};line-height:1.5;margin-top:5px'>{sumko_n}…</div>"
            ) if sumko_n else ""
            cards += (
                f"<a href='{h_n}' target='_blank' style='text-decoration:none;color:inherit;"
                f"flex:1;min-width:0;display:block'>"
                f"<div style='padding:13px;position:relative;overflow:hidden;{extra}'>"
                f"<div style='position:absolute;bottom:-4px;right:6px;font-size:44px;font-weight:900;"
                f"color:rgba(0,0,0,.04);line-height:1;pointer-events:none'>{i}</div>"
                f"<div style='display:inline-block;font-size:10px;font-weight:900;background:{NAVY};"
                f"color:{GOLD};border-radius:3px;padding:2px 6px;margin-bottom:7px'>0{i}</div>"
                f"<div style='display:flex;align-items:center;gap:5px;margin-bottom:6px'>"
                f"<span style='font-size:11px'>{fl_n}</span>"
                f"<span style='font-size:11px;font-weight:700;color:{SUB}'>{cn_n}</span></div>"
                f"<div style='font-size:13px;font-weight:800;line-height:1.4;color:{INK}'>{t_n}</div>"
                f"{sumko_html}"
                f"</div></a>"
            )
        side_html = (
            f"<div style='display:flex;background:{CARD};border:1px solid {LINE};"
            f"border-radius:8px;overflow:hidden'>{cards}</div>"
        )

    _md(f"""
<div style='margin:14px 20px 0'>
  <div style='display:flex;align-items:center;gap:10px;margin-bottom:10px'>
    <div style='flex:1;height:1.5px;background:{INK}'></div>
    <span style='font-size:11px;font-weight:900;letter-spacing:.1em;color:{INK};white-space:nowrap'>TODAY'S TOP</span>
    <div style='flex:1;height:1.5px;background:{INK}'></div>
  </div>
  {first_html}{side_html}
</div>
""")

def render_timebar():
    now_utc = datetime.now(timezone.utc)
    chips = ""
    for flag, name, tz in TIMEBAR:
        try:
            local = now_utc.astimezone(ZoneInfo(tz))
            t = local.strftime("%H:%M")
        except Exception:
            t = "--:--"
        chips += (
            f"<div style='flex-shrink:0;background:{CARD};border:1px solid {LINE};"
            f"border-radius:8px;padding:9px 12px;min-width:64px;text-align:center'>"
            f"<span style='font-size:16px;display:block;margin-bottom:4px'>{flag}</span>"
            f"<span style='font-size:10px;font-weight:700;color:{SUB};display:block;margin-bottom:3px'>{name}</span>"
            f"<span style='font-size:13px;font-weight:900;color:{INK};display:block'>{t}</span>"
            f"</div>"
        )
    _md(f"""
<div style='margin:14px 0 0;padding:0 0 0 20px'>
  <div style='font-size:10px;font-weight:900;letter-spacing:.08em;color:{SUB};margin-bottom:8px'>거점 현지시각</div>
  <div style='display:flex;gap:8px;overflow-x:auto;padding-right:20px;padding-bottom:14px;scrollbar-width:none'>
    {chips}
  </div>
</div>
""")

# ── 세션 ──────────────────────────────────────────────────────────────────────
if "sel" not in st.session_state: st.session_state.sel = "GLOBAL"
sel = st.session_state.sel

latest_date = _latest_date()

# ══════════════════════════════════════════════════════
#  1. MASTHEAD
# ══════════════════════════════════════════════════════
try:
    kst_now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Seoul"))
    day_ko  = ["월","화","수","목","금","토","일"][kst_now.weekday()]
    kst_str = kst_now.strftime(f"%Y.%m.%d {day_ko}")
except Exception:
    kst_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

render_masthead(kst_str)
_md("<div style='height:10px'></div>")

# ══════════════════════════════════════════════════════
#  2. NAV (3 탭)
# ══════════════════════════════════════════════════════
n1, n2, n3 = st.columns(3)
with n1: st.page_link("dashboard_stocks.py", label="🏠 대시보드", use_container_width=True)
with n2: st.page_link("pages/2_뉴스.py",      label="📰 뉴스",    use_container_width=True)
with n3: st.page_link("pages/1_테마뷰.py",    label="🎯 테마",    use_container_width=True)

# ══════════════════════════════════════════════════════
#  3. 국가 선택
# ══════════════════════════════════════════════════════
country_labels = [f"{f} {n}" for f, _, n in COUNTRIES]
country_codes  = [c for _, c, _ in COUNTRIES]
cur_idx = country_codes.index(sel) if sel in country_codes else 0
chosen  = st.selectbox("국가", country_labels, index=cur_idx, label_visibility="collapsed")
chosen_code = country_codes[country_labels.index(chosen)]
if chosen_code != sel:
    st.session_state.sel = chosen_code
    st.cache_data.clear(); st.rerun()

# ══════════════════════════════════════════════════════
#  4. GLOBAL PULSE
# ══════════════════════════════════════════════════════
render_pulse(load_temperature(chosen_code, latest_date))

# ══════════════════════════════════════════════════════
#  5. KEY STAT
# ══════════════════════════════════════════════════════
_brief = load_briefing_latest(chosen_code) or load_briefing_latest("GLOBAL")
render_key_stat(_brief)

# ══════════════════════════════════════════════════════
#  6. TODAY'S TOP 3
# ══════════════════════════════════════════════════════
render_top3(load_top_articles(latest_date, cc=chosen_code))

# ══════════════════════════════════════════════════════
#  7. TIMEBAR
# ══════════════════════════════════════════════════════
render_timebar()
_md(f"<div style='height:1px;background:{LINE};margin:4px 20px 12px'></div>")

# ══════════════════════════════════════════════════════
#  8. 수집 현황
# ══════════════════════════════════════════════════════
with st.expander("📊 수집 현황"):
    stats = load_admin_stats(chosen_code)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("필터 통과", f"{stats.get('passed',0):,}")
    c2.metric("AI 완료",   f"{stats.get('ai_done',0):,}")
    c3.metric("★5 핵심",   f"{stats.get('s5',0):,}")
    c4.metric("AI 대기",   f"{stats.get('ai_pending',0):,}")
    if st.button("↻ 새로고침", key="refresh_btn"):
        st.cache_data.clear(); st.rerun()

_md(
    f"<div style='text-align:center;padding:20px;border-top:1px solid {LINE};margin-top:8px'>"
    f"<span style='font-size:10px;font-weight:800;letter-spacing:.2em;color:{NAVY};opacity:.3'>"
    f"GLB NEWS RSS PROTOTYPE</span></div>"
)
