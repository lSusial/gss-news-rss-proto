"""
glb-news-rss 대시보드 — 국가별 주요 뉴스
실행: streamlit run dashboard.py
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

DB_PATH = Path(__file__).parent / "data" / "news.db"

COUNTRIES = [
    ("🌐", "GLOBAL", "글로벌"),
    ("🇺🇸", "US",     "미국"),
    ("🇨🇳", "CN",     "중국"),
    ("🇯🇵", "JP",     "일본"),
    ("🇮🇳", "IN",     "인도"),
    ("🇮🇩", "ID",     "인도네시아"),
    ("🇻🇳", "VN",     "베트남"),
    ("🇰🇭", "KH",     "캄보디아"),
    ("🇲🇲", "MM",     "미얀마"),
]

RANK_COLORS = ["#ff4b4b", "#ff8c00", "#ffd700", "#4fc3f7", "#4fc3f7",
               "#81c784", "#81c784", "#81c784", "#81c784", "#81c784"]

st.set_page_config(
    page_title="GLB News — 국가별 주요 뉴스",
    page_icon="📰",
    layout="wide",
)

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

conn = get_conn()

# ---------------------------------------------------------------------------
# 쿼리 헬퍼
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60)
def load_overview():
    return dict(conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM articles_raw WHERE filter_decision='passed') AS passed,
            (SELECT COUNT(*) FROM articles_raw)                                AS total,
            (SELECT COUNT(*) FROM media_source_feeds WHERE is_active=1)        AS feeds,
            (SELECT MAX(finished_at) FROM fetch_runs)                          AS last_fetch,
            (SELECT COUNT(*) FROM articles_raw WHERE ai_score IS NOT NULL)     AS ai_analyzed
    """).fetchone())

@st.cache_data(ttl=60)
def load_country_stat(code: str) -> dict:
    return dict(conn.execute("""
        SELECT
            COUNT(*)                                                            AS total,
            SUM(CASE WHEN filter_decision='passed'   THEN 1 ELSE 0 END)        AS passed,
            SUM(CASE WHEN ai_score IS NOT NULL       THEN 1 ELSE 0 END)        AS ai_done,
            COUNT(DISTINCT source_id)                                           AS sources
        FROM articles_raw
        WHERE source_id IN (
            SELECT source_id FROM media_sources WHERE primary_country_code = ?
        )
    """, (code,)).fetchone())

@st.cache_data(ttl=60)
def load_ai_top(code: str, limit: int = 10) -> list[dict]:
    """AI 중요도 점수 기준 TOP N 기사 (ai_score 있는 것만)."""
    rows = conn.execute("""
        SELECT m.media_name, a.title, a.link, a.published_at,
               a.ai_score, a.summary_ko
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE m.primary_country_code = ?
          AND a.filter_decision = 'passed'
          AND a.ai_score IS NOT NULL
        ORDER BY a.ai_score DESC, a.published_at DESC NULLS LAST
        LIMIT ?
    """, (code, limit)).fetchall()
    return [dict(r) for r in rows]

@st.cache_data(ttl=60)
def load_articles(code: str, limit: int = 30) -> list[dict]:
    rows = conn.execute("""
        SELECT m.media_name, a.title, a.link, a.published_at, a.filter_reason, a.ai_score
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE m.primary_country_code = ?
          AND a.filter_decision = 'passed'
        ORDER BY a.published_at DESC NULLS LAST, a.fetched_at DESC
        LIMIT ?
    """, (code, limit)).fetchall()
    return [dict(r) for r in rows]

@st.cache_data(ttl=60)
def load_sources_for_country(code: str) -> list[dict]:
    rows = conn.execute("""
        SELECT m.media_name,
               COUNT(*)                                                      AS total,
               SUM(CASE WHEN a.filter_decision='passed' THEN 1 ELSE 0 END)  AS passed
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE m.primary_country_code = ?
        GROUP BY m.media_name
        ORDER BY passed DESC
    """, (code,)).fetchall()
    return [dict(r) for r in rows]

