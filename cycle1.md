# GLB News RSS — Cycle 1 분석 보고서

> 리버스 엔지니어링 기반 시스템 구성 및 요구사항 분석  
> 분석 기준일: 2026-05-29  
> 대상 경로: `/prototype/`

---

## 1. 프로젝트 개요

글로벌 금융·경제·ESG 뉴스를 9개 대상국(미국·중국·일본·인도·인도네시아·베트남·캄보디아·미얀마·글로벌) 기준으로 자동 수집·필터링·요약하여 대시보드로 제공하는 **Python 프로토타입**이다.

최종 목표는 Java/Spring Boot + PostgreSQL 기반 프로덕션 시스템으로 이관(실행계획 v3 참조)이며, Cycle 1은 그 **검증 MVP**에 해당한다.

---

## 2. 시스템 아키텍처

### 2.1 전체 파이프라인

```
┌─────────────────────────────────────────────────────────────────┐
│                        데이터 파이프라인                          │
│                                                                   │
│  sources.yaml ──► [1단계] RSS 수집          collector.py         │
│                       │                                           │
│                       ▼                                           │
│                  articles_raw (SQLite)                            │
│                  filter_decision = 'pending'                      │
│                       │                                           │
│                       ▼                                           │
│               [2단계] 키워드 필터        keyword_filter.py        │
│                  점수제 (Finance+3, Country+1, Exclusion-4)       │
│                  통과 기준: ≥ 2점                                 │
│                       │                                           │
│                  filter_decision = 'passed' | 'rejected'          │
│                       │                                           │
│                       ▼                                           │
│               [3단계] AI 중요도 분석      llm_ranker.py           │
│                  Claude Haiku API, 10개 배치                      │
│                  ai_score(1-5) + summary_ko(한글 요약)            │
│                       │                                           │
│                       ▼                                           │
│               [대시보드] Streamlit         dashboard.py           │
│                  국가별 탭 + AI TOP 10 + 전체 기사 목록           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 모듈 구성

| 파일 | 역할 | 코드량 |
|---|---|---|
| `main.py` | CLI 진입점, 서브커맨드 라우팅 | 178줄 |
| `collector.py` | RSS 수집, DB 초기화, 소스 동기화 | 346줄 |
| `keyword_filter.py` | 점수제 키워드 필터, 텍스트 정제 | 517줄 |
| `llm_ranker.py` | Claude API 배치 호출, AI 분석 | 209줄 |
| `dashboard.py` | Streamlit 대시보드 | 285줄 |
| `schema.sql` | SQLite 스키마 정의 | 78줄 |
| `sources.yaml` | 매체·피드 설정 | 703줄 |
| `requirements.txt` | 의존성 | 4줄 |
| **합계** | | **2,320줄** |

---

## 3. 데이터 소스 구성

### 3.1 대상 국가 및 매체 수

| 국가코드 | 국가명 | 매체 수 | 피드 수 |
|---|---|---|---|
| GLOBAL | 글로벌 (Reuters, Bloomberg, FT 등) | 13개 | ~39개 |
| US | 미국 | 6개 | ~12개 |
| CN | 중국 | 6개 | ~12개 |
| JP | 일본 | 6개 | ~12개 |
| IN | 인도 | 6개 | ~12개 |
| ID | 인도네시아 | 6개 | ~12개 |
| VN | 베트남 | 6개 | ~12개 |
| KH | 캄보디아 | 6개 | ~12개 |
| MM | 미얀마 | 6개 | ~12개 |
| **합계** | | **61개 매체** | **활성 91개** |

> KR(한국) 매체는 초기 설계에 포함됐으나 "금융·경제 특화" 방향에 따라 Cycle 1에서 제거됨 (DB에는 6개 잔류)

### 3.2 피드 전략

- **직접 RSS**: 공식 피드 URL이 안정적인 매체
- **Google News RSS 폴백**: 403/404/타임아웃 등 직접 접근이 차단된 매체에 적용
  - 패턴: `https://news.google.com/rss/search?q=site:DOMAIN+when:1d&hl=en-US&gl=US&ceid=US:en`
  - 언어별 locale 분리: 영어(`en-US`), 한국어(`ko-KR`), 인도네시아어(`id-ID`)

### 3.3 피드 섹션 분류

