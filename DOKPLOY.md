# Deploy FYP Scraper on Dokploy

This project is a **batch scraper** (not a web app). On Dokploy you deploy a long-running container and use **Schedules** to run `run_all_scrapers.py` daily.

## Prerequisites

- Dokploy server installed ([docs.dokploy.com](https://docs.dokploy.com))
- Git repo pushed (GitHub/GitLab/etc.)
- MongoDB Atlas URI or username/password

---

## Option A — Application (recommended)

### 1. Create project

1. Open Dokploy → **Projects** → **Create project** (e.g. `fyp-scraper`).

### 2. Create application

1. **Add Service** → **Application**.
2. **Source**: connect your Git provider and select this repository.
3. **Branch**: `main` (or your deploy branch).
4. **Build Path**: `/` (repository root).

### 3. Build settings

| Setting | Value |
|--------|--------|
| Build type | **Dockerfile** |
| Dockerfile path | `Dockerfile` |
| Docker context | `.` |

Click **Deploy** once to build the image.

### 4. Environment variables

In the application → **Environment**, add:

```env
MONGODB_URI=mongodb+srv://USER:PASS@cluster....mongodb.net/?retryWrites=true&w=majority
```

Or:

```env
MONGODB_USERNAME=your_user
MONGODB_PASSWORD=your_pass
```

Save and **Redeploy** if the app was already running.

### 5. Schedule — run all scrapers (same as cron-jobs.yaml)

1. Open the application → **Schedules** → **Add Schedule**.
2. Configure:

| Field | Value |
|--------|--------|
| Name | `Daily news scrape` |
| Cron | `0 0 * * *` (midnight UTC daily) |
| Command | `python3 /app/run_all_scrapers.py` |
| Shell | `bash` |
| Enabled | Yes |

3. Save. Dokploy runs this **inside the running container** (`docker exec`).

Optional second schedule for status report:

| Field | Value |
|--------|--------|
| Name | `Scrape dates report` |
| Cron | `30 0 * * *` |
| Command | `python3 /app/last_scrape_dates.py` |

### 6. Manual run (test)

**Deployments** → open container logs, or add a one-off schedule / use **Terminal** in Dokploy:

```bash
python3 /app/run_all_scrapers.py
python3 /app/last_scrape_dates.py
```

---

## Option B — Docker Compose

1. **Add Service** → **Compose**.
2. Paste or link `docker-compose.yml` from this repo.
3. Set the same env vars in the Compose service UI.
4. Deploy.
5. Add **Compose Schedule** with command:

   `python3 /app/run_all_scrapers.py`

   (target the `fyp-scraper` service).

---

## Important notes

1. **Container must stay running** — the Dockerfile uses `tail -f /dev/null` so scheduled jobs can exec into it.
2. **Do not commit `.env`** — use Dokploy Environment only.
3. **MongoDB Atlas** — allow network access from your Dokploy server IP (Atlas → Network Access).
4. **Proxies** — `Free_Proxy_List.csv` is copied into the image at `FYP_Scraper/Free_Proxy_List.csv` for middleware.
5. **Resources** — scrapers are heavy; set **2 GB+ RAM** and **1+ CPU** in application resources if crawls fail or OOM.

---

## Auto deploy on git push

Application → **Webhooks** → copy URL → add as GitHub/GitLab webhook on `push` to your branch.

---

## Troubleshooting

| Problem | Fix |
|--------|-----|
| Schedule does nothing | Check container is **Running**; check schedule logs in Dokploy |
| MongoDB connection error | Verify `MONGODB_URI` in Environment; whitelist Dokploy server IP in Atlas |
| `scrapy: not found` | Rebuild image; ensure `requirements.txt` install succeeded in build logs |
| Spider fails one source | Normal — script continues; check logs for that spider name |
