# GLB News RSS — 프로토타입

글로벌 금융·경제 뉴스를 9개국(US·CN·JP·IN·ID·VN·KH·MM·KR) + 글로벌 매체에서 수집하고, AI로 분류·요약하여 브리핑을 생성하는 파이프라인 + Streamlit 대시보드입니다.

---

## 아키텍처

```
RSS 수집 → 키워드 필터 → 중복 제거 → LLM 관문 → AI 분석·요약 → 브리핑 생성
                                                           ↓
                                                  Streamlit 대시보드
```

### 파이프라인 단계

| 단계 | 모듈 | 설명 |
|---|---|---|
| 1. 수집 | `collector.py` | 80개+ 매체 RSS 병렬 수집 (feedparser) |
| 2. 키워드 필터 | `keyword_filter.py` | 제목/본문 분리 점수제 — 금융·ESG 키워드 가중치 |
| 3. 중복 제거 | `keyword_filter.py` | 제목 유사도(≥0.75)로 중복 기사 클러스터링 |
| 4. LLM 관문 | `llm_prefilter.py` | Haiku로 비금융 기사 제거 (20개 배치) |
| 5. AI 분석 | `llm_ranker.py` | Haiku로 중요도 1-5점 + 한글 요약 + 토픽 태그 |
| 6. 브리핑 | `briefing.py` | 국가별 일일/주간 브리핑 생성 |

### 화면 구성 (Streamlit)

| 페이지 | 파일 | 내용 |
|---|---|---|
| 대시보드 | `dashboard_stocks.py` | TODAY'S TOP 3, 국가별 필터, TIMEBAR, 수집 현황 |
| 뉴스 | `pages/2_뉴스.py` | 주간브리핑 / 뉴스피드 / 실시간 탭, 날짜 네비게이션 |
| 테마뷰 | `pages/1_테마뷰.py` | ESG · 규제 · 리스크 테마별 기사 필터링 |

---

## 폴더 구조

```
prototype/
├─ dashboard_stocks.py     대시보드 메인 페이지
├─ pages/
│   ├─ 1_테마뷰.py         테마별 기사 뷰 (ESG / 규제 / 리스크)
│   └─ 2_뉴스.py           뉴스피드·브리핑 페이지
├─ main.py                 CLI 진입점
├─ collector.py            RSS 수집 모듈
├─ keyword_filter.py       2단계 키워드 필터 + 중복 제거
├─ llm_prefilter.py        LLM 관문 (Haiku, 비관련 기사 제거)
├─ llm_ranker.py           AI 중요도 분석·한글 요약 (Haiku)
├─ briefing.py             국가별 브리핑 생성
├─ schema.sql              SQLite 스키마
├─ sources.yaml            매체 RSS 카탈로그
├─ requirements.txt
└─ data/
    └─ news.db             SQLite DB (WAL 모드)
```

---

## 설치

```bash
cd prototype
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

환경변수:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # Haiku 사용 (LLM 관문·AI 분석·브리핑)
```

---

## 파이프라인 실행

### 전체 파이프라인 (권장)

```bash
# 어제 기사 기준 일일 브리핑 (기본)
python main.py run-all

# 옵션 예시
python main.py run-all --limit 300 --limit-per-media 60   # 국가당 300건, 매체당 60건
python main.py run-all --skip-fetch                        # 수집 건너뛰고 필터부터
python main.py run-all --type weekly                       # 주간 브리핑
```

### 단계별 실행

```bash
python main.py init                       # 최초 1회: DB 생성 + sources.yaml 동기화
python main.py fetch                      # 전체 피드 수집
python main.py filter                     # 키워드 필터 (미처리 기사만)
python main.py filter --refilter          # 전체 재처리 (키워드 변경 후)
python main.py dedup                      # 중복 기사 표시
python main.py llm-filter                 # LLM 관문 (비관련 기사 제거)
python main.py llm-filter --country IN   # 특정 국가만
python main.py ai-rank                    # AI 중요도 분석 (국가당 200건)
python main.py ai-rank --country KH       # 캄보디아만
python main.py ai-rank --limit 300 --limit-per-media 60
python main.py brief --type daily         # 일일 브리핑
python main.py brief --type weekly        # 주간 브리핑
python main.py brief --date 2026-06-16   # 특정 날짜
```

