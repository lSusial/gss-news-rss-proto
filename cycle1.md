# GLB News RSS — Cycle 1 현황 보고서

> 프로토타입 구성 및 개선 작업 통합 정리  
> 최초 작성: 2026-05-29 / 최종 업데이트: 2026-06-05  
> 대상 경로: `/prototype/`

---

## 1. 프로젝트 개요

글로벌 금융·경제 뉴스를 9개 대상국(미국·중국·일본·인도·인도네시아·베트남·캄보디아·미얀마·글로벌) 기준으로 자동 수집·필터링·AI 분석·요약하여 대시보드로 제공하는 **Python 프로토타입**이다.

**사용 목적**: 한국 금융기관의 해외 진출 국가 경제·금융 동향을 매일 한 번 모니터링  
**최종 목표**: Java/Spring Boot + PostgreSQL 기반 프로덕션 시스템 이관 (실행계획 v3 참조)

---

## 2. 시스템 아키텍처

### 2.1 전체 파이프라인

```
sources.yaml ──► [1단계] RSS 수집          collector.py
                     │
                     ▼
                articles_raw (SQLite)
                filter_decision = 'pending'
                     │
                     ▼
             [2단계] 키워드 필터        keyword_filter.py
                Tier 0 → 자동 통과 (official_source)
                Tier 1/2 → 점수제 (Finance+3, Country+1, Exclusion-4, 통과 ≥ 2점)
                     │
                filter_decision = 'passed' | 'rejected'
                     │
                     ▼
             [3단계] AI 중요도 분석      llm_ranker.py
                Claude Haiku, 10개 배치
                ai_score(1-5) + summary_ko(한글 요약)
                     │
                     ▼
             [4단계] 동향 브리핑 생성    briefing.py
                Claude Sonnet, 국가별 종합 분석
                summary / issues / outlook / keywords
                     │
                     ▼
             [대시보드] Streamlit
                dashboard.py          기존 탭형 (포트 8501)
                dashboard_stocks.py   Apple Stocks형 [신규] (포트 8502)
```

### 2.2 모듈 구성

| 파일 | 역할 |
|---|---|
| `main.py` | CLI 진입점, 서브커맨드 라우팅 |
| `collector.py` | RSS 수집, DB 초기화, 소스 동기화 |
| `keyword_filter.py` | 점수제 키워드 필터, 한국어 분기 |
| `llm_ranker.py` | Claude Haiku 배치 분석, 노이즈 차단 |
| `briefing.py` | Claude Sonnet 국가별 동향 브리핑 [신규] |
| `dashboard.py` | 기존 Streamlit 탭형 대시보드 |
| `dashboard_stocks.py` | Apple Stocks형 대시보드 [신규] |
| `schema.sql` | SQLite 스키마 |
| `sources.yaml` | 78개 매체·피드 정의 |
| `.streamlit/config.toml` | 다크 테마 설정 [신규] |

---

## 3. 데이터 소스 구성

### 3.1 Tier 체계

| Tier | 대상 | 필터 처리 |
|---|---|---|
| **0** | 중앙은행·정부 공식기관 | 키워드 필터 **자동 통과** (`official_source`) |
| **1** | 주요 국제·현지 언론 | 점수제 키워드 필터 적용 |
| **2** | 지역 매체 | 점수제 키워드 필터 적용 |

### 3.2 Tier 0 공식기관 (신규 추가)

| 국가 | 기관명 | 피드 방식 |
|---|---|---|
| US | Federal Reserve | 직접 RSS (`federalreserve.gov/feeds/press_monetary.xml`) |
| JP | Bank of Japan | Google News `site:boj.or.jp when:7d` |
| IN | Reserve Bank of India | Google News `site:rbi.org.in` + 정책 키워드 한정¹ |
| ID | Bank Indonesia | Google News `site:bi.go.id` + 정책 키워드 한정² |
| VN | State Bank of Vietnam | Google News `site:sbv.gov.vn when:7d` |
| KH | National Bank of Cambodia | Google News `site:nbc.gov.kh when:7d` |
| CN | PBOC | Google News `site:pbc.gov.cn when:7d` |
| KR | 한국은행 | Google News `site:bok.or.kr` (한국어 locale) |
| KR | 기획재정부 | Google News `site:moef.go.kr` (한국어 locale) |
| KR | 금융위원회 | Google News `site:fsc.go.kr` (한국어 locale) |

