"""
glb-news-rss 2단계 키워드 필터 (v2 — 점수제 + 네거티브 필터)

점수 체계:
  +3  금융·경제·ESG 키워드 1개 히트
  +1  9개 대상국 키워드 1개 히트 (국가당 최초 1회)
  -4  스포츠·연예·순수과학 제외 키워드 1개 히트
  ──────────────────────────────────────────
  ≥ 3  passed   (금융 키워드 1개만 있어도 통과)
  < 3  rejected  (국가 키워드만 1-2개는 탈락,
                  스포츠 + 국가는 마이너스로 탈락)

filter_stage  : 0=미처리 | 2=키워드필터완료 | 3=LLM(미구현)
filter_decision: 'pending' | 'passed' | 'rejected'
filter_reason  : 통과·거부 주요 근거 문자열
"""
from __future__ import annotations

import html as _html
import logging
import re
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger("keyword_filter")

# ---------------------------------------------------------------------------
# 점수 상수
# ---------------------------------------------------------------------------
FINANCE_SCORE    =  3   # 금융·ESG 키워드 히트당
COUNTRY_SCORE    =  1   # 국가 키워드 히트당 (국가당 1회)
EXCLUSION_SCORE  = -4   # 스포츠·연예 제외 키워드 히트당
PASS_THRESHOLD   =  2   # ≥2 이면 passed
                         # 금융 키워드 1개(+3) 단독 통과
                         # 서로 다른 두 나라 언급(+1+1) 통과
                         # 통화·지수 등 금융 국가 KW(+3) 단독 통과

# ---------------------------------------------------------------------------
# 글로벌 카테고리 코드 (레거시 — 리포트용)
# ---------------------------------------------------------------------------
GLOBAL_CATEGORIES = ("GLOBAL_GENERAL", "GLOBAL_ECONOMY")

# ---------------------------------------------------------------------------
# ① 금융·경제·ESG 키워드 (영문, 소문자 부분 매칭)
# ---------------------------------------------------------------------------
FINANCE_KEYWORDS: list[str] = [
    # 거시경제
    "gdp", "inflation", "deflation", "recession", "stagflation",
    "monetary policy", "fiscal policy", "interest rate", "rate hike", "rate cut",
    "central bank", "quantitative easing", "quantitative tightening",
    "stimulus", "austerity", "budget deficit", "trade deficit", "surplus",
    "public debt", "sovereign debt", "credit rating",
    "economic growth", "economic output", "economic reform",
    # 재정·예산
    "budget", "fiscal year", "annual report", "fiscal stimulus",
    "tax cut", "tax hike", "tax reform", "tax rate", "income tax", "corporate tax",
    "debt", "national debt",
    # 금융·은행
    "bank", "banking", "central bank",
    "loan", "lending", "microfinance", "remittance",
    "financial stability", "financial sector",
    "stock market", "stock exchange", "equity market", "bond market", "capital market",
    "yield curve", "treasury", "etf", "hedge fund", "private equity",
    "ipo", "listing", "delisting", "dividend", "earnings", "revenue", "profit",
    "forex", "exchange rate", "currency", "currency depreciation", "currency appreciation",
    "share price", "shares", "equity shares",
    # 기업·투자·거래
    "deal", "trade deal", "merger", "acquisition", "m&a", "takeover", "joint venture",
    "investment", "venture capital", "fdi", "foreign direct investment",
    "startup", "unicorn",
    "bankruptcy", "default", "restructuring", "privatization", "nationalization",
    "subsidy", "tariff", "sanction", "trade war", "trade agreement", "trade",
    "supply chain", "export", "import", "current account",
    # 국제기구·고위직
    "imf", "world bank", "adb", "asian development bank",
    "wto", "g20", "g7", "oecd",
    "finance minister", "minister of finance", "treasury minister",
    "central bank governor", "finance secretary",
    # 통화 (국가 귀속 통화를 금융 신호로 처리)
    "yen", "jpy", "yuan", "renminbi", "rmb", "cny",
    "rupee", "inr", "rupiah", "idr",
    "dong", "vnd", "riel", "khr", "kyat", "mmk",
    "us dollar", "usd",
    # 주요 주가지수·거래소 (3자 약어 제외 — 부분매칭 오탐 방지)
    "nikkei", "topix", "sensex", "nifty", "kospi", "kosdaq", "ihsg",
    "nasdaq", "s&p 500", "dow jones",
    # 중앙은행·금융감독 (약어)
    "the fed", "federal reserve", "pboc", "boj", "rbi", "sebi",
    "bank of japan", "bank indonesia", "ojk",
    "people's bank of china", "reserve bank of india",
    # 에너지·원자재·EV
    "oil price", "oil market", "crude oil", "natural gas", "gas price",
    "commodit",
    "electric vehicle", "new energy vehicle", "energy transition", "energy storage",
    "solar power", "wind power", "clean energy",
    "nev sales", "ev market", "ev sales",
    # 규제·정책·핀테크
    "regulation", "deregulation", "compliance", "antitrust",
    "fintech", "digital asset", "cryptocurren", "bitcoin", "blockchain",
    "cbdc", "digital currency",
    # ESG·기후
    "esg", "carbon emission", "carbon tax", "carbon credit",
    "climate change", "net zero", "renewable energy", "green energy",
    "sustainability", "sustainable finance", "green bond", "social bond",
    "governance", "corporate governance", "disclosure", "csr",
]