| 섹션명 | 피드 수 | 용도 |
|---|---|---|
| top | 42 | Google News 검색 결과 (주요 헤드라인) |
| business | 20 | 비즈니스·경제 섹션 |
| world | 13 | 국제 뉴스 |
| economy | 4 | 경제 전문 |
| markets | 4 | 시장·금융 |
| 기타 | 8 | china, home, all 등 |

---

## 4. DB 스키마

### 4.1 테이블 목록

```
articles_raw          ← 수집 기사 (핵심)
media_sources         ← 매체 정보
media_source_feeds    ← 피드 URL
media_category_map    ← 매체-카테고리 N:M
fetch_runs            ← 수집 실행 이력
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
| filter_stage | INTEGER | 0=미처리, 2=키워드필터완료, 3=LLM(미구현) |
| filter_decision | TEXT | pending / passed / rejected |
| filter_reason | TEXT | 통과·거부 근거 (예: finance:trade) |
| ai_score | INTEGER | AI 중요도 1-5 (NULL=미분석) |
| summary_ko | TEXT | AI 생성 한글 요약 |
| ai_model | TEXT | 사용 모델명 |

### 4.3 현재 DB 데이터 현황

| 항목 | 수치 |
|---|---|
| 전체 수집 기사 | 4,489건 |
| 필터 통과 (passed) | 1,365건 (30.4%) |
| 필터 거부 (rejected) | 3,124건 (69.6%) |
| AI 분석 완료 | 399건 |
| 활성 피드 | 91개 |
| 수집 실행 횟수 | 3회 |

---

## 5. 필터링 파이프라인 상세

### 5.1 1단계: RSS 수집 (`collector.py`)

- `requests` + `certifi`로 HTTP 요청 (SSL 안정성)
- `feedparser`로 RSS/Atom 파싱
- 병렬 처리: `ThreadPoolExecutor(max_workers=8)`
- 중복 제거: `content_hash = sha256(title + "\x1f" + link)`
- 저장: INSERT OR IGNORE (UNIQUE 제약 활용)
- 타임아웃: 20초

### 5.2 2단계: 키워드 필터 (`keyword_filter.py`)

#### 텍스트 정제 (`_clean_text`)
1. HTML 엔티티 디코딩 (`html.unescape`)
2. HTML 태그 제거
3. `&nbsp;` → 공백 변환
4. Google News RSS 출처명 제거:
   - `" - Source Name"` 패턴 (공백 필수 — `US-China` 같은 복합어 보호)
   - `"  Source Name"` 패턴

#### 점수 체계

| 신호 | 점수 | 설명 |
|---|---|---|
| 금융·ESG 키워드 히트 | **+3** | 통화·지수·중앙은행 포함 |
| 국가 키워드 히트 | **+1** | 지명·인명 등, 국가당 1회 |
| 스포츠·연예 제외 키워드 | **-4** | 명백한 비금융 신호 |
| **통과 기준** | **≥ 2점** | |

#### 키워드 카테고리

**FINANCE_KEYWORDS** (약 70개+)
- 거시경제: gdp, inflation, recession, monetary policy, interest rate …
- 재정: budget, fiscal policy, tax reform, national debt …
- 금융기관: bank, banking, central bank, imf, world bank, adb …
- 통화: yen, yuan, rupee, rupiah, dong, riel, kyat, usd …
- 주가지수: nikkei, topix, sensex, nifty, kospi, nasdaq, s&p 500 …
- 기업거래: deal, merger, acquisition, m&a, ipo, investment, fdi …
- ESG: esg, carbon emission, net zero, renewable energy, green bond …
- EV·에너지: electric vehicle, new energy vehicle, energy transition …

**COUNTRY_KEYWORDS** (9개국 × 10~15개)
- 각 국가의 지명, 주요 도시, 지도자 이름, 중앙은행명 (단, 통화·지수는 FINANCE로 이동)

**EXCLUSION_KEYWORDS** (약 30개)
- 스포츠: gold medal, sea games, powerlifter, world cup qualifier …
- 연예: box office, music festival, grammy award …
- 자연과학: deep-sea, new species, marine biolog …
- 부고: dies at, obituary, saxophonist …

**INDONESIAN_FINANCE_KEYWORDS** (약 35개)
- 인도네시아어 매체 전용 (language='id' 일 때 적용)

### 5.3 3단계: AI 중요도 분석 (`llm_ranker.py`)

| 항목 | 내용 |
|---|---|
| 모델 | claude-haiku-4-5-20251001 |
| 배치 크기 | 10건/호출 |
| 호출 간격 | 0.5초 (rate limit 방지) |
| 출력 | ai_score(1-5), summary_ko(한글 1-2문장) |
| 비용 | 400건 기준 약 $0.05 미만 |

**중요도 기준**
- 5점: 시장 핵심 이슈 (금리 결정, 대형 M&A, GDP 쇼크)
- 4점: 중요 금융·경제 뉴스 (기업 실적, IPO, 주요 정책)
- 3점: 관련성 있는 일반 경제 뉴스
- 2점: 낮은 관련성
- 1점: 금융·경제와 거의 무관

---

## 6. 대시보드 (`dashboard.py`)

### 6.1 구조

```
사이드바
├── 관련 기사 수 (passed)
├── 활성 피드 수
├── AI 분석 완료 수
├── 마지막 수집 시각
└── 새로고침 버튼

