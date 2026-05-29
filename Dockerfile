FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY FYP_Scraper ./FYP_Scraper
COPY run_all_scrapers.py last_scrape_dates.py ./
COPY Free_Proxy_List.csv ./FYP_Scraper/Free_Proxy_List.csv

ENV PYTHONUNBUFFERED=1

# Keep container running; Dokploy Schedule runs scrapers via docker exec
CMD ["tail", "-f", "/dev/null"]
