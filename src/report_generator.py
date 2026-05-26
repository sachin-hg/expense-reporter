"""Generates monthly expense report as a Markdown file."""

import calendar
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .statement_processor import Transaction
from .classifier import CATEGORIES

REPORTS_DIR = Path("reports")

CATEGORY_LABELS: Dict[str, str] = {
    "travel": "Travel",
    "trip": "Trip",
    "shopping": "Shopping",
    "baby": "Baby",
    "outside_food": "Outside Food",
    "grocery": "Grocery",
    "utilities": "Utilities & Bills",
    "medicine": "Medicine",
    "others": "Others",
}


class ReportGenerator:
    def __init__(self):
        REPORTS_DIR.mkdir(exist_ok=True)

    def generate(self, transactions: List[Transaction], year: int, month: int) -> Path:
        # Filter to target calendar month only
        monthly = [t for t in transactions if t.date.year == year and t.date.month == month]

        by_category: Dict[str, List[Transaction]] = defaultdict(list)
        for t in monthly:
            by_category[t.category or "others"].append(t)

        prev_totals = self._load_prev_totals(year, month)
        md = self._render(by_category, year, month, prev_totals)

        report_path = REPORTS_DIR / f"{year}-{month:02d}.md"
        report_path.write_text(md, encoding="utf-8")

        # JSON sidecar for future comparison lookups
        totals = {cat: round(sum(t.amount for t in txns), 2) for cat, txns in by_category.items()}
        (REPORTS_DIR / f"{year}-{month:02d}.json").write_text(
            json.dumps(totals, indent=2), encoding="utf-8"
        )

        return report_path

    def _load_prev_totals(self, year: int, month: int) -> List[Tuple[str, Dict]]:
        """Return [(label, totals_dict)] for previous 2 months, oldest first."""
        results = []
        for delta in [2, 1]:
            y, m = year, month - delta
            if m <= 0:
                m += 12
                y -= 1
            json_path = REPORTS_DIR / f"{y}-{m:02d}.json"
            if json_path.exists():
                try:
                    data = json.loads(json_path.read_text())
                    results.append((f"{calendar.month_abbr[m]} {y}", data))
                except (json.JSONDecodeError, OSError):
                    pass
        return results

    def _render(
        self,
        by_category: Dict[str, List[Transaction]],
        year: int,
        month: int,
        prev_totals: List[Tuple[str, Dict]],
    ) -> str:
        month_name = calendar.month_name[month]
        current_label = f"{month_name} {year}"
        lines: List[str] = []

        lines.append(f"# Expense Report — {current_label}")
        lines.append(f"\n_Generated: {date.today().isoformat()}_\n")

        # ── Summary table ──────────────────────────────────────────────
        lines.append("## Summary\n")
        headers = ["Category", current_label] + [lbl for lbl, _ in prev_totals]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

        total_current = 0.0
        total_prev = [0.0] * len(prev_totals)

        for cat in CATEGORIES:
            txns = by_category.get(cat, [])
            current = sum(t.amount for t in txns)
            prev_vals = [ptotals.get(cat, 0.0) for _, ptotals in prev_totals]

            if current == 0 and all(v == 0 for v in prev_vals):
                continue

            total_current += current
            for i, v in enumerate(prev_vals):
                total_prev[i] += v

            row = [CATEGORY_LABELS.get(cat, cat), self._fmt(current)]
            for v in prev_vals:
                row.append(self._fmt(v))
            lines.append("| " + " | ".join(row) + " |")

        total_row = ["**Total**", f"**{self._fmt(total_current)}**"]
        for t in total_prev:
            total_row.append(f"**{self._fmt(t)}**")
        lines.append("| " + " | ".join(total_row) + " |")

        lines += ["", "---", ""]

        # ── Per-category detail tables ──────────────────────────────────
        for cat in CATEGORIES:
            txns = by_category.get(cat, [])
            if not txns:
                continue

            txns_sorted = sorted(txns, key=lambda t: t.date)
            cat_total = sum(t.amount for t in txns_sorted)
            cat_label = CATEGORY_LABELS.get(cat, cat)

            lines.append(f"## {cat_label}\n")
            lines.append("| Date | Merchant | Amount |")
            lines.append("| ---- | -------- | ------:|")

            for t in txns_sorted:
                name = t.merchant_short or t.merchant
                lines.append(f"| {t.date.strftime('%b %d')} | {name} | {self._fmt(t.amount)} |")

            lines.append(f"| | **Total** | **{self._fmt(cat_total)}** |")
            lines.append("")

        return "\n".join(lines)

    def _fmt(self, amount: float) -> str:
        return f"₹{amount:,.0f}"