> ¹ RBI: `monetary policy OR rate OR inflation OR regulation OR circular` 키워드 한정  
> ² BI: `monetary OR rate OR inflation OR policy OR rupiah` 키워드 한정  
> (단순 환율 공시·로그인 페이지 수집 방지)

### 3.3 국가별 매체 현황

| 국가 | 매체 수 | 피드 수 | 비고 |
|---|---|---|---|
| GLOBAL | 13개 | ~39개 | Reuters, Bloomberg, FT 등 |
| US | 7개 | ~14개 | Fed 공식 포함 |
| CN | 7개 | ~14개 | PBOC 공식 포함 |
| JP | 7개 | ~14개 | BOJ 공식 포함 |
| IN | 7개 | ~14개 | RBI 공식 포함 |
| ID | 7개 | ~14개 | BI 공식 포함 |
| VN | 6개 | ~12개 | SBV 공식 포함 |
| KH | 6개 | ~10개 | NBC 공식 포함 |
| MM | 5개 | ~8개 | |
| KR | 3개 | ~6개 | 공식기관만 (한국은행·기재부·금융위) |
| **합계** | **78개** | **99개** | Tier 0: 11개 |

> KR 일반 매체(연합뉴스, 한국경제, 매일경제, 동아일보, 조선일보, 중앙일보) 제거.  
> 사유: "해외 국가 모니터링" 목적에 국내 일반 뉴스는 불필요.

### 3.4 제거·교체된 피드

| 매체 | 문제 | 처리 |
|---|---|---|
| VOD English (KH) | 0건 수집 | 제거 |
| Tuoi Tre News (VN) | 0건 수집 | 제거 → Vietnam Investment Review 대체 |
| Saigon Times (VN) | 2건 수집 | 제거 |
| Times of India (직접) | ConnectTimeout | Google News 우회 대체 |
| Economic Times (직접) | ConnectTimeout | Google News 우회 대체 |
| Business Standard (직접) | HTTP 403 | Google News 우회 대체 |

---

## 4. DB 스키마

### 4.1 테이블 목록

```
articles_raw          ← 수집 기사 (핵심)
media_sources         ← 매체 정보 (tier 컬럼 포함)
media_source_feeds    ← 피드 URL
media_category_map    ← 매체-카테고리 N:M
fetch_runs            ← 수집 실행 이력
country_briefings     ← 국가별 동향 브리핑 [신규]
```

### 4.2 articles_raw 주요 컬럼

| 컬럼 | 타입 | 설명 |
|---|---|---|
| article_id | INTEGER PK | 기사 고유 ID |
| feed_id | INTEGER FK | 출처 피드 |
| source_id | INTEGER FK | 출처 매체 |
| title | TEXT | 기사 제목 (최대 1000자) |
| link | TEXT | 기사 URL (최대 2000자) |
| summary | TEXT | 요약/리드 (최대 4000자) |
| published_at | TEXT | 발행 시각 (ISO 8601) |
| content_hash | TEXT UNIQUE | sha256(title+link) — 중복 제거 키 |
| filter_decision | TEXT | pending / passed / rejected |
| filter_reason | TEXT | 통과·거부 근거 (예: finance:trade, official_source) |
| ai_score | INTEGER | AI 중요도 1-5 (NULL=미분석) |
| summary_ko | TEXT | AI 생성 한글 요약 |
| ai_model | TEXT | 사용 모델명 |

### 4.3 country_briefings 테이블 (신규)

| 컬럼 | 설명 |
|---|---|
| cc | 국가코드 (PK) |
| generated_at | 생성 시각 |
| summary | 종합 요약 (4-6문장) |
| issues | JSON: [{title, detail}, ...] |
| outlook | 전망 및 시사점 |
| keywords | JSON: [keyword, ...] |
| source_articles | JSON: [{title, link, source, score}, ...] |
| model | 사용 모델명 |
| article_count | 분석 기사 수 |

### 4.4 현재 DB 데이터 현황 (2026-06-05 기준)

| 항목 | 수치 |
|---|---|
| 전체 수집 기사 | 17,545건 |
| 필터 통과 (passed) | 5,094건 (29.0%) |
| AI 분석 완료 | 2,094건 |
| 활성 피드 | 99개 |
| 수집 실행 횟수 | 9회 |
| 국가별 브리핑 | 9개국 |

