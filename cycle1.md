# GLB News RSS — 현황 보고서

> 프로토타입 구성 및 개선 작업 통합 정리
> 최초 작성: 2026-05-29 / 최종 업데이트: 2026-06-09
> 대상 경로: `/prototype/`

---

## 1. 프로젝트 개요

글로벌 금융·경제 뉴스를 9개 대상국(미국·중국·일본·인도·인도네시아·베트남·캄보디아·미얀마·글로벌) 기준으로 자동 수집·필터링·AI 분석·요약하여 대시보드로 제공하는 **Python 프로토타입**이다.

**사용 목적**: 한국 금융기관의 해외 진출 국가 경제·금융 동향을 매일 한 번 모니터링
**최종 목표**: Java/Spring Boot + PostgreSQL 기반 프로덕션 시스템 이관

---

## 2. 전체 파이프라인

```
매일 새벽 실행 순서:
  python main.py fetch                    # 피드 수집 (전날 밤~새벽 기사)
  python main.py filter                   # 키워드 필터 (v3, 제목/본문 분리 가중치)
  python main.py dedup                    # 중복 기사 클러스터링
  python main.py llm-filter               # LLM 1차 관문 (Haiku, 비관련 기사 제거)
  python main.py ai-rank                  # AI 중요도 분석 (1-5점 + 한글 요약 + 토픽)
  python main.py brief --type daily       # 일일 브리핑 (어제 기사 기준)
  python main.py brief --type weekly      # 주간 브리핑 (어제 기준 월~일)
  python export_html.py                   # 날짜별 HTML 스냅샷 생성
```

```
sources.yaml ──► [1단계] RSS 수집          collector.py
                     │
                     ▼
                articles_raw (SQLite)
                     │
                     ▼
             [2단계] 키워드 필터        keyword_filter.py  (v3)
                Tier 0 → 자동 통과
                Tier 1/2 → 제목/본문 분리 점수제 (통과 ≥ 3점)
                단어 경계 매칭 (bis/rbi/riel 오탐 방지)
                     │
                     ▼
             [2.5단계] LLM 1차 관문     llm_prefilter.py
                Haiku, 20건 배치
                키워드 오탐 제거 (food bank, Serbia→rbi 등)
                     │
                     ▼
             [3단계] AI 중요도 분석      llm_ranker.py
                Haiku, 10건 배치
                ai_score(1-5) + summary_ko + topics(태그)
                     │
                     ▼
             [4단계] 동향 브리핑         briefing.py
                Sonnet, 국가별
                일일(하루) / 주간(월~일 캘린더 주)
                     │
                     ▼
             [대시보드] Streamlit        dashboard_stocks.py
             [HTML 스냅샷]              export_html.py
```

---

## 3. 모듈 구성

| 파일 | 역할 |
|---|---|
| `main.py` | CLI 진입점, 서브커맨드 라우팅 |
| `collector.py` | RSS 수집, DB 초기화, 소스 동기화 |
| `keyword_filter.py` | 점수제 키워드 필터 v3 + 중복 탐지 |
| `llm_prefilter.py` | Haiku LLM 1차 관문 (Tier 0 자동 통과) |
| `llm_ranker.py` | Haiku AI 분석 (score + 요약 + 토픽) |
| `briefing.py` | Sonnet 일일/주간 브리핑 생성 |
| `dashboard_stocks.py` | Apple Stocks형 Streamlit 대시보드 |
| `export_html.py` | 날짜별 정적 HTML 내보내기 |
| `schema.sql` | SQLite 스키마 |
| `sources.yaml` | 78개 매체·피드 정의 |

---

## 4. CLI 전체 커맨드

```bash
# 초기화
python main.py init

# 파이프라인
python main.py fetch
python main.py filter [--refilter]
python main.py dedup [--recheck]
python main.py llm-filter [--country XX] [--limit N]
python main.py ai-rank [--country XX] [--limit N]
python main.py retag [--days 7] [--country XX]     # 기존 기사 토픽 태깅
python main.py brief --type daily [--date YYYY-MM-DD]
python main.py brief --type weekly [--date YYYY-MM-DD]

# HTML 내보내기
python export_html.py                   # 어제 일일+주간
python export_html.py --all             # DB 전체
python export_html.py --date 2026-06-07 --type daily

# 대시보드
streamlit run dashboard_stocks.py       # 포트 8502

# 리포트
python main.py report
python main.py filter-report
python main.py list [--limit 20]
python main.py export articles.json
```

---

## 5. 데이터 소스

### Tier 체계

| Tier | 대상 | 필터 처리 |
|---|---|---|
| **0** | 중앙은행·정부 공식기관 (11개) | 키워드·LLM 관문 **자동 통과** |
| **1** | 주요 국제·현지 언론 | 점수제 키워드 + LLM 관문 |
| **2** | 지역 매체 | 점수제 키워드 + LLM 관문 |

**Tier 0 공식기관**: Fed, BOJ, RBI, BI, SBV, NBC, PBOC, 한국은행, 기재부, 금융위, MOEF

**전체**: 78개 매체, 99개 피드

---

## 6. DB 스키마 주요 컬럼

### articles_raw

| 컬럼 | 설명 |
|---|---|
| `article_id` | PK |
| `title`, `link`, `summary` | 기사 정보 |
| `published_at`, `fetched_at` | 날짜 |
| `filter_decision` | pending / passed / rejected |
| `filter_reason` | 통과 근거 키워드 |
| `filter_score` | 키워드 합산 점수 |
| `llm_prefilter` | passed / rejected (LLM 1차 관문) |
| `duplicate_of` | 중복인 경우 원본 article_id |
| `ai_score` | AI 중요도 1-5 |
| `summary_ko` | AI 한글 요약 |
| `topics` | AI 토픽 태그 JSON (예: `["금리인상","BOJ","엔화"]`) |
| `ai_model` | 사용 모델명 |

