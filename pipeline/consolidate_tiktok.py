#!/usr/bin/env python3
"""
Consolidate TikTok Studio .xlsx exports into three tidy tables.

What TikTok actually gives you
------------------------------
Studio exports .xlsx (not .csv), one file per tab, and the Content tab exports
only the page of "top posts" currently on screen. Paging through and exporting
each page produces many overlapping files — here, 16 files holding 240 rows that
describe 58 distinct videos.

Two problems have to be solved before any of it is usable:

1. NO YEAR ON ANY DATE. Every date reads "September 9" or "March 30". A naive
   parse puts a year of TikTok history into a single ambiguous year and silently
   destroys the ordering. Handled by walking each series in order and rolling the
   year over at the December -> January boundary, anchored so the series ends on
   or before the export date.

2. DUPLICATE VIDEO ROWS THAT DISAGREE. The same video appears in up to 16 files,
   and a few disagree because they were exported minutes apart while counts were
   still ticking up. Resolved by taking the MAXIMUM per metric: these are
   monotonic lifetime counters, so the highest reading is the most recent one.
   Every disagreement is reported rather than quietly resolved.

Outputs
-------
    tiktok_daily.csv      one row per date — Overview + Viewers + FollowerHistory
                          joined, because they share a grain and belong together
    tiktok_videos.csv     one row per video, deduplicated
    tiktok_snapshots.csv  gender, territories and hourly activity in long form,
                          kept separate because they are point-in-time
                          distributions, not a time series

CSVs are written UTF-8 with BOM so Excel on Windows opens the emoji in video
titles correctly rather than as mojibake.

Usage
-----
    python consolidate_tiktok.py "<folder of .xlsx>" --out "<output folder>"
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import pathlib
import re
import sys

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl is required:  pip install openpyxl")

MONTHS = {
    m: i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], 1
    )
}

DAILY_SOURCES = {
    # file stem -> {source column: output column}
    "Overview": {
        "Video Views": "video_views",
        "Profile Views": "profile_views",
        "Likes": "likes",
        "Comments": "comments",
        "Shares": "shares",
    },
    "Viewers": {
        "Total Viewers": "total_viewers",
        "New Viewers": "new_viewers",
        "Returning Viewers": "returning_viewers",
    },
    "FollowerHistory": {
        "Followers": "followers",
        # TikTok has used more than one wording here; both are accepted so a
        # relabelled export does not silently drop the column.
        "Difference in followers from previous day": "follower_change",
        "Difference in followers from previous time period": "follower_change",
    },
}

DAILY_COLUMNS = [
    "date", "video_views", "profile_views", "likes", "comments", "shares",
    "total_viewers", "new_viewers", "returning_viewers",
    "followers", "follower_change",
]

VIDEO_COLUMNS = [
    "video_id", "video_url", "title", "post_date", "days_live",
    "views", "likes", "comments", "shares",
    "engagement_rate", "share_rate", "exported_on", "seen_in_files",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def read_sheet(path: pathlib.Path) -> list[list]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    wb.close()
    return rows


def month_day(value) -> tuple[int, int] | None:
    """'September 9' -> (9, 9). Returns None if unparseable."""
    if value is None:
        return None
    m = re.match(r"\s*([A-Za-z]+)\s+(\d{1,2})\s*$", str(value))
    if not m or m.group(1) not in MONTHS:
        return None
    return MONTHS[m.group(1)], int(m.group(2))


def date_series(values: list, today: dt.date) -> list[dt.date | None]:
    """
    Assign years to a chronological, ascending list of year-less dates.

    Rolls the year forward whenever the month goes backwards (December ->
    January), then anchors the whole series so its last date falls on or before
    the export date. Without the anchor, a series ending in September would be
    dated a full year late.
    """
    parsed = [month_day(v) for v in values]
    known = [p for p in parsed if p]
    if not known:
        return [None] * len(values)

    def build(start_year: int) -> list[dt.date | None]:
        out, year, prev_month = [], start_year, None
        for p in parsed:
            if p is None:
                out.append(None)
                continue
            month, day = p
            if prev_month is not None and month < prev_month:
                year += 1
            prev_month = month
            try:
                out.append(dt.date(year, month, day))
            except ValueError:      # 29 Feb in a non-leap year
                out.append(None)
        return out

    candidate = build(today.year)
    last = next((d for d in reversed(candidate) if d), None)
    if last and last > today:
        candidate = build(today.year - 1)
    return candidate


def most_recent(value, today: dt.date) -> dt.date | None:
    """A post date with no year: the latest such date at or before today."""
    p = month_day(value)
    if not p:
        return None
    month, day = p
    for year in (today.year, today.year - 1, today.year - 2):
        try:
            d = dt.date(year, month, day)
        except ValueError:
            continue
        if d <= today:
            return d
    return None


def number(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace(" ", "").replace(" ", "")
    if not s or s in {"-", "—", "–"}:
        return None
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    try:
        n = float(s)
    except ValueError:
        return None
    return n / 100 if pct else n


def clean_int(n):
    if n is None:
        return ""
    return int(n) if float(n).is_integer() else n


def write_csv(path: pathlib.Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel on Windows needs the BOM to read emoji in titles
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def build_daily(folder: pathlib.Path, today: dt.date, report: list[str]):
    daily: dict[dt.date, dict] = {}

    for stem, mapping in DAILY_SOURCES.items():
        path = folder / f"{stem}.xlsx"
        if not path.exists():
            report.append(f"  ! {stem}.xlsx not found — those columns will be empty")
            continue

        rows = read_sheet(path)
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        body = rows[1:]
        dates = date_series([r[0] for r in body], today)

        matched = {c: header.index(c) for c in mapping if c in header}
        # An output column counts as missing only when none of its accepted
        # source spellings appeared — otherwise every alias looks like a gap.
        missing = sorted({mapping[c] for c in mapping} - {mapping[c] for c in matched})
        if missing:
            report.append(f"  ! {stem}.xlsx: no source column for: {', '.join(missing)}")
            report.append(f"      header was: {', '.join(h for h in header if h)}")

        undated = 0
        for row, date in zip(body, dates):
            if date is None:
                undated += 1
                continue
            slot = daily.setdefault(date, {"date": date.isoformat()})
            for src, idx in matched.items():
                slot[mapping[src]] = clean_int(number(row[idx]))

        span = [d for d in dates if d]
        report.append(
            f"  {stem}.xlsx: {len(body)} rows, "
            f"{min(span).isoformat()} to {max(span).isoformat()}"
            + (f", {undated} undated skipped" if undated else "")
        )

    return [daily[d] for d in sorted(daily)]


def build_videos(folder: pathlib.Path, today: dt.date, report: list[str]):
    seen: dict[str, dict] = {}
    files_seen: dict[str, set] = collections.defaultdict(set)
    disagreements: list[str] = []
    total_rows = 0
    content_files = 0

    for path in sorted(folder.glob("*.xlsx")):
        rows = read_sheet(path)
        if not rows:
            continue
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        if "Video link" not in header:
            continue

        content_files += 1
        idx = {name: header.index(name) for name in header}

        for row in rows[1:]:
            url = row[idx["Video link"]]
            if not url:
                continue
            total_rows += 1
            url = str(url).strip()
            files_seen[url].add(path.name)

            metrics = {
                "views": number(row[idx.get("Total views")]),
                "likes": number(row[idx.get("Total likes")]),
                "comments": number(row[idx.get("Total comments")]),
                "shares": number(row[idx.get("Total shares")]),
            }

            if url in seen:
                prior = seen[url]
                for key, value in metrics.items():
                    old = prior["_m"][key]
                    if value is not None and old is not None and value != old:
                        disagreements.append(
                            f"{url.rsplit('/', 1)[-1]} {key}: {clean_int(old)} vs "
                            f"{clean_int(value)} (keeping the larger)"
                        )
                    # lifetime counters only go up, so the max is the freshest
                    if value is not None:
                        prior["_m"][key] = value if old is None else max(old, value)
            else:
                seen[url] = {
                    "_m": metrics,
                    "title": row[idx.get("Video title")],
                    "post_raw": row[idx.get("Post time")],
                    "exported_raw": row[idx.get("Time")],
                }

    out = []
    for url, rec in seen.items():
        post = most_recent(rec["post_raw"], today)
        exported = most_recent(rec["exported_raw"], today)
        m = rec["_m"]
        views = m["views"] or 0
        engagement = (
            ((m["likes"] or 0) + (m["comments"] or 0) + (m["shares"] or 0)) / views
            if views else None
        )
        out.append({
            "video_id": url.rsplit("/", 1)[-1],
            "video_url": url,
            "title": (str(rec["title"]).replace("\n", " ").strip()
                      if rec["title"] else ""),
            "post_date": post.isoformat() if post else "",
            "days_live": (today - post).days if post else "",
            "views": clean_int(m["views"]),
            "likes": clean_int(m["likes"]),
            "comments": clean_int(m["comments"]),
            "shares": clean_int(m["shares"]),
            "engagement_rate": round(engagement, 5) if engagement is not None else "",
            "share_rate": round((m["shares"] or 0) / views, 5) if views else "",
            "exported_on": exported.isoformat() if exported else "",
            "seen_in_files": len(files_seen[url]),
        })

    out.sort(key=lambda r: r["views"] if r["views"] != "" else -1, reverse=True)

    report.append(
        f"  Content: {content_files} files, {total_rows} rows -> "
        f"{len(out)} unique videos ({total_rows - len(out)} duplicate rows collapsed)"
    )
    if disagreements:
        report.append(
            f"  {len(disagreements)} metric disagreements between exports "
            f"(counts moved while you were downloading):"
        )
        for line in disagreements[:10]:
            report.append(f"      {line}")
        if len(disagreements) > 10:
            report.append(f"      ... and {len(disagreements) - 10} more")

    return out


def build_snapshots(folder: pathlib.Path, today: dt.date, report: list[str]):
    out = []

    simple = {
        "FollowerGender.xlsx": ("follower_gender", "Gender", "Distribution"),
        "FollowerTopTerritories.xlsx": ("follower_territory", "Top territories", "Distribution"),
    }
    for filename, (kind, key_col, val_col) in simple.items():
        path = folder / filename
        if not path.exists():
            continue
        rows = read_sheet(path)
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        if key_col not in header or val_col not in header:
            report.append(f"  ! {filename}: unexpected columns {header}")
            continue
        ki, vi = header.index(key_col), header.index(val_col)
        n = 0
        for row in rows[1:]:
            if row[ki] is None:
                continue
            out.append({
                "snapshot": kind,
                "key": str(row[ki]).strip(),
                "sub_key": "",
                "value": number(row[vi]),
            })
            n += 1
        report.append(f"  {filename}: {n} rows")

    path = folder / "FollowerActivity.xlsx"
    if path.exists():
        rows = read_sheet(path)
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        body = rows[1:]
        dates = date_series([r[0] for r in body], today)
        n = 0
        for row, date in zip(body, dates):
            if date is None:
                continue
            out.append({
                "snapshot": "follower_activity_hourly",
                "key": date.isoformat(),
                "sub_key": str(row[header.index("Hour")]),
                "value": number(row[header.index("Active followers")]),
            })
            n += 1
        report.append(f"  FollowerActivity.xlsx: {n} rows")

    return out


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", help="folder containing the TikTok .xlsx exports")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--today", help="export date, YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    folder = pathlib.Path(args.folder)
    out_dir = pathlib.Path(args.out)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    if not folder.is_dir():
        sys.exit(f"not a folder: {folder}")

    report: list[str] = []

    report.append("\nDAILY SERIES")
    daily = build_daily(folder, today, report)

    report.append("\nVIDEOS")
    videos = build_videos(folder, today, report)

    report.append("\nSNAPSHOTS")
    snapshots = build_snapshots(folder, today, report)

    write_csv(out_dir / "tiktok_daily.csv", DAILY_COLUMNS, daily)
    write_csv(out_dir / "tiktok_videos.csv", VIDEO_COLUMNS, videos)
    write_csv(out_dir / "tiktok_snapshots.csv",
              ["snapshot", "key", "sub_key", "value"], snapshots)

    print("\n".join(report))
    print(f"\nWrote to {out_dir}:")
    print(f"  tiktok_daily.csv      {len(daily)} rows")
    print(f"  tiktok_videos.csv     {len(videos)} rows")
    print(f"  tiktok_snapshots.csv  {len(snapshots)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
