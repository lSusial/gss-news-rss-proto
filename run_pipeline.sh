#!/bin/bash

cd /home/ubuntu/gss-news-rss-proto
source venv/bin/activate

python main.py fetch
python main.py filter
python main.py dedup
python main.py llm-filter
python main.py ai-rank
python main.py brief --type daily
python main.py brief --type weekly