---

## 5. 필터링 파이프라인 상세

### 5.1 1단계: RSS 수집 (`collector.py`)

- `requests` + `certifi`로 HTTP 요청 (SSL 안정성)
- `feedparser`로 RSS/Atom 파싱
- 병렬 처리: `ThreadPoolExecutor(max_workers=8)`
- 중복 제거: `content_hash = sha256(title + "\x1f" + link)`
- 타임아웃: 20초
- `sync_sources()` — sources.yaml에서 제거된 매체 피드 자동 비활성화

### 5.2 2단계: 키워드 필터 (`keyword_filter.py`)

#### Tier 0 자동 통과

```python
if row["tier"] == 0:
    decision, reason, stage = "passed", "official_source", 1
else:
    decision, reason = _apply_keyword_filter(text, language)
```

#### 점수 체계 (Tier 1/2)

| 신호 | 점수 |
|---|---|
| 금융·ESG 키워드 히트 | **+3** |
| 국가 키워드 히트 | **+1** (국가당 1회) |
| 스포츠·연예 제외 키워드 | **−4** |
| **통과 기준** | **≥ 2점** |

#### 언어별 키워드 분기

- `language = 'en'` — FINANCE_KEYWORDS(70개+), COUNTRY_KEYWORDS(9개국)
- `language = 'ko'` — KOREAN_FINANCE_KEYWORDS(34개), KOREAN_COUNTRY_KEYWORDS(9개국 한국어)
- `language = 'id'` — INDONESIAN_FINANCE_KEYWORDS(35개) 추가 적용

#### 주요 키워드 카테고리

**FINANCE_KEYWORDS** (영문, 70개+)
- 거시경제: gdp, inflation, recession, monetary policy, interest rate …
- 재정: budget, fiscal policy, tax reform, national debt …
- 금융기관: bank, central bank, imf, world bank, adb …
- 통화: yen, yuan, rupee, rupiah, dong, riel, kyat …
- 주가지수: nikkei, sensex, nifty, nasdaq, s&p 500 …
- 기업거래: merger, acquisition, m&a, ipo, fdi …
- ESG: esg, carbon emission, net zero, green bond …

**KOREAN_FINANCE_KEYWORDS** (한국어, 34개)
- 금리, 기준금리, 환율, 인플레이션, 물가, 경제성장, gdp, 무역, 수출, 수입,
  금융, 은행, 중앙은행, 투자, 주식, 증시, 채권, 외환, 관세, 제재, esg, 반도체 …

**EXCLUSION_KEYWORDS** (30개+)
- 스포츠: gold medal, sea games, powerlifter, world cup qualifier …
- 연예: box office, music festival, grammy award …
- 자연과학: deep-sea, new species, marine biology …

### 5.3 3단계: AI 중요도 분석 (`llm_ranker.py`)

| 항목 | 내용 |
|---|---|
| 모델 | claude-haiku-4-5-20251001 |
| 배치 크기 | 10건/호출 |
| 호출 간격 | 0.5초 |
| 출력 | ai_score(1-5), summary_ko(한글 1-2문장) |
| 처리 우선순위 | Tier 0 먼저 (`ORDER BY m.tier ASC`) |

**노이즈 제목 자동 차단** (신규)

```python
_NOISE_TITLES = {
    "foreign exchange rates", "login", "bank indonesia", "- rbi", ...
}
def _is_noise_title(title):
    if len(title.strip()) < 12: return True
    if title.lower() in _NOISE_TITLES: return True
    ...
# → ai_score=1, summary_ko='[자동제외: 무의미 제목]' 저장 (API 호출 없음)
```

**중요도 기준**

| 점수 | 기준 |
|---|---|
| 5 | 시장 핵심 이슈 (금리 결정, 대형 M&A, GDP 쇼크) |
| 4 | 중요 금융·경제 뉴스 (기업 실적, IPO, 주요 정책) |
| 3 | 관련성 있는 일반 경제 뉴스 |
| 2 | 낮은 관련성 |
| 1 | 금융·경제와 거의 무관 |

### 5.4 4단계: 동향 브리핑 생성 (`briefing.py`) — 신규

```bash
python main.py brief              # 전체 국가 (최근 3일 기준)
python main.py brief --country US # 특정 국가
python main.py brief --days 7     # 최근 7일 기준
```

