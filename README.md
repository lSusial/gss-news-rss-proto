# glb-news-rss 프로토타입 (Python)

실행계획_v3.md Phase 1 "수집 MVP"의 Python 검증판입니다.
74개 매체의 RSS 가용성과 일일 수집량을 빠르게 확인하기 위한 도구입니다.
검증이 끝나면 Java/Spring Boot + PostgreSQL+pgvector로 이관합니다.

## 폴더 구조

```
prototype/
├─ schema.sql        SQLite 스키마 (v3 §5 스키마 명명 규칙 유지)
├─ sources.yaml      74개 매체 RSS 카탈로그
├─ collector.py      RSS 수집 모듈 (feedparser, 병렬)
├─ main.py           CLI 진입점
├─ requirements.txt
└─ data/             # 런타임 생성 (news.db, availability_report.md)
```

## 의존성 설치

```bash
cd prototype
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 사용법

```bash
python main.py init      # DB 초기화 + sources.yaml 동기화 (멱등)
python main.py fetch     # 전체 활성 피드 1회 수집 (병렬 8 워커)
python main.py report    # data/availability_report.md 생성
python main.py list --limit 30
python main.py export data/articles.json
```

권장 첫 실행 순서: `init → fetch → report`. fetch 1회로 약 100개 피드를 병렬로 가져오므로 30초 ~ 1분 정도 걸립니다. 끝나면 `data/availability_report.md`에 매체별 HTTP 응답·기사 수·마지막 게시 시각·국가별 요약이 나옵니다.

## 가용성 리포트 보는 법

- `last_status = 200` + `article_count > 0` → 사용 가능
- `last_status = -1` → 네트워크/파싱 에러 (사이트가 RSS 자체를 막은 경우 많음)
- `last_status = 403/404` → 피드 폐쇄. 다른 RSS URL이나 Google News 우회로 교체 필요
- 한국 매체는 일부가 User-Agent 차단을 걸어둘 수 있음 — 403이면 `collector.py`의 `USER_AGENT`를 일반 브라우저 UA로 바꿔서 재시도

## 트러블슈팅

- **`SSLCertVerificationError` 가 대부분** — macOS 시스템 Python이 루트 CA를 못 찾아서 발생. 이 프로젝트는 `requirements.txt`에 `certifi`를 추가하고 collector가 자동으로 certifi 번들을 사용하므로, 다음만 하면 됩니다:
  ```bash
  source .venv/bin/activate
  pip install -r requirements.txt
  ```
  그래도 안 된다면 python.org 인스톨러 Python을 쓰는 경우인데, `Applications/Python\ 3.x/Install\ Certificates.command`를 한 번 실행하면 시스템 레벨로도 해결됩니다.
- **`sqlite3.OperationalError: disk I/O error`** — DB 경로가 SMB/네트워크 드라이브일 때 WAL 모드 충돌. `schema.sql`의 `PRAGMA journal_mode = WAL;`을 `DELETE`로 바꾸세요.
- **`feedparser`가 빈 entries 반환** — 사이트가 RSS는 살아있는데 User-Agent로 차단. `collector.USER_AGENT`를 다른 브라우저 UA로 교체.
- **403 일괄 발생** — 회사망/VPN/사내 프록시가 외부 피드를 막은 경우. 개인 회선에서 시도.

## v3 실행계획과의 매핑

| v3 항목 | 프로토타입 대응 |
|---|---|
| §5.1 Media_Sources | `media_sources` 테이블 (동일 컬럼) |
| §5.1 Media_Category_Map (N:M) | `media_category_map` |
| §5.1 Media_Source_Feeds | `media_source_feeds` |
| §5.2 News_Articles_Raw | `articles_raw` (filter 컬럼은 다음 단계) |
| §11.6 캄보디아·미얀마 RSS 가용성 점검 | `report` 명령으로 자동화 |

## 다음 단계 (이 프로토타입에서 검증 후)

1. **1·2단계 필터** — 섹션·키워드 필터를 `articles_raw.filter_decision`에 기록
2. **번역·요약** — 영문 외 기사 한국어 처리 (gpt-4o-mini 배치)
3. **본 시스템 이관** — Java/Spring Boot, PostgreSQL+pgvector로 동일 스키마 재현
