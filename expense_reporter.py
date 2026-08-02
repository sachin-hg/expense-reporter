#!/usr/bin/env python3
"""
Monthly expense reporter.
Run on the 8th of each month to generate the previous month's report.
Usage:
    python expense_reporter.py                  # reports previous month
    python expense_reporter.py --month 2026-04  # reports specific month
    python expense_reporter.py --no-push        # skip GitHub push
    python expense_reporter.py --skip-fetch     # use cached PDFs in data/
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("expense_reporter")


def get_report_month(override) -> tuple:
    """Return (year, month) to report on. Defaults to the calendar month before today."""
    if override:
        try:
            year, month = map(int, override.split("-"))
            if not (1 <= month <= 12):
                raise ValueError
            return year, month
        except (ValueError, AttributeError):
            logger.error(f"Invalid --month format {override!r}. Use YYYY-MM.")
            sys.exit(1)

    today = date.today()
    # Go to last day of previous month, then extract year/month
    first_of_this = today.replace(day=1)
    from datetime import timedelta
    last_of_prev = first_of_this - timedelta(days=1)
    return last_of_prev.year, last_of_prev.month


def load_cached_pdfs() -> list:
    """Load PDFs from data/ directory for --skip-fetch mode."""
    from src.email_fetcher import PDFResult

    results = []
    for pdf_file in Path("data").glob("*.pdf"):
        try:
            subject = pdf_file.stem  # filename without extension
            results.append(
                PDFResult(
                    bytes=pdf_file.read_bytes(),
                    subject=subject,
                    email_date=date.today(),
                    account="cached",
                )
            )
        except OSError as e:
            logger.warning(f"Could not read {pdf_file}: {e}")
    return results


def main():
    parser = argparse.ArgumentParser(description="Generate monthly expense report")
    parser.add_argument(
        "--month", metavar="YYYY-MM", help="Month to report (default: previous month)"
    )
    parser.add_argument("--no-push", action="store_true", help="Skip GitHub push")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Skip email fetch; use PDF files already in data/",
    )
    parser.add_argument(
        "--force-fresh",
        action="store_true",
        help="Ignore cached Claude extractions and re-process all PDFs",
    )
    parser.add_argument(
        "--local",
        metavar="FOLDER",
        help=(
            "Process PDFs from a local folder instead of fetching from Gmail. "
            "Without --month, generates reports for every calendar month found in the statements. "
            "PDF passwords: set PASS_<first4charsOfFilename>=<password> in .env"
        ),
    )
    args = parser.parse_args()

    local_mode = bool(args.local)
    all_months_mode = local_mode and not args.month

    # Resolve target month(s) — deferred to post-classification for all_months_mode
    year = month = month_label = None
    if not all_months_mode:
        year, month = get_report_month(args.month)
        month_label = f"{year}-{month:02d}"
        logger.info(f"Generating expense report for {month_label}")

    # 1. Fetch PDFs ─────────────────────────────────────────────────────
    if local_mode:
        from src.local_fetcher import load_local_pdfs

        logger.info(f"Local mode: loading PDFs from {args.local}")
        pdf_results = load_local_pdfs(args.local)
    elif args.skip_fetch:
        logger.info("--skip-fetch: loading PDFs from data/")
        pdf_results = load_cached_pdfs()
    else:
        from src.email_fetcher import GmailFetcher

        logger.info("Fetching email statements via IMAP...")
        fetcher = GmailFetcher()
        pdf_results = fetcher.fetch_pdfs(year, month)

    # When targeting a single month in local mode, skip PDFs that clearly belong to
    # other months — saves API calls for large historical folders.
    if local_mode and not all_months_mode:
        from src.local_fetcher import is_relevant_for_month

        before = len(pdf_results)
        pdf_results = [p for p in pdf_results if is_relevant_for_month(p.subject, year, month)]
        skipped = before - len(pdf_results)
        if skipped:
            logger.info(f"Skipped {skipped} PDF(s) outside {month_label} — not relevant")

    logger.info(f"PDFs to process: {len(pdf_results)}")
    if not pdf_results:
        logger.warning("No statement PDFs found — generating empty report")

    # 2. Extract transactions from each PDF ─────────────────────────────
    from src.statement_processor import StatementProcessor

    logger.info("Extracting transactions with Claude...")
    processor = StatementProcessor(force_fresh=args.force_fresh)
    all_transactions = []

    for pdf in pdf_results:
        logger.info(f"  Processing: {pdf.subject[:70]}")
        try:
            txns = processor.process(pdf)
            debits = sum(1 for t in txns if t.amount > 0)
            refunds = sum(1 for t in txns if t.amount < 0)
            logger.info(f"    → {debits} transactions, {refunds} refunds")
            all_transactions.extend(txns)
        except Exception as e:
            logger.error(f"    ✗ Failed: {e}")

    logger.info(f"Total transactions extracted: {len(all_transactions)}")

    # 3. Classify ────────────────────────────────────────────────────────
    from src.classifier import ExpenseClassifier

    logger.info("Classifying expenses with Claude...")
    classifier = ExpenseClassifier()
    try:
        classified = classifier.classify(all_transactions)
    except Exception as e:
        logger.error(f"Classification failed — aborting, no report written: {e}")
        sys.exit(1)

    # 4. Generate report(s) ──────────────────────────────────────────────
    from src.report_generator import ReportGenerator

    logger.info("Generating markdown report(s)...")
    generator = ReportGenerator()

    if all_months_mode:
        # Discover all calendar months represented in the transactions
        months = sorted(set((t.date.year, t.date.month) for t in classified))
        if not months:
            logger.warning("No transactions found — no reports generated")
            return
        logger.info(
            f"Found {len(months)} months: {', '.join(f'{y}-{m:02d}' for y, m in months)}"
        )
        report_entries = []  # list of (path, year, month)
        for yr, mo in months:
            rpath = generator.generate(classified, yr, mo)
            logger.info(f"  Saved → {rpath}")
            report_entries.append((rpath, yr, mo))
    else:
        report_path = generator.generate(classified, year, month)
        logger.info(f"Report saved → {report_path}")
        report_entries = [(report_path, year, month)]

    # 5. Push to GitHub ──────────────────────────────────────────────────
    if not args.no_push:
        from src.github_pusher import GitHubPusher

        logger.info("Pushing to GitHub (sachin-hg/expense-reporter)...")
        try:
            pusher = GitHubPusher()
            pusher.push_all(report_entries)
            logger.info("Pushed successfully")
        except RuntimeError as e:
            logger.error(f"GitHub push failed: {e}")
            for rpath, _, _ in report_entries:
                logger.info(f"  Report available locally at {rpath}")
    else:
        logger.info("--no-push: skipping GitHub push")

    if all_months_mode:
        labels = ", ".join(f"{y}-{m:02d}" for y, m in months)
        print(f"\n✓ Done — reports generated for: {labels}")
    else:
        print(f"\n✓ Done — {month_label} report complete: {report_entries[0][0]}")


if __name__ == "__main__":
    main()