메인 (국가별 탭 × 9개)
├── 메트릭 4개: 관련기사 / 수집기사 / 통과율 / AI분석건수
├── 🤖 AI 선별 주요 뉴스 TOP 10
│   ├── 순위 번호 + 중요도 배지 (⭐⭐⭐ 핵심 / ⭐⭐ 중요 / ⭐ 관련)
│   ├── 기사 제목 (링크)
│   ├── 매체명 + 발행 시각 (N분/시간 전)
│   └── AI 한글 요약 (파란 배경 박스)
├── [구분선]
└── 📋 전체 기사 목록 (최신 30건) + 우측 매체별 현황
```

### 6.2 Streamlit 캐싱 전략

| 함수 | 캐시 방식 | TTL |
|---|---|---|
| `get_conn()` | `@st.cache_resource` | 영구 (세션 유지) |
| `load_overview()` | `@st.cache_data` | 60초 |
| `load_country_stat()` | `@st.cache_data` | 60초 |
| `load_ai_top()` | `@st.cache_data` | 60초 |
| `load_articles()` | `@st.cache_data` | 60초 |
| `load_sources_for_country()` | `@st.cache_data` | 60초 |

---

## 7. CLI 커맨드 목록

```bash
python main.py init                        # DB 생성 + sources.yaml 동기화
python main.py fetch                       # 전체 활성 피드 1회 수집
python main.py filter                      # 키워드 필터 (pending → passed/rejected)
python main.py filter --refilter           # 전체 기사 재필터링
python main.py filter-report              # 필터 결과 리포트 → data/filter_report.md
python main.py ai-rank                     # AI 분석 (국가당 최신 50건)
python main.py ai-rank --country KH        # 특정 국가만
python main.py ai-rank --limit 30          # 국가당 N건
python main.py report                      # 매체 가용성 리포트
python main.py list --limit 20             # 최근 수집 기사 출력
python main.py export articles.json        # 전체 기사 JSON 덤프

