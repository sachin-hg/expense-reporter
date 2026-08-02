#!/usr/bin/env python3
"""Generate reports/summary.csv from all monthly JSON sidecars in reports/."""

import calendar
import csv
import json
from pathlib import Path

from src.classifier import CATEGORIES
from src.report_generator import CATEGORY_LABELS


def main():
    json_files = sorted(Path("reports").glob("20??-??.json"))
    if not json_files:
        print("No report JSON files found in reports/")
        return

    months = {}
    for f in json_files:
        year, month = map(int, f.stem.split("-"))
        label = f"{calendar.month_abbr[month]} {year}"
        months[label] = json.loads(f.read_text(encoding="utf-8"))

    month_labels = list(months.keys())

    rows = []
    for cat in CATEGORIES:
        row = [CATEGORY_LABELS.get(cat, cat)]
        for lbl in month_labels:
            row.append(round(months[lbl].get(cat, 0.0), 2))
        rows.append(row)

    total_row = ["Total"]
    for lbl in month_labels:
        total_row.append(round(sum(months[lbl].get(cat, 0.0) for cat in CATEGORIES), 2))
    rows.append(total_row)

    out = Path("reports/summary.csv")
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Category"] + month_labels)
        writer.writerows(rows)

    print(f"Written: {out}  ({len(rows) - 1} categories × {len(month_labels)} months)")


if __name__ == "__main__":
    main()