### 보조 명령

```bash
python main.py retag --days 7             # 기존 기사에 토픽 태그 추가
python main.py filter-report              # 필터 결과 리포트 → data/filter_report.md
python main.py report                     # 매체 가용성 리포트
python main.py list --limit 30
python main.py export data/articles.json
```

---

## 키워드 필터 점수 체계

| 신호 | 점수 |
|---|---|
| 금융·ESG 키워드 — **제목** 히트 | +5 |
| 금융·ESG 키워드 — **본문** 히트 | +3 |
| 국가 키워드 — 제목 히트 (국가당 1회) | +2 |
| 국가 키워드 — 본문 히트 (국가당 1회) | +1 |
| 스포츠·연예 제외 키워드 히트 | -4 |
| **통과 기준** | **≥ 3** |
| 본문 금융 키워드만 있을 때 기준 (오탐 감소) | **≥ 5** |

---

## AI 분석 설계 (llm_ranker)

- **모델**: claude-haiku-4-5-20251001
- **국가당 최대**: 200건 (기본), `--limit` 옵션으로 조정
- **매체당 최대**: 50건 (기본), `--limit-per-media` 옵션으로 조정 — 인도 4대 매체처럼 기사량 많은 소스의 독점 방지
- **JSON 파싱 실패 시**: 배치를 절반 분할 후 재시도 (최대 depth 2) → ~17% 실패율 개선
- **출력**: `ai_score`(1-5), `summary_ko`(한글 요약 1-2문장), `topics`(토픽 태그 3-5개)

---

## 대시보드 실행

```bash
streamlit run dashboard_stocks.py --server.port 8502
```

- 모바일 최적화 (max-width 500px), KB 브리핑 스타일 라이트 테마
- **대시보드**: 국가 선택 → TODAY'S TOP 3 (국가별 필터), TIMEBAR(9개 도시 현지시각), 수집 현황
- **뉴스**: 주간브리핑 / 뉴스피드 / 실시간 탭, 날짜 네비게이션, 국가 필터
- **테마뷰**: ESG / 규제 / 리스크 테마 탭, 기간·점수·건수 필터

---

## DB 관리

**로컬 → 서버 DB 이전**: git이 아닌 `scp` 사용 (WAL 모드 DB는 git으로 옮기면 손상)

```bash
# 1. 로컬에서 WAL 체크포인트
python -c "import sqlite3; c=sqlite3.connect('data/news.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"

# 2. 서버 Streamlit 중지
ssh -i ~/.ssh/glb-oracle.key ubuntu@<SERVER_IP> "pkill -f streamlit; sleep 2; fuser -k 8502/tcp 2>/dev/null; true"

# 3. 서버 WAL 잔재 제거 + DB 전송
ssh -i ~/.ssh/glb-oracle.key ubuntu@<SERVER_IP> "rm -f /home/ubuntu/gss-news-rss-proto/data/news.db-shm /home/ubuntu/gss-news-rss-proto/data/news.db-wal"
scp -i ~/.ssh/glb-oracle.key data/news.db ubuntu@<SERVER_IP>:/home/ubuntu/gss-news-rss-proto/data/

# 4. 서버 Streamlit 재시작
ssh -i ~/.ssh/glb-oracle.key ubuntu@<SERVER_IP> "cd /home/ubuntu/gss-news-rss-proto && nohup .venv/bin/streamlit run dashboard_stocks.py --server.port 8502 > /tmp/st_kb.log 2>&1 &"
```

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `database disk image is malformed` | WAL 파일 없이 DB만 이전 | 위 DB 관리 절차대로 scp 사용 |
| `Port 8502 is not available` | 기존 프로세스 미종료 | `fuser -k 8502/tcp` |
| SSLCertVerificationError | macOS 루트 CA 미설치 | `pip install certifi` 또는 `Install Certificates.command` 실행 |
| feedparser 빈 entries | User-Agent 차단 | `collector.USER_AGENT` 교체 |
| JSON 파싱 실패 (llm_ranker) | Claude 응답에 불필요한 텍스트 포함 | 자동 재시도 (배치 분할, depth≤2) |
