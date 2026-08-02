"""Generates monthly expense report as a Markdown file."""

import calendar
import json
import math
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
    "health": "Health, Fitness & Self Care",
    "upskill_ai": "Upskill / AI",
    "rohan": "Rohan",
    "papa": "Papa",
    "one_time": "One-Time Expense",
    "investments": "Investments",
    "tax": "Tax",
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
        history = self._load_history(year, month, 6)
        md = self._render(by_category, year, month, prev_totals, history)

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

    def _load_history(self, year: int, month: int, n: int) -> List[Tuple[str, Dict]]:
        """Return [(short_label, totals_dict)] for up to n months before current, oldest first."""
        results = []
        for delta in range(n, 0, -1):
            y, m = year, month - delta
            while m <= 0:
                m += 12
                y -= 1
            json_path = REPORTS_DIR / f"{y}-{m:02d}.json"
            if json_path.exists():
                try:
                    data = json.loads(json_path.read_text())
                    results.append((f"{calendar.month_abbr[m]} '{str(y)[-2:]}", data))
                except (json.JSONDecodeError, OSError):
                    pass
        return results

    def _render(
        self,
        by_category: Dict[str, List[Transaction]],
        year: int,
        month: int,
        prev_totals: List[Tuple[str, Dict]],
        history: List[Tuple[str, Dict]],
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

        # ── Charts ─────────────────────────────────────────────────────
        lines += self._render_charts(by_category, year, month, history)
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

            if cat == "rohan":
                lines.append("| Date | Merchant | USD | Card | Amount |")
                lines.append("| ---- | -------- | --- | ---- | ------:|")
                for t in txns_sorted:
                    name = t.merchant_short or t.merchant
                    if t.amount < 0:
                        name = f"{name} ↩ Refund"
                    usd = f"${t.usd_amount:,.2f}" if t.usd_amount else "—"
                    card = t.source_bank if t.source_bank and t.source_bank != "Unknown" else "—"
                    lines.append(
                        f"| {t.date.strftime('%b %d')} | {name} | {usd} | {card} | {self._fmt(t.amount)} |"
                    )
                lines.append(f"| | **Total** | | | **{self._fmt(cat_total)}** |")
            else:
                lines.append("| Date | Merchant | Card | Amount |")
                lines.append("| ---- | -------- | ---- | ------:|")
                for t in txns_sorted:
                    name = t.merchant_short or t.merchant
                    if t.amount < 0:
                        name = f"{name} ↩ Refund"
                    card = t.source_bank if t.source_bank and t.source_bank != "Unknown" else "—"
                    lines.append(
                        f"| {t.date.strftime('%b %d')} | {name} | {card} | {self._fmt(t.amount)} |"
                    )
                lines.append(f"| | **Total** | | **{self._fmt(cat_total)}** |")
            lines.append("")

        return "\n".join(lines)

    def _render_charts(
        self,
        by_category: Dict[str, List[Transaction]],
        year: int,
        month: int,
        history: List[Tuple[str, Dict]],
    ) -> List[str]:
        lines: List[str] = []
        current_label = f"{calendar.month_abbr[month]} '{str(year)[-2:]}"
        current_totals = {
            cat: round(sum(t.amount for t in txns), 2)
            for cat, txns in by_category.items()
        }

        all_months = history + [(current_label, current_totals)]

        # ── Line chart: month-on-month trend ────────────────────────────
        active_cats = [
            cat for cat in CATEGORIES
            if any(totals.get(cat, 0) != 0 for _, totals in all_months)
        ]

        all_vals_k = [
            totals.get(cat, 0) / 1000
            for cat in active_cats
            for _, totals in all_months
        ]
        max_k = max(all_vals_k, default=100)
        min_k = min(all_vals_k, default=0)
        y_max = max(math.ceil(max_k / 25) * 25, 25)
        y_min = min(math.floor(min_k / 25) * 25, 0)

        x_labels = ", ".join(f'"{lbl}"' for lbl, _ in all_months)

        lines.append("```mermaid")
        lines.append("xychart-beta")
        lines.append('    title "Monthly Expense Trend (Rs thousands)"')
        lines.append(f"    x-axis [{x_labels}]")
        lines.append(f'    y-axis "Rs Thousands" {y_min} --> {y_max}')
        for cat in active_cats:
            cat_label = CATEGORY_LABELS.get(cat, cat)
            vals = [round(totals.get(cat, 0) / 1000, 1) for _, totals in all_months]
            lines.append(f'    line "{cat_label}" [{", ".join(str(v) for v in vals)}]')
        lines.append("```")
        lines.append("")
        # Legend key (xychart-beta doesn't render line labels in GitHub's mermaid)
        lines.append("**Lines:** " + " · ".join(
            CATEGORY_LABELS.get(cat, cat) for cat in active_cats
        ))
        lines.append("")

        # ── Pie chart: current month category breakdown ──────────────────
        pie_data = sorted(
            [
                (CATEGORY_LABELS.get(cat, cat), round(sum(t.amount for t in txns)))
                for cat, txns in by_category.items()
                if sum(t.amount for t in txns) > 0
            ],
            key=lambda x: CATEGORIES.index(
                next((c for c in CATEGORIES if CATEGORY_LABELS.get(c) == x[0]), "others")
            ) if any(CATEGORY_LABELS.get(c) == x[0] for c in CATEGORIES) else 999,
        )

        lines.append("```mermaid")
        lines.append("pie showData")
        lines.append(f"    title {calendar.month_name[month]} {year} Expenses")
        for label, amount in pie_data:
            lines.append(f'    "{label}" : {amount}')
        lines.append("```")
        lines.append("")

        return lines

    def _fmt(self, amount: float) -> str:
        if amount < 0:
            return f"(₹{abs(amount):,.0f})"
        return f"₹{amount:,.0f}"