streamlit run dashboard.py                 # 대시보드 실행
```

---

## 8. 의존성

### Python 패키지 (requirements.txt + 추가 설치)

| 패키지 | 버전 | 용도 |
|---|---|---|
| feedparser | ≥6.0.10 | RSS/Atom 파싱 |
| PyYAML | ≥6.0 | sources.yaml 파싱 |
| certifi | ≥2024.2.2 | SSL 인증서 번들 |
| requests | ≥2.31.0 | HTTP 클라이언트 |
| anthropic | 0.104.1 | Claude API SDK |
| streamlit | (설치됨) | 대시보드 프레임워크 |

### 환경변수

| 변수명 | 필수 | 용도 |
|---|---|---|
| `ANTHROPIC_API_KEY` | AI 분석 시 필수 | Claude API 인증 |

---

## 9. 발견된 주요 이슈 및 해결 이력

### 9.1 수집 안정성

| 이슈 | 해결 |
|---|---|
| 직접 RSS가 403/404인 매체 | Google News RSS 검색 URL로 대체 |
| macOS SSL 인증서 미설치 | `certifi` CA 번들 명시 사용 |
| 봇 차단 (403) | 브라우저 User-Agent 설정 |
| 구 URL이 비활성화되지 않음 | `sync_sources`에 `UPDATE is_active=0` 추가 |

### 9.2 필터링 오탐 (False Positive) 수정 이력

| 오탐 케이스 | 원인 | 수정 |
|---|---|---|
| "Scientists Find Deep-Sea Octopus - Cambodianess" 통과 | 제목 끝 출처명 "Cambodianess"에서 "cambodia" 매칭 | `_clean_text()` 함수로 출처명 제거 |
| Google News summary HTML 내 출처명 매칭 | `<font>Cambodianess</font>` 태그 내 "cambodia" 매칭 | HTML 태그·엔티티 정제 추가 |
| "US-China trade tensions" → score:0 오류 | `\s*-\s*` regex가 "US-China" 하이픈을 출처 구분자로 오인 | `\s+-\s+` (공백 필수)로 수정 |
| "Cambodian Powerlifter" 통과 | country:KH + country:VN = +2점 통과 | EXCLUSION_KEYWORDS에 powerlifter 추가 → -4점 |
| "Cambodia 왕 기사" 통과 | cambodia(+1) + cambodian(+1) = +2점 | per-country 1회 카운트로 환원 → +1점으로 탈락 |
| "nse" 부분 문자열 오탐 | "nonsense" 등에서 "nse" 매칭 | FINANCE_KEYWORDS에서 nse/bse 제거 (COUNTRY에만 유지) |

### 9.3 Streamlit 캐싱 이슈

| 이슈 | 해결 |
|---|---|
| `UnserializableReturnValueError` | `sqlite3.Row` → `dict()` 래핑으로 직렬화 가능하게 변경 |

---

## 10. 현재 한계 및 미구현 사항

| 항목 | 상태 | 비고 |
|---|---|---|
| filter_stage=3 (LLM 분류기) | 미구현 | ai-rank가 사후 분석으로 대체 |
| 자동 주기 수집 (cron/scheduler) | 미구현 | 수동 `python main.py fetch` 실행 |
| 기사 본문 크롤링 | 미구현 | 현재 RSS 요약(summary)만 사용 |
| 증분 AI 분석 | 부분 구현 | `ai_score IS NULL` 조건으로 미분석 건만 처리 |
| 중요도 기반 자동 알림 | 미구현 | score ≥ 4 기사 Push/Email 없음 |
| 다국어 요약 (한글 외) | 미구현 | 영문 요약 옵션 없음 |
| KR(한국) 매체 | 제거 | 금융·경제 특화로 방향 전환 시 제외 결정 |

---

## 11. 다음 사이클(Cycle 2) 후보 과제

### 우선순위 HIGH

1. **자동 수집 스케줄러** — `schedule` 라이브러리 또는 cron으로 1시간/6시간 단위 자동 실행
2. **AI 자동 분석 연동** — 수집 후 자동으로 `ai-rank` 실행 (신규 기사만)
3. **기사 본문 크롤링** — `newspaper3k` 또는 `trafilatura`로 본문 추출 → 요약 품질 향상

### 우선순위 MEDIUM

4. **score ≥ 4 알림** — 이메일 또는 Slack webhook으로 중요 기사 발송
5. **검색 기능** — 대시보드 내 키워드 검색 (제목 + 요약 대상)
6. **날짜 필터** — 오늘/이번 주/기간 선택 기능

### 우선순위 LOW (Java 이관 시)

7. **PostgreSQL 이관** — SQLite → PostgreSQL + pgvector
8. **Spring Boot API 서버** — REST API 레이어 추가
9. **벡터 검색** — 기사 임베딩 기반 유사 기사 추천

---

## 12. 디렉터리 구조

```
prototype/
├── main.py              # CLI 진입점
├── collector.py         # RSS 수집기
├── keyword_filter.py    # 점수제 키워드 필터
├── llm_ranker.py        # Claude AI 중요도 분석
├── dashboard.py         # Streamlit 대시보드
├── schema.sql           # SQLite 스키마
├── sources.yaml         # 61개 매체·피드 정의
├── requirements.txt     # Python 패키지 의존성
└── data/
    ├── news.db          # SQLite DB (4,489건)
    ├── filter_report.md # 필터 결과 리포트
    └── availability_report.md  # 피드 가용성 리포트
```

---

*분석 완료: 2026-05-29*
