#!/usr/bin/env python3
"""
Report the latest scrape activity per news source in MongoDB.

Uses MONGODB_URI if set, otherwise MONGODB_USERNAME + MONGODB_PASSWORD
(same as pipelines.py). Collections are named {source}_raw in news_db.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import dns.resolver
import pymongo
from bson import ObjectId
from dateutil import parser as date_parser
from dotenv import load_dotenv
from tabulate import tabulate

dns.resolver.default_resolver = dns.resolver.Resolver()
dns.resolver.default_resolver.nameservers = ["8.8.8.8"]

NEWS_DB = "news_db"
WEATHER_DB = "weather_db"
RAW_SUFFIX = "_raw"


def get_mongo_uri() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    load_dotenv(os.path.join(repo_root, ".env"))
    load_dotenv(os.path.join(script_dir, ".env"))
    uri = os.getenv("MONGODB_URI", "").strip()
    if uri:
        return uri
    username = os.getenv("MONGODB_USERNAME", "").strip()
    password = os.getenv("MONGODB_PASSWORD", "").strip()
    if not username or not password:
        print(
            "Error: set MONGODB_URI or both MONGODB_USERNAME and MONGODB_PASSWORD in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    user = quote_plus(username)
    pwd = quote_plus(password)
    return (
        f"mongodb+srv://{user}:{pwd}@cluster0.66sawpl.mongodb.net/"
        "?retryWrites=true&w=majority&appName=Cluster0"
    )


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
    # YYYY-MM-DD (dunya_news search_date style)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            pass
    try:
        return date_parser.parse(text, dayfirst=True, fuzzy=True).replace(tzinfo=None)
    except (ValueError, TypeError, OverflowError):
        return None


def source_from_collection(name: str) -> str | None:
    if name.endswith(RAW_SUFFIX):
        return name[: -len(RAW_SUFFIX)]
    return None


def analyze_news_db(db) -> list[dict]:
    rows: list[dict] = []
    collection_names = sorted(db.list_collection_names())
    raw_collections = [n for n in collection_names if n.endswith(RAW_SUFFIX)]

    for coll_name in raw_collections:
        source = source_from_collection(coll_name) or coll_name
        coll = db[coll_name]
        count = coll.estimated_document_count()

        newest_id_doc = coll.find_one(sort=[("_id", pymongo.DESCENDING)], projection={"_id": 1})
        last_inserted = object_id_to_datetime(
            newest_id_doc["_id"] if newest_id_doc else None
        )

        # Latest article date among recently inserted docs (mixed formats in DB)
        latest_article_date: datetime | None = None
        latest_article_date_raw: str | None = None
        recent = coll.find(
            {"date": {"$exists": True, "$nin": [None, "", "N/A"]}},
            {"date": 1},
            sort=[("_id", pymongo.DESCENDING)],
            limit=2000,
        )
        for doc in recent:
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
                "last_scrape_utc": last_inserted,
                "latest_article_date": latest_article_date,
                "latest_article_date_raw": latest_article_date_raw,
            }
        )

    return rows


def analyze_weather_db(db) -> list[dict]:
    rows: list[dict] = []
    if "weather_data" not in db.list_collection_names():
        return rows

    coll = db["weather_data"]
    count = coll.estimated_document_count()
    newest = coll.find_one(sort=[("_id", pymongo.DESCENDING)])
    last_inserted = object_id_to_datetime(newest["_id"] if newest else None)

    latest_scraped_at: datetime | None = None
    for doc in coll.find({"scraped_at": {"$exists": True, "$ne": None}}, {"scraped_at": 1}):
        parsed = parse_article_date(doc.get("scraped_at"))
        if parsed and (latest_scraped_at is None or parsed > latest_scraped_at):
            latest_scraped_at = parsed

    rows.append(
        {
            "source": "weather",
            "collection": "weather_data",
            "documents": count,
            "last_scrape_utc": latest_scraped_at or last_inserted,
            "latest_article_date": None,
            "latest_article_date_raw": None,
        }
    )
    return rows


def fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def print_report(news_rows: list[dict], weather_rows: list[dict]) -> None:
    all_rows = news_rows + weather_rows
    if not all_rows:
        print("No *_raw collections found in news_db and no weather_data in weather_db.")
        return

    table = []
    for r in sorted(all_rows, key=lambda x: x["source"]):
        table.append(
            [
                r["source"],
                r["documents"],
                fmt_dt(r["last_scrape_utc"]),
                fmt_dt(r["latest_article_date"]),
                r["latest_article_date_raw"] or "—",
            ]
        )

    print("\n=== Last scrape / latest dates per source ===\n")
    print(
        tabulate(
            table,
            headers=[
                "Source",
                "Docs",
                "Last scrape (UTC)*",
                "Latest article date",
                "Article date (raw)",
            ],
            tablefmt="simple",
        )
    )
    print(
        "\n* Last scrape = timestamp of the newest MongoDB document (_id).\n"
        "  New URLs get a new _id; re-scraped existing URLs update in place.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Show last scrape dates per MongoDB source.")
    parser.add_argument(
        "--weather",
        action="store_true",
        help="Also include weather_db.weather_data",
    )
    args = parser.parse_args()

    uri = get_mongo_uri()
    try:
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=15000)
        client.admin.command("ping")
    except pymongo.errors.ConfigurationError as exc:
        print(f"MongoDB connection failed: {exc}", file=sys.stderr)
        print(
            "Tip: copy your full connection string from MongoDB Atlas into .env as MONGODB_URI.",
            file=sys.stderr,
        )
        sys.exit(1)

    news_db = client[NEWS_DB]
    news_rows = analyze_news_db(news_db)

    weather_rows: list[dict] = []
    if args.weather:
        weather_rows = analyze_weather_db(client[WEATHER_DB])

    print_report(news_rows, weather_rows)
    client.close()


if __name__ == "__main__":
    main()
