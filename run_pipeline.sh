#!/bin/bash

cd /home/ubuntu/gss-news-rss-proto
source venv/bin/activate

# KST 기준 날짜 계산 (서버가 UTC이므로 +9시간 보정)
YESTERDAY=$(TZ=Asia/Seoul date -d "yesterday" +%Y-%m-%d)
LAST_MONDAY=$(TZ=Asia/Seoul date -d "last monday" +%Y-%m-%d)

python main.py fetch
python main.py filter
python main.py dedup
python main.py llm-filter
python main.py ai-rank
python main.py brief --type daily --date "$YESTERDAY"
python main.py brief --type weekly --date "$LAST_MONDAY"
