#!/usr/bin/env python3
"""
Normalise TikTok Studio analytics CSV exports into one tidy long table.

Why this exists
---------------
TikTok Studio exports one CSV per tab (Overview, Content, Viewers, Followers),
each with its own column names, its own date format, and column headers that
change with the interface language. None of that is stable enough to load
straight into a warehouse.

This script turns any of them into the same shape:

    export_tab, metric_date, metric_name, metric_value, source_file, loaded_at

One row per date per metric. A new column appearing in a future export becomes
new rows, not a schema migration — which is the point of the long format here.

Design notes
------------
- Unrecognised columns are KEPT, under their normalised name, and listed in the
  run report. Dropping a column silently is how you discover six months later
  that TikTok started exporting something useful and you threw it away.
- Nothing is deduplicated on load. The same date can appear in several exports
  with different values (TikTok's numbers settle over ~48h). Keeping every
  version is what lets the dbt layer decide which one wins — usually the latest
  load for a given date. Collapsing it here would throw that choice away.

Usage
-----
    python pipeline/parse_tiktok_csv.py data/tiktok/raw/*.csv \\
        --out data/tiktok/normalised/tiktok_metrics.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import io
import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Column mapping.
#
# NOT YET PINNED TO A REAL EXPORT. These are the names TikTok Studio is
# expected to use; the mapping is deliberately in one place so it can be
# corrected in one edit once a real file has been seen. Anything not listed
# here still loads — it just keeps its normalised name and is flagged in the
# report so you can decide whether to add an alias.
# ---------------------------------------------------------------------------
COLUMN_ALIASES = {
    "video_views": "video_views",
    "views": "video_views",
    "profile_views": "profile_views",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
    "followers": "followers",
    "total_followers": "followers",
    "net_followers": "net_followers",
    "new_followers": "new_followers",
    "lost_followers": "lost_followers",
    "unique_viewers": "unique_viewers",
    "new_viewers": "new_viewers",
    "returning_viewers": "returning_viewers",
}

DATE_COLUMN_NAMES = {"date", "day", "metric_date"}

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d")

OUTPUT_FIELDS = [
    "export_tab",
    "metric_date",
    "metric_name",
    "metric_value",
    "source_file",
    "loaded_at",
]


def normalise_name(raw: str) -> str:
    """'Video Views (total)' -> 'video_views_total'."""
    s = raw.strip().lower()
    s = re.sub(r"[^\w\s-]", " ", s)
    s = re.sub(r"[\s\-]+", "_", s).strip("_")
    return s


def guess_tab(path: pathlib.Path) -> str:
    """TikTok names the file after the tab; fall back to the stem."""
    stem = path.stem.lower()
    for tab in ("overview", "content", "viewers", "followers"):
        if tab in stem:
            return tab
    return normalise_name(path.stem)


def parse_date(value: str) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_number(value: str) -> float | None:
    """TikTok writes '1,234', '12.3%', '1 234' and '—' for no data."""
    v = (value or "").strip()
    if not v or v in {"-", "—", "–", "N/A", "n/a"}:
        return None
    v = v.replace(" ", "").replace(" ", "").replace(",", "")
    percent = v.endswith("%")
    if percent:
        v = v[:-1]
    try:
        n = float(v)
    except ValueError:
        return None
    return n / 100 if percent else n


def read_rows(path: pathlib.Path) -> list[dict]:
    """TikTok exports UTF-8, sometimes with a BOM."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def parse_file(path: pathlib.Path, loaded_at: str) -> tuple[list[dict], dict]:
    rows = read_rows(path)
    report = {
        "file": path.name,
        "rows_in": len(rows),
        "rows_out": 0,
        "unmapped_columns": set(),
        "undated_rows": 0,
        "unparsed_values": 0,
        "error": None,
    }

    if not rows:
        report["error"] = "empty file"
        return [], report

    tab = guess_tab(path)
    headers = {h: normalise_name(h) for h in rows[0].keys() if h}

    date_key = next(
        (raw for raw, norm in headers.items() if norm in DATE_COLUMN_NAMES), None
    )
    if date_key is None:
        report["error"] = "no date column found; columns were: " + ", ".join(
            sorted(headers.values())
        )
        return [], report

    out: list[dict] = []

    for row in rows:
        metric_date = parse_date(row.get(date_key, ""))
        if metric_date is None:
            report["undated_rows"] += 1
            continue

        for raw_col, norm_col in headers.items():
            if raw_col == date_key:
                continue

            value = parse_number(row.get(raw_col, ""))
            if value is None:
                if (row.get(raw_col) or "").strip():
                    report["unparsed_values"] += 1
                continue

            if norm_col not in COLUMN_ALIASES:
                report["unmapped_columns"].add(norm_col)

            out.append(
                {
                    "export_tab": tab,
                    "metric_date": metric_date,
                    "metric_name": COLUMN_ALIASES.get(norm_col, norm_col),
                    "metric_value": value,
                    "source_file": path.name,
                    "loaded_at": loaded_at,
                }
            )

    report["rows_out"] = len(out)
    return out, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help="CSV files or globs")
    ap.add_argument("--out", required=True, help="output CSV path")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any file failed or any column was unmapped",
    )
    args = ap.parse_args()

    paths: list[pathlib.Path] = []
    for pattern in args.inputs:
        paths.extend(pathlib.Path(p) for p in sorted(glob.glob(pattern)))
    paths = [p for p in paths if p.is_file()]

    if not paths:
        print("No input files matched.", file=sys.stderr)
        return 1

    loaded_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    all_rows: list[dict] = []
    reports: list[dict] = []

    for path in paths:
        rows, report = parse_file(path, loaded_at)
        all_rows.extend(rows)
        reports.append(report)

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    # --- run report --------------------------------------------------------
    print(f"\nWrote {len(all_rows)} rows to {out_path}\n")
    failed = False
    unmapped_any = False

    for r in reports:
        status = "FAILED" if r["error"] else "ok"
        print(f"  [{status}] {r['file']}: {r['rows_in']} rows in -> {r['rows_out']} out")
        if r["error"]:
            failed = True
            print(f"      {r['error']}")
        if r["undated_rows"]:
            print(f"      {r['undated_rows']} rows skipped, unreadable date")
        if r["unparsed_values"]:
            print(f"      {r['unparsed_values']} values skipped, not numeric")
        if r["unmapped_columns"]:
            unmapped_any = True
            print(
                "      columns with no alias (kept as-is): "
                + ", ".join(sorted(r["unmapped_columns"]))
            )

    if unmapped_any:
        print(
            "\n  Unmapped columns were loaded under their own names. Add them to "
            "COLUMN_ALIASES if you want a stable metric_name for them."
        )

    if args.strict and (failed or unmapped_any):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
