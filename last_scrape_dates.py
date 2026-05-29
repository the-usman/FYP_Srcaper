#!/usr/bin/env python3
"""
Standalone script for EC2 (or any server): show last scrape date per news source.

Setup on EC2:
  pip install pymongo python-dotenv python-dateutil dnspython tabulate

  export MONGODB_URI='mongodb+srv://USER:PASS@cluster....mongodb.net/...'
  # or:
  export MONGODB_USERNAME='your_user'
  export MONGODB_PASSWORD='your_pass'

  python3 last_scrape_dates.py
  python3 last_scrape_dates.py --json > report.json
  python3 last_scrape_dates.py --csv scrape_dates.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

try:
    import dns.resolver

    dns.resolver.default_resolver = dns.resolver.Resolver()
    dns.resolver.default_resolver.nameservers = ["8.8.8.8", "8.8.4.4"]
except ImportError:
    pass

import pymongo
from bson import ObjectId
from dateutil import parser as date_parser

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore

try:
    from tabulate import tabulate
except ImportError:
    tabulate = None

NEWS_DB = "news_db"
WEATHER_DB = "weather_db"
RAW_SUFFIX = "_raw"
DEFAULT_CLUSTER = (
    "mongodb+srv://{user}:{pwd}@cluster0.66sawpl.mongodb.net/"
    "?retryWrites=true&w=majority&appName=Cluster0"
)


def load_env() -> None:
    if load_dotenv is None:
        return
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.expanduser("~/.env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "FYP_Scraper", ".env"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            load_dotenv(path)


def get_mongo_uri() -> str:
    load_env()
    uri = os.environ.get("MONGODB_URI", "").strip()
    if uri:
        return uri
    username = os.environ.get("MONGODB_USERNAME", "").strip()
    password = os.environ.get("MONGODB_PASSWORD", "").strip()
    if not username or not password:
        print(
            "Error: set MONGODB_URI or both MONGODB_USERNAME and MONGODB_PASSWORD.\n"
            "  export MONGODB_URI='mongodb+srv://...'",
            file=sys.stderr,
        )
        sys.exit(1)
    user = quote_plus(username)
    pwd = quote_plus(password)
    return DEFAULT_CLUSTER.format(user=user, pwd=pwd)


def object_id_to_datetime(oid: ObjectId | None) -> datetime | None:
    if oid is None:
        return None
    return oid.generation_time.replace(tzinfo=None)


def parse_article_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text or text.upper() == "N/A":
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            pass
    try:
        return date_parser.parse(text, dayfirst=True, fuzzy=True).replace(tzinfo=None)
    except (ValueError, TypeError, OverflowError):
        return None


def dt_iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def analyze_news_db(db) -> list[dict]:
    rows: list[dict] = []
    raw_collections = [n for n in sorted(db.list_collection_names()) if n.endswith(RAW_SUFFIX)]

    for coll_name in raw_collections:
        source = coll_name[: -len(RAW_SUFFIX)]
        coll = db[coll_name]
        count = coll.estimated_document_count()

        newest = coll.find_one(sort=[("_id", pymongo.DESCENDING)], projection={"_id": 1})
        last_scrape = object_id_to_datetime(newest["_id"] if newest else None)

        latest_article_date: datetime | None = None
        latest_article_date_raw: str | None = None
        for doc in coll.find(
            {"date": {"$exists": True, "$nin": [None, "", "N/A"]}},
            {"date": 1},
            sort=[("_id", pymongo.DESCENDING)],
            limit=2000,
        ):
            parsed = parse_article_date(doc.get("date"))
            if parsed is None:
                continue
            if latest_article_date is None or parsed > latest_article_date:
                latest_article_date = parsed
                latest_article_date_raw = str(doc.get("date"))

        rows.append(
            {
                "source": source,
                "collection": coll_name,
                "documents": count,
                "last_scrape_utc": dt_iso(last_scrape),
                "latest_article_date": dt_iso(latest_article_date),
                "latest_article_date_raw": latest_article_date_raw,
            }
        )
    return rows


def analyze_weather_db(db) -> list[dict]:
    if "weather_data" not in db.list_collection_names():
        return []
    coll = db["weather_data"]
    newest = coll.find_one(sort=[("_id", pymongo.DESCENDING)])
    last = object_id_to_datetime(newest["_id"] if newest else None)
    return [
        {
            "source": "weather",
            "collection": "weather_data",
            "documents": coll.estimated_document_count(),
            "last_scrape_utc": dt_iso(last),
            "latest_article_date": None,
            "latest_article_date_raw": None,
        }
    ]


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("No *_raw collections in news_db.")
        return

    headers = [
        "Source",
        "Docs",
        "Last scrape (UTC)",
        "Latest article date",
        "Article date (raw)",
    ]
    data = [
        [
            r["source"],
            r["documents"],
            r["last_scrape_utc"] or "—",
            r["latest_article_date"] or "—",
            r["latest_article_date_raw"] or "—",
        ]
        for r in sorted(rows, key=lambda x: x["source"])
    ]

    print("\n=== Last scrape dates per source ===\n")
    if tabulate:
        print(tabulate(data, headers=headers, tablefmt="simple"))
    else:
        print(" | ".join(headers))
        print("-" * 80)
        for row in data:
            print(" | ".join(str(c) for c in row))
    print()


def write_csv(path: str, rows: list[dict]) -> None:
    fieldnames = [
        "source",
        "collection",
        "documents",
        "last_scrape_utc",
        "latest_article_date",
        "latest_article_date_raw",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda x: x["source"]))
    print(f"Wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report last MongoDB scrape date per news source (for EC2/local)."
    )
    parser.add_argument("--weather", action="store_true", help="Include weather_db")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    parser.add_argument("--csv", metavar="FILE", help="Write results to CSV file")
    args = parser.parse_args()

    uri = get_mongo_uri()
    try:
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=20000)
        client.admin.command("ping")
    except Exception as exc:
        print(f"MongoDB connection failed: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = analyze_news_db(client[NEWS_DB])
    if args.weather:
        rows.extend(analyze_weather_db(client[WEATHER_DB]))
    client.close()

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    elif args.csv:
        write_csv(args.csv, rows)
        if not args.json:
            print_table(rows)
    else:
        print_table(rows)


if __name__ == "__main__":
    main()
