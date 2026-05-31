# Run FYP Scraper on AWS EC2

Guide for **Ubuntu EC2** (t3.small or larger recommended — Playwright needs ~2 GB RAM).

---

## 1. Launch EC2

- **AMI:** Ubuntu 22.04 or 24.04 LTS
- **Instance:** `t3.small` (2 GB RAM) minimum; `t3.medium` for all spiders
- **Storage:** 20 GB+
- **Security group:** Outbound HTTPS (443) allowed (MongoDB Atlas + websites)
- **MongoDB Atlas:** add EC2 **public IP** under Network Access

---

## 2. Connect and clone

```bash
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>

git clone <your-repo-url> ~/FYP_Srcaper
cd ~/FYP_Srcaper
```

---

## 3. One-time setup

```bash
chmod +x scripts/ec2_setup.sh
./scripts/ec2_setup.sh
```

Edit MongoDB credentials:

```bash
nano .env
```

```env
MONGODB_USERNAME=your_user
MONGODB_PASSWORD=your_pass
```

Or:

```env
MONGODB_URI=mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true&w=majority
```

---

## 4. Run UrduPoint search spider (Playwright)

```bash
cd ~/FYP_Srcaper
source venv/bin/activate
./scripts/run_urdupoint_search.sh
```

Options:

```bash
QUERY=زیادتی MAX_PAGES=5 ./scripts/run_urdupoint_search.sh
```

Or directly:

```bash
cd FYP_Scraper
scrapy crawl urdupoint_search -a query=زیادتی -a max_pages=5 -s PLAYWRIGHT_HEADLESS=true
```

---

## 5. Run all scrapers

```bash
./scripts/run_all_scrapers_ec2.sh
```

---

## 6. Daily cron (optional)

```bash
crontab -e
```

Add (runs daily at 2:00 AM UTC):

```cron
0 2 * * * /home/ubuntu/FYP_Srcaper/scripts/run_urdupoint_search.sh >> /home/ubuntu/urdupoint_search.log 2>&1
```

Or all scrapers:

```cron
0 2 * * * /home/ubuntu/FYP_Srcaper/scripts/run_all_scrapers_ec2.sh >> /home/ubuntu/scrapers.log 2>&1
```

---

## 7. Other useful commands

**Last scrape dates:**

```bash
source venv/bin/activate
python3 last_scrape_dates.py
```

**Delete old articles (dry-run first):**

```bash
python3 delete_old_articles.py
python3 delete_old_articles.py --execute
```

---

## Troubleshooting

| Issue | Fix |
|--------|-----|
| `playwright: command not found` | `source venv/bin/activate` then `playwright install chromium` |
| Browser crashes / OOM | Use `t3.medium` or add swap: `sudo fallocate -l 2G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile` |
| MongoDB connection error | Whitelist EC2 IP in Atlas; check `.env` |
| No articles scraped | Check log for Cloudflare; ensure `PLAYWRIGHT_HEADLESS=true` |
| Slow run | Normal for first run; search uses one browser pass (~1–2 min) + fast article fetch |

---

## Quick reference

```bash
cd ~/FYP_Srcaper && source venv/bin/activate
./scripts/run_urdupoint_search.sh          # search only
./scripts/run_all_scrapers_ec2.sh         # all spiders in run_all_scrapers.py
python3 last_scrape_dates.py             # status report
```