def fmt_date(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        if diff.total_seconds() < 3600:
            return f"{int(diff.total_seconds()//60)}분 전"
        if diff.total_seconds() < 86400:
            return f"{int(diff.total_seconds()//3600)}시간 전"
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return iso[:16]

def score_badge(score: int | None) -> str:
    if score is None:
        return ""
    colors = {5: "#ff4b4b", 4: "#ff8c00", 3: "#ffd700", 2: "#888", 1: "#555"}
    labels = {5: "⭐⭐⭐ 핵심", 4: "⭐⭐ 중요", 3: "⭐ 관련", 2: "보통", 1: "낮음"}
    c = colors.get(score, "#888")
    l = labels.get(score, str(score))
    return f"<span style='background:{c};color:#fff;font-size:0.72em;padding:2px 7px;border-radius:10px;font-weight:700'>{l}</span>"

# ---------------------------------------------------------------------------
# 사이드바
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 📰 GLB News RSS")
    st.caption("글로벌 금융·경제·ESG 뉴스")
    st.divider()

    ov = load_overview()
    st.metric("관련 기사", f"{ov['passed']:,}건")
    st.metric("활성 피드", f"{ov['feeds']}개")
    if ov.get("ai_analyzed"):
        st.metric("AI 분석 완료", f"{ov['ai_analyzed']:,}건")

    if ov["last_fetch"]:
        try:
            dt = datetime.fromisoformat(ov["last_fetch"])
            st.caption(f"마지막 수집: {dt.strftime('%m/%d %H:%M')}")
        except Exception:
            pass

    st.divider()
    if st.button("🔄 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("수집 기준: 금융·경제·ESG 키워드")
    st.caption("AI 분석: `python main.py ai-rank`")

# ---------------------------------------------------------------------------
# 메인: 국가별 탭
# ---------------------------------------------------------------------------
st.title("🌏 국가별 주요 뉴스")

tab_labels = [f"{flag} {name}" for flag, _, name in COUNTRIES]
tabs = st.tabs(tab_labels)

for tab, (flag, code, name) in zip(tabs, COUNTRIES):
    with tab:
        stat   = load_country_stat(code)
        passed = stat["passed"] or 0
        total  = stat["total"]  or 0
        ai_done = stat["ai_done"] or 0
        rate   = passed / total * 100 if total else 0

        # 탭 상단 메트릭
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("관련 기사", f"{passed:,}건")
        m2.metric("수집 기사", f"{total:,}건")
        m3.metric("필터 통과율", f"{rate:.0f}%")
        m4.metric("AI 분석", f"{ai_done:,}건")

        st.divider()

        # ── 🤖 AI 선별 주요 뉴스 TOP 10 ─────────────────────────────────────
        ai_articles = load_ai_top(code, limit=10)

        if ai_articles:
            st.markdown("### 🤖 AI 선별 주요 뉴스 TOP 10")
            st.caption("Claude AI가 금융·경제 중요도를 평가하고 한글로 요약한 기사입니다.")

            for i, art in enumerate(ai_articles, 1):
                pub    = fmt_date(art["published_at"])
                title  = art["title"] or "(제목 없음)"
                link   = art["link"]  or "#"
                media  = art["media_name"] or ""
                score  = art["ai_score"]
                sumko  = art["summary_ko"] or ""
                color  = RANK_COLORS[i - 1]
                badge  = score_badge(score)

                st.markdown(
                    f"""
                    <div style="
                        padding:14px 18px; margin-bottom:10px;
                        background:#16213e; border-radius:10px;
                        border-left:5px solid {color};
                    ">
                        <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
                            <span style="font-size:1.2em;font-weight:900;color:{color};min-width:24px">{i}</span>
                            {badge}
                            <span style="color:#888;font-size:0.78em">📌 {media} &nbsp;·&nbsp; 🕐 {pub}</span>
                        </div>
                        <a href="{link}" target="_blank" style="
                            font-size:1.03em;font-weight:600;color:#dce8ff;
                            text-decoration:none;line-height:1.45;display:block;margin-bottom:6px
                        ">{title}</a>
                        {"<div style='color:#b0c4de;font-size:0.88em;line-height:1.5;padding:8px 10px;background:#0d1b2a;border-radius:6px'>" + sumko + "</div>" if sumko else ""}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        else:
            st.info(
                "🤖 AI 분석 데이터가 없습니다.  \n"
                f"`python main.py ai-rank --country {code}` 를 실행하면 이 탭에 TOP 10이 표시됩니다.",
                icon="💡",
            )

        st.divider()

        # ── 전체 기사 목록 + 매체별 현황 ─────────────────────────────────────
        col_news, col_src = st.columns([3, 1])

        with col_news:
            st.markdown("#### 📋 전체 기사 목록")
            all_articles = load_articles(code, limit=30)
            if not all_articles:
                st.info("수집된 기사가 없습니다.")
            else:
                for art in all_articles:
                    pub   = fmt_date(art["published_at"])
                    title = art["title"] or ""
                    link  = art["link"]  or "#"
                    media = art["media_name"] or ""
                    badge = score_badge(art.get("ai_score"))

                    st.markdown(
                        f"**[{title}]({link})**  \n"
                        f"<span style='color:#888;font-size:0.82em'>"
                        f"📌 {media} &nbsp;·&nbsp; 🕐 {pub}"
                        f"</span>"
                        + (f" &nbsp;{badge}" if badge else ""),
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        "<hr style='margin:6px 0;border-color:#2a2a2a'>",
                        unsafe_allow_html=True,
                    )

        with col_src:
            st.markdown("#### 매체별 현황")
            sources = load_sources_for_country(code)
            for s in sources:
                p = s["passed"]
                t = s["total"]
                r = p / t * 100 if t else 0
                st.markdown(
                    f"**{s['media_name']}**  \n"
                    f"<span style='color:#888;font-size:0.8em'>"
                    f"{p:,}건 / {t:,}건 ({r:.0f}%)</span>",
                    unsafe_allow_html=True,
                )
                st.progress(min(r / 100, 1.0))