# ---------------------------------------------------------------------------
# ② 9개국 지명·기관·통화·인명 키워드
# ---------------------------------------------------------------------------
COUNTRY_KEYWORDS: dict[str, list[str]] = {
    "KR": [
        "korea", "korean", "south korea", "seoul", "busan", "incheon",
        "korean won", " won ", "krw", "kospi", "kosdaq",
        "bank of korea", "bok", "financial services commission",
        "yoon suk", "lee jae",
    ],
    "US": [
        "united states", " u.s.", "america", "american",
        "washington d.c.", "new york", "wall street",
        "us dollar", "usd", "federal reserve", "the fed", "jerome powell",
        "nasdaq", "s&p 500", "dow jones", "treasury secretary",
        "trump", "donald trump", "white house",
    ],
    "CN": [
        "china", "chinese", "beijing", "shanghai", "guangzhou", "shenzhen",
        "yuan", "renminbi", "rmb", "cny",
        "people's bank of china", "pboc",
        "xi jinping", "li qiang", "cpc", "politburo",
    ],
    "JP": [
        "japan", "japanese", "tokyo", "osaka", "kyoto",
        "japanese yen", "yen", "jpy",
        "bank of japan", "boj", "kazuo ueda",
        "nikkei", "topix", "kishida", "ishiba",
    ],
    "ID": [
        "indonesia", "indonesian", "jakarta", "surabaya", "bandung",
        "indonesian rupiah", "rupiah", "idr",
        "bank indonesia", "ojk", "prabowo", "jokowi",
        "kb bukopin",
    ],
    "VN": [
        "vietnam", "vietnamese", "hanoi", "ho chi minh city", "hcmc",
        "vietnamese dong", "vnd",
        "state bank of vietnam", "sbv",
        "to lam", "nguyen",
    ],
    "KH": [
        "cambodia", "cambodian", "phnom penh", "siem reap",
        "cambodian riel", "riel", "khr",
        "hun manet", "hun sen", "national bank of cambodia",
        "prasac",
    ],
    "MM": [
        "myanmar", "burmese", "burma", "yangon", "naypyidaw", "mandalay",
        "myanmar kyat", "kyat", "mmk",
        "central bank of myanmar", "tatmadaw", "sac", "min aung hlaing",
        "aung san suu kyi", "nld",
    ],
    "IN": [
        "india", "indian", "new delhi", "mumbai", "bangalore", "bengaluru",
        "indian rupee", "rupee", "inr",
        "reserve bank of india", "rbi", "narendra modi", "sebi",
        "sensex", "nifty", "bse", "nse",
    ],
}

