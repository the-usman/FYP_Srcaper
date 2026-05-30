#!/usr/bin/env python3
"""
Delete MongoDB articles ON OR AFTER a cutoff date per source.

KEEP articles BEFORE the cutoff date.
DELETE articles on the cutoff day and any date after it.

Default cutoffs (from your last_scrape_dates report — latest article date):
  24_news         -> 2025-10-01
  city42          -> 2025-09-29
  daily_pakistan  -> 2025-09-26
  dunya_news      -> 2025-10-01
  nawaiwaqt       -> 2025-10-02
  urdupoint       -> 2025-08-23

Usage:
  python delete_old_articles.py              # dry-run (shows counts only)
  python delete_old_articles.py --execute    # actually delete
  python delete_old_articles.py --source urdupoint --cutoff 2025-08-23 --execute
"""

from __future__ import annotations

import argparse
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
from dateutil import parser as date_parser

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

NEWS_DB = "news_db"
RAW_SUFFIX = "_raw"
BATCH_SIZE = 500

# Latest article date per source (your report)
DEFAULT_CUTOFFS: dict[str, str] = {
    "24_news": "2025-10-01",
    # "city42": "2025-09-29",
    "daily_pakistan": "2025-09-26",
    "dunya_news": "2025-10-01",
    # "nawaiwaqt": "2025-10-02",
    "urdupoint": "2025-08-23",
}

DEFAULT_CLUSTER = (
    "mongodb+srv://{user}:{pwd}@cluster0.66sawpl.mongodb.net/"
    "?retryWrites=true&w=majority&appName=Cluster0"
)


def load_env() -> None:
    if load_dotenv is None:
        return
    root = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(root, ".env"), os.path.join(root, "FYP_Scraper", ".env")):
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
        print("Error: set MONGODB_URI or MONGODB_USERNAME + MONGODB_PASSWORD", file=sys.stderr)
        sys.exit(1)
    return DEFAULT_CLUSTER.format(user=quote_plus(username), pwd=quote_plus(password))


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


def parse_cutoff(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y-%m-%d")


def process_collection(
    coll,
    source: str,
    cutoff: datetime,
    *,
    execute: bool,
    delete_unparseable: bool,
) -> tuple[int, int, int]:
    """Returns (scanned, would_delete, deleted)."""
    to_delete_ids: list[Any] = []
    scanned = 0
    unparseable = 0

    for doc in coll.find({}, {"_id": 1, "date": 1}):
        scanned += 1
        parsed = parse_article_date(doc.get("date"))
        if parsed is None:
            unparseable += 1
            if delete_unparseable:
                to_delete_ids.append(doc["_id"])
            continue
        if parsed >= cutoff:
            to_delete_ids.append(doc["_id"])

    would_delete = len(to_delete_ids)
    deleted = 0
    if execute and to_delete_ids:
        for i in range(0, len(to_delete_ids), BATCH_SIZE):
            batch = to_delete_ids[i : i + BATCH_SIZE]
            deleted += coll.delete_many({"_id": {"$in": batch}}).deleted_count

    print(
        f"  {source}: scanned={scanned}, to_delete={would_delete}, "
        f"unparseable={unparseable}, keep date<{cutoff.date()}"
    )
    if execute:
        print(f"    -> deleted {deleted}")
    return scanned, would_delete, deleted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete articles on/after cutoff; keep articles before cutoff."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete (default is dry-run only)",
    )
    parser.add_argument("--source", help="Only this source (e.g. urdupoint)")
    parser.add_argument("--cutoff", help="Override cutoff YYYY-MM-DD for --source")
    parser.add_argument(
        "--delete-unparseable",
        action="store_true",
        help="Also delete docs with missing/unparseable date field",
    )
    args = parser.parse_args()

    cutoffs = dict(DEFAULT_CUTOFFS)
    if args.source:
        if args.cutoff:
            cutoffs = {args.source: args.cutoff}
        elif args.source not in cutoffs:
            print(f"Error: unknown source {args.source}. Known: {list(DEFAULT_CUTOFFS)}", file=sys.stderr)
            sys.exit(1)
        else:
            cutoffs = {args.source: cutoffs[args.source]}

    if not args.execute:
        print("DRY RUN — no data deleted. Pass --execute to delete.\n")

    uri = get_mongo_uri()
    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=20000)
    client.admin.command("ping")
    db = client[NEWS_DB]

    total_delete = 0
    for source, cutoff_str in sorted(cutoffs.items()):
        cutoff = parse_cutoff(cutoff_str)
        coll_name = f"{source}{RAW_SUFFIX}"
        if coll_name not in db.list_collection_names():
            print(f"  {source}: collection {coll_name} not found, skip")
            continue
        print(f"\n{source} ({coll_name}) — delete articles on/after {cutoff.date()}, keep older:")
        _, n, _ = process_collection(
            db[coll_name],
            source,
            cutoff,
            execute=args.execute,
            delete_unparseable=args.delete_unparseable,
        )
        total_delete += n

    print(f"\n{'Would delete' if not args.execute else 'Deleted'}: {total_delete} documents total.")
    if not args.execute and total_delete:
        print("Run again with:  python delete_old_articles.py --execute")
    client.close()


if __name__ == "__main__":
    main()