- 국가별 ai_score ≥ 3 기사 최대 20건을 Claude Sonnet으로 분석
- 생성 내용: 종합 요약(4-6문장) / 주요 이슈(3-5개, 상세 3-4문장) / 전망·시사점(4-5문장) / 키워드
- 분석 관점: 한국 금융기관 해외사업 담당자 시각

---

## 6. 대시보드

### 6.1 기존 대시보드 (`dashboard.py`)

탭 기반 구성. 국가별 탭 × 9개, AI TOP 10, 전체 기사 목록, 공식기관 발표 섹션.

### 6.2 신규 대시보드 (`dashboard_stocks.py`)

Apple Stocks 앱 컨셉. 완전 다크 테마, 단일 피드 뷰.

**레이아웃**

```
[GLB News]                                    [날짜]
[🌐 All][🇺🇸 USA][🇨🇳 CHN]...[📅 전체/2일]

────────────────────────────────────────────

[동향 브리핑 카드 — 파란 배경]
  종합 요약 (4-6문장, 한국어)
  주요 이슈 3-5개 (제목 + 상세 설명)
  전망 & 시사점
  키워드 태그
  참고 기사 링크 (최대 10개)

────────────────────────────────────────────

● SOURCE NAME      시간
  기사 제목 (전체 클릭 → 원문 이동)
  AI 한글 요약

● SOURCE NAME      시간
  ...

────────────────────────────────────────────
[↻ 새로고침]          [🔧 관리자 통계 ▼]
```

**색상 점(●) 구분**