# ---------------------------------------------------------------------------
# ③ 한국어 금융·경제 키워드
# ---------------------------------------------------------------------------
KOREAN_FINANCE_KEYWORDS: list[str] = [
    # 거시경제
    "금리", "기준금리", "금리인상", "금리인하", "금리동결",
    "환율", "원달러", "달러",
    "인플레이션", "물가", "소비자물가", "생산자물가",
    "경제성장", "성장률", "경기", "경기침체", "경기둔화", "불황",
    "gdp", "국내총생산",
    "무역", "교역", "수출", "수입", "무역수지", "경상수지",
    "재정", "예산", "재정적자", "재정흑자", "국가채무", "국채",
    "적자", "흑자",
    # 금융·은행
    "금융", "은행", "금융시장", "자본시장", "금융감독",
    "중앙은행", "기준금리",
    "대출", "여신", "수신", "부실대출",
    "주식", "증시", "주가", "코스피", "나스닥",
    "채권", "국채", "회사채",
    "외환", "외화", "달러화", "위안화", "엔화",
    # 기업·투자
    "투자", "외국인투자", "직접투자", "fdi",
    "인수합병", "m&a", "기업인수",
    "상장", "ipo", "기업공개",
    "파산", "부도", "채무불이행", "구조조정",
    "관세", "제재", "무역협정", "fta",
    "공급망", "반도체", "배터리", "전기차",
    # 국제기구·정책
    "imf", "세계은행", "아시아개발은행", "adb",
    "g20", "oecd",
    "통화정책", "재정정책", "경제정책", "규제",
    # ESG·에너지
    "esg", "탄소", "탄소중립", "넷제로",
    "유가", "원유", "에너지",
    "재생에너지", "태양광", "풍력",
]

# 한국어 기사에서 대상 국가를 인식하는 키워드
KOREAN_COUNTRY_KEYWORDS: dict[str, list[str]] = {
    "US": ["미국", "미 연준", "연준", "연방준비제도", "트럼프", "바이든", "재무부"],
    "CN": ["중국", "위안화", "인민은행", "중국인민은행", "중국 경제"],
    "JP": ["일본", "엔화", "일본은행", "엔 ", "BOJ"],
    "IN": ["인도", "루피", "인도 경제", "인도중앙은행"],
    "ID": ["인도네시아", "루피아", "인도네시아 중앙은행"],
    "VN": ["베트남", "동화", "베트남 경제", "베트남 중앙은행"],
    "KH": ["캄보디아", "캄보디아 경제"],
    "MM": ["미얀마", "미얀마 경제"],
    "KR": ["한국", "한국은행", "기재부", "금융위", "코스피", "원화"],
}

# ---------------------------------------------------------------------------
# ④ 인도네시아어 금융·경제 키워드
# ---------------------------------------------------------------------------
INDONESIAN_FINANCE_KEYWORDS: list[str] = [
    "ekonomi", "keuangan", "moneter", "fiskal", "inflasi", "deflasi",
    "pertumbuhan", "resesi", "gdp", "pdb", "anggaran", "defisit", "surplus",
    "saham", "bursa", "ihsg", "obligasi", "investasi", "modal", "pasar modal",
    "dividen", "ipo", "emiten", "reksadana", "aset",
    "bank", "perbankan", "rupiah", "idr", "suku bunga", "kredit", "pinjaman",
    "bank indonesia", "ojk", "lkm",
    "bisnis", "perusahaan", "merger", "akuisisi", "ekspor", "impor",
    "perdagangan", "tarif", "sanksi", "subsidi", "privatisasi",
    "esg", "karbon", "energi", "keberlanjutan", "lingkungan", "tata kelola",
]

# ---------------------------------------------------------------------------
# ④ 제외 키워드 — 스포츠·연예·순수과학 (고신뢰 비금융 신호)
#    하나라도 히트 → -4점 (금융 키워드 없으면 탈락)
# ---------------------------------------------------------------------------
EXCLUSION_KEYWORDS: list[str] = [
    # 스포츠 경기·선수·결과
    "gold medal", "silver medal", "bronze medal",
    "powerlifter", "weightlift",
    "sea games", "asian games",
    "world cup qualifier",
    "hat trick", "penalty shootout", "clean sheet",
    "sumo",
    "tennis match", "cricket match", "badminton match",
    "chess tournament", "chess championship",
    # 연예·미디어
    "box office", "music festival", "concert tour", "music album",
    "grammy award", "academy award", "film festival",
    "chart-topping", "chart topping",
    # 부고·인물
    "dies at", "passed away", "in memoriam", "obituary",
    "saxophonist", "violinist", "pianist", "conductor",
    # 순수 자연과학
    "deep-sea", "new species", "newly discovered species",
    "paleontolog", "fossil discover",
    "marine biolog",
]

