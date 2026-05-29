-- glb-news-rss 프로토타입 스키마 (SQLite)
-- 실행계획_v3.md §5의 PostgreSQL 스키마를 Python 프로토타입용으로 단순화.
-- Java/Postgres 본 시스템으로 이관할 때 필드명이 그대로 매핑되도록 유지.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- 매체 본체 (v3 §5.1 Media_Sources에 대응)
CREATE TABLE IF NOT EXISTS media_sources (
    source_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    media_name           TEXT    NOT NULL UNIQUE,
    primary_country_code TEXT    NOT NULL,      -- KR, US, CN, JP, ID, VN, KH, MM, IN, GLOBAL
    language             TEXT    NOT NULL,      -- ko, en, zh, ja, id, vi, km, my
    tier                 INTEGER NOT NULL,      -- 1 또는 2
    is_active            INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 매체-카테고리 매핑 (v3 §5.1 Media_Category_Map, N:M)
-- 예: NYT는 GLOBAL_GENERAL과 US에 동시 매핑
CREATE TABLE IF NOT EXISTS media_category_map (
    source_id     INTEGER NOT NULL,
    category_code TEXT    NOT NULL,   -- GLOBAL_GENERAL, GLOBAL_ECONOMY, KR, US, ...
    PRIMARY KEY (source_id, category_code),
    FOREIGN KEY (source_id) REFERENCES media_sources(source_id) ON DELETE CASCADE
);

-- 매체별 RSS 피드 (v3 §5.1 Media_Source_Feeds)
-- 한 매체가 World/Business/Economy 등 섹션별로 여러 피드를 가짐
CREATE TABLE IF NOT EXISTS media_source_feeds (
    feed_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL,
    feed_url     TEXT    NOT NULL UNIQUE,
    feed_section TEXT    NOT NULL,    -- world, business, economy, top, ...
    is_active    INTEGER NOT NULL DEFAULT 1,
    last_status  INTEGER,             -- 마지막 HTTP 응답 코드 (또는 -1: 네트워크 에러)
    last_fetched TEXT,
    last_error   TEXT,
    FOREIGN KEY (source_id) REFERENCES media_sources(source_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_feeds_source ON media_source_feeds(source_id);

-- 수집된 기사 (v3 §5.2 News_Articles_Raw 단순화 버전)
CREATE TABLE IF NOT EXISTS articles_raw (
    article_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id         INTEGER NOT NULL,
    source_id       INTEGER NOT NULL,
    title           TEXT    NOT NULL,
    link            TEXT    NOT NULL,
    summary         TEXT,
    published_at    TEXT,                       -- ISO 8601, NULL 가능
    fetched_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    content_hash    TEXT    NOT NULL,           -- sha256(title + link) — 중복 제거 키
    -- 2단계 필터 컬럼 (v3 §3.2)
    filter_stage    INTEGER NOT NULL DEFAULT 0, -- 0=미처리 | 1=국가매체자동통과 | 2=키워드필터 | 3=LLM
    filter_decision TEXT    NOT NULL DEFAULT 'pending', -- pending | passed | rejected
    filter_reason   TEXT,                       -- 통과·거부 근거 (키워드명 등)
    FOREIGN KEY (feed_id)   REFERENCES media_source_feeds(feed_id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES media_sources(source_id) ON DELETE CASCADE,
    UNIQUE (content_hash)
);

CREATE INDEX IF NOT EXISTS idx_articles_source     ON articles_raw(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_feed       ON articles_raw(feed_id);
CREATE INDEX IF NOT EXISTS idx_articles_published  ON articles_raw(published_at DESC);

-- 수집 실행 이력 (v3에는 없지만 프로토타입 검증에 유용)
CREATE TABLE IF NOT EXISTS fetch_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at  TEXT,
    feeds_total  INTEGER NOT NULL DEFAULT 0,
    feeds_ok     INTEGER NOT NULL DEFAULT 0,
    feeds_failed INTEGER NOT NULL DEFAULT 0,
    new_articles INTEGER NOT NULL DEFAULT 0,
    dup_articles INTEGER NOT NULL DEFAULT 0
);