| 색상 | 의미 |
|---|---|
| 🟢 초록 (#30d158) | Tier 0 공식기관 |
| 🔴 빨강 (#ff453a) | score 5 핵심 |
| 🟠 주황 (#ff9f0a) | score 4 중요 |
| 🟡 노랑 (#ffd60a) | score 3 관련 |
| ⚫ 어두운 (#3a3a3c) | score 1-2 |

**주요 구현 사항**

- `.streamlit/config.toml`으로 base 다크 테마 설정 (CSS 단독 처리의 한계 해소)
- 모든 HTML → `st.html()` 렌더링 (Streamlit 마크다운 파서 우회)
- 국가 pill 버튼: `\n`으로 국기(위)/3자리 코드(아래) 2줄 표시, `::first-line` CSS 크기 분리
- 날짜 필터 토글: `📅 전체 ↔ 📅 2일` (운영 시 기본값 변경 가능)
- 관리자 통계(수집 건수, 필터링, 매체별 현황)는 `st.expander`에 숨김

---

## 7. CLI 전체 커맨드

```bash
# 초기화
python main.py init                        # DB 생성 + sources.yaml 동기화

# 수집 파이프라인
python main.py fetch                       # 전체 활성 피드 수집
python main.py filter                      # 키워드 필터 실행
python main.py filter --refilter           # 전체 기사 재필터링
python main.py ai-rank                     # AI 분석 (국가당 최신 50건)
python main.py ai-rank --country US        # 특정 국가만
python main.py ai-rank --limit 30          # 국가당 N건
python main.py brief                       # 전체 국가 동향 브리핑 생성
python main.py brief --country US          # 특정 국가 브리핑
python main.py brief --days 7             # 최근 7일 기준

# 리포트·유틸
python main.py report                      # 매체 가용성 리포트
python main.py filter-report              # 필터 결과 리포트
python main.py list --limit 20             # 최근 수집 기사 출력
python main.py export articles.json        # 전체 기사 JSON 덤프

# 대시보드 실행
streamlit run dashboard.py                 # 기존 탭형 (포트 8501)
streamlit run dashboard_stocks.py          # Apple Stocks형 (포트 8502)
```

---

## 8. 발견 및 해결된 이슈 전체

| 이슈 | 원인 | 해결 |
|---|---|---|
| 직접 RSS 403/404 | 봇 차단, 피드 폐지 | Google News RSS 우회 |
| macOS SSL 인증서 오류 | 시스템 Python CA 미설치 | `certifi` CA 번들 명시 |
| 구 피드 URL 비활성화 누락 | `sync_sources` 미처리 | `UPDATE is_active=0` 추가 |
| `US-China` 하이픈 오탐 | `\s*-\s*` regex가 복합어 분리 | `\s+-\s+` (공백 필수)로 수정 |
| "Cambodian Powerlifter" 통과 | country×2 = +2점 통과 | per-country 1회 카운트 + exclusion 추가 |
| `UnserializableReturnValueError` | `sqlite3.Row` 캐시 직렬화 불가 | `dict()` 래핑 |
| RBI 피드 — "- RBI" 제목 | `site:rbi.org.in` 검색이 환율공시 수집 | 정책 키워드 한정 + 노이즈 차단 |
| BI 피드 — 로그인 페이지 수집 | `site:bi.go.id` 정적 페이지 포함 | `monetary OR rate ...` 키워드 한정 |
| Streamlit HTML 깨짐 | `st.markdown(unsafe_allow_html)` 마크다운 파서 오동작 | 모든 복잡한 HTML → `st.html()` 전환 |
| f-string 백슬래시 SyntaxError | Python 3.11 f-string 내 `\"` 불가 | HTML 블록 변수 분리 후 조합 |
| pill 버튼 "Cambodia" 글자 잘림 | 좁은 버튼에 8자 텍스트 | 국가명 3자리 코드(KHM)로 통일 + `height:auto` |
| ngrok 설치 실패 | 네트워크 SSL 차단 | Cloudflare Tunnel(`cloudflared`)으로 대체 |
| 불량 기사 202건 잔류 | 피드 개선 전 수집된 노이즈 | DB에서 직접 DELETE |

---

## 9. 시연 환경

| 항목 | 내용 |
|---|---|
| 로컬 URL | http://localhost:8502 |
| 공개 공유 | `cloudflared tunnel --url http://localhost:8502` |
| 공개 URL 특성 | 터널 재시작 시 URL 변경, 로컬 PC 켜져 있는 동안만 유효 |

---

## 10. 디렉터리 구조

```
prototype/
├── main.py                 # CLI 진입점
├── collector.py            # RSS 수집기
├── keyword_filter.py       # 키워드 필터 (한국어 분기 포함)
├── llm_ranker.py           # Claude Haiku AI 분석 (노이즈 차단 포함)
├── briefing.py             # Claude Sonnet 동향 브리핑 [신규]
├── dashboard.py            # 기존 탭형 대시보드
├── dashboard_stocks.py     # Apple Stocks형 대시보드 [신규]
├── schema.sql              # SQLite 스키마
├── sources.yaml            # 78개 매체·피드 정의
├── requirements.txt        # Python 패키지 의존성
├── cycle1.md               # 본 문서
├── .streamlit/
│   └── config.toml         # 다크 테마 설정 [신규]
└── data/
    ├── news.db             # SQLite DB (17,545건)
    ├── filter_report.md
    └── availability_report.md
```

---

## 11. 의존성

| 패키지 | 용도 |
|---|---|
| feedparser | RSS/Atom 파싱 |
| PyYAML | sources.yaml 파싱 |
| certifi | SSL 인증서 번들 |
| requests | HTTP 클라이언트 |
| anthropic | Claude API SDK |
| streamlit | 대시보드 프레임워크 |
| pyngrok | ngrok Python 래퍼 (설치됨, 현재 cloudflared 사용) |

| 환경변수 | 필수 | 용도 |
|---|---|---|
| `ANTHROPIC_API_KEY` | AI 분석·브리핑 시 | Claude API 인증 |

---

## 12. 다음 사이클(Cycle 2) 후보 과제

### 우선순위 HIGH

1. **자동화 파이프라인** — `fetch → filter → ai-rank → brief` 일괄 실행 커맨드
2. **macOS launchd 스케줄러** — 매일 오전 7시 자동 수집·분석
3. **날짜 기반 아카이빙** — 30일 이상 기사 자동 삭제

### 우선순위 MEDIUM

4. **score ≥ 4 알림** — 이메일 또는 Slack webhook
5. **GLOBAL 탭 브리핑** — 전국가 통합 동향 브리핑
6. **브리핑 히스토리** — 날짜별 이전 브리핑 조회
7. **공개 배포 고정화** — Railway/Render 클라우드 배포

### 우선순위 LOW (Java 이관 시)

8. **PostgreSQL 이관** — SQLite → PostgreSQL + pgvector
9. **Spring Boot API 서버** — REST API 레이어
10. **벡터 검색** — 유사 기사 추천

---

*최종 업데이트: 2026-06-05*