### country_briefings

| 컬럼 | 설명 |
|---|---|
| `cc` | 국가코드 |
| `briefing_date` | 기준 날짜 (daily=해당일, weekly=해당주 월요일) |
| `briefing_type` | daily / weekly |
| `summary`, `issues`, `outlook`, `keywords` | 브리핑 내용 |
| `source_articles` | 참고 기사 링크 |
| `article_count` | 분석 기사 수 |

---

## 7. 필터링 상세

### 키워드 필터 v3 점수 체계

| 신호 | 점수 |
|---|---|
| 금융 키워드 — 제목 히트 | +5 |
| 금융 키워드 — 본문 히트 | +3 |
| 국가 키워드 — 제목 히트 | +2 |
| 국가 키워드 — 본문 히트 | +1 |
| 제외 키워드(스포츠·연예) | -4 |
| **통과 기준** | **≥ 3** |

**단어 경계 매칭**: `bis`(ibis 오탐), `rbi`(Serbia 오탐), `riel`(Gabriel 오탐), `yen`(Feyenoord 오탐) 등 수정

### LLM 관문 결과 (전체 적용)
- 처리: 4,616건 → 거부 1,205건 (26.1% 추가 제거)
- Tier 0 공식기관은 자동 통과

---

## 8. 대시보드 UI

### 뷰 타입 (최상단)

| 버튼 | 동작 |
|---|---|
| **주간 브리핑** | 선택한 주(월~일) 브리핑 카드만 표시 |
| **일일** | 선택한 날짜 브리핑 + 뉴스 목록 |
| **실시간** | 오늘 수집 기사 표시, 브리핑 없음 |

### 날짜 네비게이션
- `◀ [날짜선택기] ▶` — 일일: 1일씩, 주간: 1주씩 이동
- 실시간 선택 시 날짜 선택 비활성화
- 기본값: 주간 → **전주 월요일**, 일일 → 어제

### 모바일 UI 개선 (2026-06-09)
- 국가 선택: pill 버튼 → **드롭다운(selectbox)** 으로 교체
- 뷰 타입 버튼(주간/일일/실시간)과 날짜 네비를 각각 별도 줄로 분리
- 모바일에서 컬럼이 세로로 쌓이는 문제 CSS로 해결 (`flex-direction: row`)
- 입력 포커스 시 자동 확대 방지 (`font-size: 16px`)
- 수집 건수·필터 통과율 등 관리 정보를 **접힌 expander로 숨김** (일반 사용자 노출 제거)

### 기사 색상 점

| 색상 | 의미 |
|---|---|
| 🟢 초록 | Tier 0 공식기관 |
| 🔴 빨강 | score 5 핵심 |
| 🟠 주황 | score 4 중요 |
| 🟡 노랑 | score 3 관련 |
| ⚫ 어두운 | score 1-2 |

---

## 9. 운영 서버 (Oracle Cloud)

- 서버: Oracle Cloud 무료 티어 Ubuntu (`/home/ubuntu/gss-news-rss-proto`)
- 배포 방식: `git push` → 서버에서 `git pull`
- 자동화: `run_pipeline.sh` crontab 등록 (한국 시간 06:00 = UTC 21:00)
  ```
  0 21 * * * /home/ubuntu/gss-news-rss-proto/run_pipeline.sh >> /home/ubuntu/glbnews.log 2>&1
  ```
- 특정 국가 재실행:
  ```bash
  python main.py llm-filter --country MM
  python main.py ai-rank --country MM
  python main.py brief --country MM --type daily --date YYYY-MM-DD
  ```

---

## 10. HTML 스냅샷 / 배포

```
data/html/
├── index.html              ← 날짜별 브리핑 목록
├── 2026-06-07_daily.html
├── 2026-06-07_weekly.html
└── ...
```

**Netlify Drop 배포**: `data/html/` 폴더를 app.netlify.com/drop에 드래그

---

## 11. 현재 DB 데이터 현황 (2026-06-08 기준)

| 항목 | 수치 |
|---|---|
| 전체 수집 기사 | 21,239건 |
| 키워드 필터 통과 | 5,007건 (23.6%) |
| LLM 관문 처리 | 4,616건 → 통과 3,411건 |
| AI 분석 완료 | ~2,500건 |
| 토픽 태그 생성 | 최근 7일 기사 (~1,424건, 진행 중) |
| 브리핑 | 일일 8개국×8일 + 주간 10개국 |
| 활성 피드 | 99개 |

---

## 12. 예상 운영 비용 (30명 기준)

| 항목 | 월 비용 |
|---|---|
| Anthropic API (Haiku + Sonnet) | ~$27 |
| AWS EC2 t3.small + EBS | ~$18 |
| **합계 (프로토타입)** | **~$45/월** |
| Java 이관 후 (RDS + ALB 포함) | ~$100/월 |

---

## 13. 다음 과제

### 자동화
- ~~macOS launchd 또는 AWS EventBridge로 매일 새벽 파이프라인 자동 실행~~ → **완료** (Oracle Cloud 서버 cron 등록, 한국 시간 06:00, `run_pipeline.sh` git 관리)

### 기능
- 토픽 태그 기반 대시보드 필터링 (태그 클릭 → 관련 기사)
- 날짜별 HTML 자동 Netlify 배포

### 프로덕션 이관
- SQLite → PostgreSQL + pgvector
- Python → Java/Spring Boot REST API
- 벡터 검색 (유사 기사 추천)

---

*최종 업데이트: 2026-06-09*
