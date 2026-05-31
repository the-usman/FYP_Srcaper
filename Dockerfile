FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

COPY FYP_Scraper ./FYP_Scraper
COPY run_all_scrapers.py last_scrape_dates.py ./
COPY Free_Proxy_List.csv ./FYP_Scraper/Free_Proxy_List.csv

ENV PYTHONUNBUFFERED=1

# Keep container running; Dokploy Schedule runs scrapers via docker exec
CMD ["tail", "-f", "/dev/null"]
