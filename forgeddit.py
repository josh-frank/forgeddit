#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["praw>=7.7"]
# ///
"""
forgeddit.py - scrub your Reddit history using your GDPR data export.

Reddit's API only lists your ~1000 newest items, but the export
(https://www.reddit.com/settings/data-request) contains the ID of everything
you've ever posted. This script reads those IDs straight from the export zip
(nothing is extracted or written to disk), overwrites each item with junk
text, then deletes it.

Usage (needs `uv`: https://docs.astral.sh/uv/ ; `brew install uv` on a Mac):

    uv run --env-file .env forgeddit.py export_you_20261004.zip --dry-run
    uv run --env-file .env forgeddit.py export_you_20261004.zip --older-than 90 --exclude-subreddits mycoolsub

Credentials (a "script" app from https://www.reddit.com/prefs/apps) come from
environment variables, so they never land in your shell history:

    export REDDIT_CLIENT_ID=...
    export REDDIT_CLIENT_SECRET=...
    export REDDIT_USERNAME=...
    export REDDIT_PASSWORD=...        # with 2FA use "password:123456"

Dry runs need no credentials and no network access.
"""

import argparse
import csv
import io
import os
import random
import string
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ---------------------------------------------------------------- reading ---

def read_csv(source: Path, name: str):
    """Yield rows from `name` inside an export zip or an unzipped folder."""
    if source.is_dir():
        path = source / name
        if not path.exists():
            return
        fh = open(path, newline="", encoding="utf-8-sig")
    else:
        zf = zipfile.ZipFile(source)
        if name not in zf.namelist():
            return
        fh = io.TextIOWrapper(zf.open(name), encoding="utf-8-sig", newline="")
    with fh:
        yield from csv.DictReader(fh)


def parse_date(s: str):
    try:
        return datetime.strptime(s.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, AttributeError):
        return None


def load_items(source: Path):
    items = []
    for row in read_csv(source, "comments.csv"):
        items.append(
            dict(kind="comment", id=row["id"], sub=row.get("subreddit", ""),
                 date=parse_date(row.get("date", "")), editable=True)
        )
    for row in read_csv(source, "posts.csv"):
        # Only text posts can be edited; link posts have an empty body.
        items.append(
            dict(kind="post", id=row["id"], sub=row.get("subreddit", ""),
                 date=parse_date(row.get("date", "")),
                 editable=bool((row.get("body") or "").strip()))
        )
    return items


def load_ids(source: Path, filename: str):
    return [r["id"] for r in read_csv(source, filename) if r.get("id")]


# -------------------------------------------------------------- filtering ---

def split_subs(values):
    return {s.strip().lower().removeprefix("r/") for v in (values or []) for s in v.split(",") if s.strip()}


def select(items, older_than, include, exclude):
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than) if older_than else None
    keep, skipped = [], 0
    for it in items:
        sub = it["sub"].lower()
        if cutoff and it["date"] and it["date"] > cutoff:
            skipped += 1
        elif include and sub not in include:
            skipped += 1
        elif sub in exclude:
            skipped += 1
        else:
            keep.append(it)
    return keep, skipped


# ----------------------------------------------------------------- reddit ---

def connect():
    import praw  # imported lazily so --dry-run works without it

    needed = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD"]
    missing = [k for k in needed if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing environment variables: {', '.join(missing)}\n(see the top of this file)")
    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        username=os.environ["REDDIT_USERNAME"],
        password=os.environ["REDDIT_PASSWORD"],
        user_agent=f"forgeddit/1.0 (by /u/{os.environ['REDDIT_USERNAME']})",
        ratelimit_seconds=600,  # sleep through rate limits instead of crashing
    )
    me = reddit.user.me()
    if me is None:
        sys.exit("Login failed. Check your credentials (and 2FA format).")
    return reddit, me.name


def junk() -> str:
    words = ("".join(random.choices(string.ascii_lowercase, k=random.randint(3, 9))) for _ in range(random.randint(8, 20)))
    return " ".join(words)


def thing(reddit, kind, id_):
    return reddit.comment(id_) if kind == "comment" else reddit.submission(id_)


def scrub(reddit, it, overwrite):
    """Returns 'deleted', 'gone' (already deleted), or raises."""
    from prawcore.exceptions import NotFound

    obj = thing(reddit, it["kind"], it["id"])
    if overwrite and it["editable"]:
        try:
            obj.edit(junk())
        except Exception as e:  # archived / already deleted / etc. Still try to delete.
            print(f"    (couldn't overwrite: {type(e).__name__})", end="")
    try:
        obj.delete()
    except NotFound:
        return "gone"
    return "deleted"