# ---------------------------------------------------------------------------
# DB 마이그레이션
# ---------------------------------------------------------------------------
def ensure_filter_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(articles_raw)")}
    migrations = [
        ("filter_stage",
         "ALTER TABLE articles_raw ADD COLUMN filter_stage    INTEGER NOT NULL DEFAULT 0"),
        ("filter_decision",
         "ALTER TABLE articles_raw ADD COLUMN filter_decision TEXT    NOT NULL DEFAULT 'pending'"),
        ("filter_reason",
         "ALTER TABLE articles_raw ADD COLUMN filter_reason   TEXT"),
    ]
    added = []
    for col, sql in migrations:
        if col not in existing:
            conn.execute(sql)
            added.append(col)
    if added:
        conn.commit()
        log.info("마이그레이션 완료: articles_raw 컬럼 추가 %s", added)

# ---------------------------------------------------------------------------
# 텍스트 정제 헬퍼
# ---------------------------------------------------------------------------
def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def _clean_text(raw: str) -> str:
    """
    Google News RSS 텍스트 정제:
    ① HTML 태그 제거
    ② HTML 엔티티 디코딩 (&nbsp; 등)
    ③ 제목/요약 끝 '- 출처명' 또는 '  출처명' 패턴 제거
    """
    text = _html.unescape(raw or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\xa0+", " ", text)
    # " - Source Name" 패턴 (양쪽 공백 필수 → "US-China" 같은 복합어 보호)
    text = re.sub(r"\s+-\s+[A-Za-z][\w\s]{1,60}$", "", text)
    # "  Source Name" 패턴 (2칸 이상 공백 + 대문자 시작)
    text = re.sub(r"\s{2,}[A-Z][A-Za-z][\w\s]{1,60}$", "", text)
    return text.strip()


def _first_match(haystack: str, keywords: list[str]) -> str | None:
    for kw in keywords:
        if kw in haystack:
            return kw
    return None

# ---------------------------------------------------------------------------
# 핵심 필터 함수 — 점수제 + 네거티브
# ---------------------------------------------------------------------------
def _apply_keyword_filter(
    title: str, summary: str, language: str = "en"
) -> tuple[str, str]:
    """
    점수제 키워드 필터.

    점수 계산:
      +3  FINANCE_KEYWORDS / KOREAN_FINANCE_KEYWORDS 히트
      +1  COUNTRY_KEYWORDS / KOREAN_COUNTRY_KEYWORDS 히트 (국가당 1회)
      -4  EXCLUSION_KEYWORDS 히트
      language='id' → INDONESIAN_FINANCE_KEYWORDS 추가 적용
      language='ko' → KOREAN_FINANCE_KEYWORDS + KOREAN_COUNTRY_KEYWORDS 사용

    통과 기준: score >= PASS_THRESHOLD (= 2)

    Returns (decision, reason)
    """
    text = _normalize(f"{_clean_text(title)} {_clean_text(summary)}")
    score = 0
    top_reason: str | None = None

    # ── 제외 키워드 먼저 확인 ────────────────────────────────
    excl_hit = _first_match(text, EXCLUSION_KEYWORDS)
    if excl_hit:
        score += EXCLUSION_SCORE

    if language == "ko":
        # ── 한국어 금융 키워드 (+3) ───────────────────────────
        fin_hit = _first_match(text, KOREAN_FINANCE_KEYWORDS)
        if fin_hit:
            score += FINANCE_SCORE
            top_reason = f"ko_finance:{fin_hit}"

        # ── 한국어 국가 키워드 (국가당 1회, +1) ──────────────
        country_first: str | None = None
        for country, kws in KOREAN_COUNTRY_KEYWORDS.items():
            hit = _first_match(text, kws)
            if hit:
                score += COUNTRY_SCORE
                if country_first is None:
                    country_first = f"country:{country}:{hit}"
        if top_reason is None:
            top_reason = country_first

    else:
        # ── 영문/기타 금융·ESG 키워드 (+3) ──────────────────
        fin_hit = _first_match(text, FINANCE_KEYWORDS)
        if fin_hit:
            score += FINANCE_SCORE
            top_reason = f"finance:{fin_hit}"

        # ── 인도네시아어 금융 키워드 (+3) ────────────────────
        if language == "id":
            id_hit = _first_match(text, INDONESIAN_FINANCE_KEYWORDS)
            if id_hit:
                score += FINANCE_SCORE
                if top_reason is None:
                    top_reason = f"id_finance:{id_hit}"

        # ── 국가 키워드 (국가당 1회, +1) ─────────────────────
        country_first = None
        for country, kws in COUNTRY_KEYWORDS.items():
            hit = _first_match(text, kws)
            if hit:
                score += COUNTRY_SCORE
                if country_first is None:
                    country_first = f"country:{country}:{hit}"
        if top_reason is None:
            top_reason = country_first

    # ── 판정 ─────────────────────────────────────────────────
    if score >= PASS_THRESHOLD:
        return "passed", top_reason or "passed"
    else:
        if excl_hit:
            return "rejected", f"excl:{excl_hit}(score:{score})"
        return "rejected", f"score:{score}|{top_reason or 'no_match'}"


# ---------------------------------------------------------------------------
# 메인 필터 실행
# ---------------------------------------------------------------------------
def run_keyword_filter(conn: sqlite3.Connection, refilter_all: bool = False) -> dict:
    """
    refilter_all=False : filter_decision='pending' 기사만 처리 (기본)
    refilter_all=True  : 전체 기사 재처리 (키워드·점수 기준 변경 후 사용)
    """
    ensure_filter_columns(conn)
    cur = conn.cursor()

    where = "1=1" if refilter_all else "a.filter_decision = 'pending'"
    rows = cur.execute(f"""
        SELECT a.article_id, a.title, a.summary, m.language, m.tier
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE {where}
    """).fetchall()

    stats = dict(total=len(rows), passed=0, rejected=0)

    for row in rows:
        # Tier 0 (공식 기관) → 키워드 필터 없이 자동 통과
        if row["tier"] == 0:
            decision, reason, stage = "passed", "official_source", 1
        else:
            decision, reason = _apply_keyword_filter(
                row["title"] or "", row["summary"] or "", row["language"] or "en"
            )
            stage = 2

        stats[decision] += 1
        cur.execute(
            """UPDATE articles_raw
               SET filter_stage = ?, filter_decision = ?, filter_reason = ?
               WHERE article_id = ?""",
            (stage, decision, reason, row["article_id"]),
        )

    conn.commit()
    log.info(
        "필터 완료 — 전체=%d  통과=%d  거부=%d  (통과율=%.1f%%)",
        stats["total"], stats["passed"], stats["rejected"],
        stats["passed"] / stats["total"] * 100 if stats["total"] else 0,
    )
    return stats


# ---------------------------------------------------------------------------
# 필터 결과 리포트
# ---------------------------------------------------------------------------
def build_filter_report(conn: sqlite3.Connection) -> str:
    ensure_filter_columns(conn)

    tot = conn.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN filter_decision='passed'   THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN filter_decision='rejected' THEN 1 ELSE 0 END) AS rejected,
               SUM(CASE WHEN filter_decision='pending'  THEN 1 ELSE 0 END) AS pending
        FROM articles_raw
    """).fetchone()

    placeholders = ",".join("?" * len(GLOBAL_CATEGORIES))
    glob = conn.execute(f"""
        SELECT COUNT(DISTINCT a.article_id) AS total,
               SUM(CASE WHEN a.filter_decision='passed'   THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN a.filter_decision='rejected' THEN 1 ELSE 0 END) AS rejected
        FROM articles_raw a
        WHERE EXISTS (
            SELECT 1 FROM media_category_map mc
            WHERE mc.source_id = a.source_id
              AND mc.category_code IN ({placeholders})
        )
    """, GLOBAL_CATEGORIES).fetchone()

    country_rows = conn.execute("""
        SELECT m.primary_country_code AS country,
               COUNT(*) AS total,
               SUM(CASE WHEN a.filter_decision='passed'   THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN a.filter_decision='rejected' THEN 1 ELSE 0 END) AS rejected
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        GROUP BY m.primary_country_code
        ORDER BY passed DESC
    """).fetchall()

    top_hits = conn.execute("""
        SELECT filter_reason, COUNT(*) AS cnt
        FROM articles_raw
        WHERE filter_decision='passed' AND filter_stage=2
        GROUP BY filter_reason
        ORDER BY cnt DESC
        LIMIT 20
    """).fetchall()

    # 제외 키워드로 거부된 기사 TOP 10
    excl_hits = conn.execute("""
        SELECT filter_reason, COUNT(*) AS cnt
        FROM articles_raw
        WHERE filter_decision='rejected' AND filter_reason LIKE 'excl:%'
        GROUP BY filter_reason
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()

    rejected_samples = conn.execute("""
        SELECT m.media_name, a.title, a.filter_reason
        FROM articles_raw a
        JOIN media_sources m ON m.source_id = a.source_id
        WHERE a.filter_decision='rejected'
        ORDER BY RANDOM()
        LIMIT 15
    """).fetchall()

    def pct(n, d):
        return f"{n/d*100:.1f}%" if d else "-"

    ts = datetime.now(timezone.utc).isoformat()
    lines = [
        "# 2단계 키워드 필터 결과 (v2 — 점수제)",
        "",
        f"_생성 시각: {ts}_",
        "",
        "## 점수 체계",
        "",
        "| 신호 | 점수 |",
        "|---|---|",
        f"| 금융·ESG 키워드 히트 | +{FINANCE_SCORE} |",
        f"| 국가 키워드 히트 (국가당 1회) | +{COUNTRY_SCORE} |",
        f"| 스포츠·연예 제외 키워드 히트 | {EXCLUSION_SCORE} |",
        f"| **통과 기준** | **≥ {PASS_THRESHOLD}** |",
        "",
        "## 전체 요약",
        "",
        "| 항목 | 건수 | 비율 |",
        "|---|---|---|",
        f"| 전체 수집 기사 | {tot['total']:,} | 100% |",
        f"| ✅ 통과 (passed) | {tot['passed']:,} | {pct(tot['passed'], tot['total'])} |",
        f"| ❌ 거부 (rejected) | {tot['rejected']:,} | {pct(tot['rejected'], tot['total'])} |",
        f"| ⏳ 미처리 (pending) | {tot['pending']:,} | {pct(tot['pending'], tot['total'])} |",
        "",
        "## 글로벌 매체 키워드 필터 상세",
        "",
        "| 항목 | 건수 | 비율 |",
        "|---|---|---|",
        f"| 글로벌 매체 기사 | {glob['total']:,} | 100% |",
        f"| ✅ 통과 | {glob['passed']:,} | {pct(glob['passed'], glob['total'])} |",
        f"| ❌ 거부 | {glob['rejected']:,} | {pct(glob['rejected'], glob['total'])} |",
        "",
        "## 국가별 수집·필터 현황",
        "",
        "| 국가 | 수집 | 통과 | 거부 | 통과율 |",
        "|---|---|---|---|---|",
    ]
    for r in country_rows:
        lines.append(
            f"| {r['country']} | {r['total']:,} | {r['passed']:,} | "
            f"{r['rejected']:,} | {pct(r['passed'], r['total'])} |"
        )

    lines += [
        "",
        "## 통과 기사 주요 매칭 키워드 (TOP 20)",
        "",
        "| 매칭 근거 | 건수 |",
        "|---|---|",
    ]
    for r in top_hits:
        lines.append(f"| `{r['filter_reason']}` | {r['cnt']:,} |")

    lines += [
        "",
        "## 제외 키워드 히트 현황 (TOP 10)",
        "",
        "| 제외 근거 | 건수 |",
        "|---|---|",
    ]
    for r in excl_hits:
        lines.append(f"| `{r['filter_reason']}` | {r['cnt']:,} |")

    lines += [
        "",
        "## 거부 기사 샘플 (랜덤 15건)",
        "",
        "> 정상적으로 걸러졌는지 확인 → 놓친 금융 기사 있으면 키워드 추가",
        "",
        "| 매체 | 제목 | 거부 근거 |",
        "|---|---|---|",
    ]
    for r in rejected_samples:
        title  = (r["title"]  or "")[:70]
        reason = (r["filter_reason"] or "")[:40]
        lines.append(f"| {r['media_name']} | {title} | `{reason}` |")

    return "\n".join(lines) + "\n"