# ------------------------------------------------------------------- main ---

def main():
    p = argparse.ArgumentParser(description="Scrub Reddit history using your GDPR export.")
    p.add_argument("export", type=Path, help="export zip (or unzipped folder) from Reddit")
    p.add_argument("--dry-run", action="store_true", help="show what would happen; change nothing")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--older-than", type=int, metavar="DAYS", help="only touch items older than this")
    p.add_argument("--include-subreddits", action="append", metavar="A,B", help="only these subreddits")
    p.add_argument("--exclude-subreddits", action="append", metavar="A,B", help="never touch these subreddits")
    p.add_argument("--no-overwrite", action="store_true", help="delete without overwriting text first")
    p.add_argument("--votes", action="store_true", help="also clear your up/downvotes")
    p.add_argument("--saved", action="store_true", help="also unsave saved posts/comments")
    p.add_argument("--delay", type=float, default=0.0, help="extra seconds to wait between actions")
    args = p.parse_args()

    if not args.export.exists():
        sys.exit(f"Not found: {args.export}")

    items = load_items(args.export)
    if not items:
        sys.exit("No comments.csv / posts.csv found. Is this a Reddit data export?")
    todo, skipped = select(items, args.older_than, split_subs(args.include_subreddits),
                           split_subs(args.exclude_subreddits))

    extras = []  # (label, action_name, kind, id)
    if args.votes:
        extras += [("vote", "comment", i) for i in load_ids(args.export, "comment_votes.csv")]
        extras += [("vote", "post", i) for i in load_ids(args.export, "post_votes.csv")]
    if args.saved:
        extras += [("saved", "comment", i) for i in load_ids(args.export, "saved_comments.csv")]
        extras += [("saved", "post", i) for i in load_ids(args.export, "saved_posts.csv")]

    n_c = sum(i["kind"] == "comment" for i in todo)
    n_p = len(todo) - n_c
    print(f"Export: {args.export.name}")
    print(f"  to scrub: {n_c} comments, {n_p} posts   (skipped by filters: {skipped})")
    if extras:
        print(f"  extras:   {len(extras)} votes/saved items to clear")

    if args.dry_run:
        print("\nDRY RUN - nothing will be changed. Items that would be scrubbed:\n")
        for it in sorted(todo, key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc)):
            d = it["date"].strftime("%Y-%m-%d") if it["date"] else "unknown"
            action = "overwrite+delete" if (not args.no_overwrite and it["editable"]) else "delete"
            print(f"  {d}  {it['kind']:<7} {it['id']:<8} r/{it['sub']:<25} {action}")
        print(f"\nDry run complete: {len(todo)} items (+{len(extras)} extras). Re-run without --dry-run to apply.")
        return

    if not todo and not extras:
        sys.exit("Nothing to do.")

    reddit, user = connect()
    print(f"\nLogged in as u/{user}")
    if not args.yes:
        if input(f"This PERMANENTLY deletes {len(todo)} items. Type DELETE to continue: ") != "DELETE":
            sys.exit("Aborted.")

    done = gone = failed = 0
    try:
        for n, it in enumerate(todo, 1):
            print(f"[{n}/{len(todo)}] {it['kind']} {it['id']} r/{it['sub']}", end="", flush=True)
            try:
                result = scrub(reddit, it, not args.no_overwrite)
                done += result == "deleted"
                gone += result == "gone"
                print(f" -> {result}")
            except Exception as e:
                failed += 1
                print(f" -> FAILED ({type(e).__name__}: {e})")
            time.sleep(args.delay)

        for n, (what, kind, id_) in enumerate(extras, 1):
            print(f"[extra {n}/{len(extras)}] {what} {kind} {id_}", end="", flush=True)
            try:
                obj = thing(reddit, kind, id_)
                obj.clear_vote() if what == "vote" else obj.unsave()
                print(" -> cleared")
            except Exception as e:
                print(f" -> skipped ({type(e).__name__})")
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\nInterrupted. Safe to re-run: finished items just report 'gone'.")

    print(f"\nDeleted: {done}   Already gone: {gone}   Failed: {failed}")


if __name__ == "__main__":
    main()
