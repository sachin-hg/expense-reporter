"""Local folder PDF loader for --local mode.

Password resolution: set PASS_<first 4 chars of filename> in .env
  e.g. PASS_5268=mypassword  covers  5268XXXXXXXXXX70_22-01-2026_455.pdf
       PASS_Payt=secret      covers  Paytm_Statement_April_2026.pdf
If no matching env var is found, the file is assumed to be unprotected.
"""

import calendar as _cal
import io
import os
import re
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_MONTH_BY_NAME = {name.lower(): i for i, name in enumerate(_cal.month_name) if name}
_MONTH_BY_NAME.update({name.lower(): i for i, name in enumerate(_cal.month_abbr) if name})


def _statement_end_date(stem: str) -> Optional[date]:
    """Parse the statement-end date from a filename stem, or return None if unrecognised."""
    # HDFC / cycle style: ..._22-07-2026_...  (DD-MM-YYYY surrounded by underscores or hyphens)
    m = re.search(r'[_\-](\d{2})-(\d{2})-(\d{4})[_\-]', stem)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass

    # ICICI Emeralde style: ..._YYYY-MM at the end of the stem
    # Cycle runs 19th of prev month → 18th of named month, so end date = 18th.
    m = re.search(r'_(\d{4})-(\d{2})$', stem)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 18)
        except ValueError:
            pass

    # Paytm UPI style: Paytm_UPI_Statement_01_Apr'26_-_30_Apr'26  (or with " (1)" suffix)
    # The apostrophe is U+2019 (curly right single quote) on macOS-generated filenames.
    # Take the LAST DD_Mon'YY occurrence — that is the range end date.
    matches = re.findall(r"(\d{2})_([A-Za-z]{3})['’](\d{2})", stem)
    if matches:
        day_s, mon_s, yr_s = matches[-1]
        month_num = _MONTH_BY_NAME.get(mon_s.lower())
        if month_num:
            year = 2000 + int(yr_s)
            try:
                return date(year, month_num, int(day_s))
            except ValueError:
                pass

    # PhonePe style: PhonePe_Transaction_Statement 2026-04  (space + YYYY-MM at end)
    m = re.search(r'\s(\d{4})-(\d{2})$', stem)
    if m:
        try:
            year, month = int(m.group(1)), int(m.group(2))
            last_day = _cal.monthrange(year, month)[1]
            return date(year, month, last_day)
        except ValueError:
            pass

    # Paytm / calendar-month style: ..._MonthName_YYYY  (e.g. Paytm_Statement_April_2026)
    m = re.search(r'_([A-Za-z]{3,9})_(\d{4})', stem)
    if m:
        month_num = _MONTH_BY_NAME.get(m.group(1).lower())
        if month_num:
            last_day = _cal.monthrange(int(m.group(2)), month_num)[1]
            return date(int(m.group(2)), month_num, last_day)

    return None


def is_relevant_for_month(stem: str, target_year: int, target_month: int) -> bool:
    """Return True if this PDF likely contains transactions for (target_year, target_month).

    For calendar-month statements (e.g. Paytm): exact month match only.
    For billing-cycle statements (e.g. HDFC ending ~22nd): include if the cycle end date
    falls in the target month or the following month — a cycle ending Aug 22 still
    contains July 23–31 transactions.
    """
    end_date = _statement_end_date(stem)
    if end_date is None:
        return True  # can't determine — include to be safe

    last_day_of_end_month = _cal.monthrange(end_date.year, end_date.month)[1]
    is_calendar_month = end_date.day == last_day_of_end_month

    if is_calendar_month:
        return end_date.year == target_year and end_date.month == target_month

    # Billing-cycle statement: end date in target month OR the month immediately after
    next_year, next_month = target_year, target_month + 1
    if next_month > 12:
        next_month, next_year = 1, target_year + 1

    in_target = (end_date.year == target_year and end_date.month == target_month)
    in_next = (end_date.year == next_year and end_date.month == next_month)
    return in_target or in_next


def _get_password(filename: str) -> Optional[str]:
    prefix = Path(filename).name[:4]
    env_key = f"PASS_{prefix}"
    pw = os.environ.get(env_key)
    if pw:
        logger.debug(f"Using password from {env_key} for {filename}")
    return pw


def _open_pdf(pdf_bytes: bytes, password: Optional[str]) -> bytes:
    """Open PDF with pikepdf, decrypting if a password is supplied, return clean bytes."""
    import pikepdf

    open_kwargs: dict = {}
    if password:
        open_kwargs["password"] = password

    with pikepdf.open(io.BytesIO(pdf_bytes), **open_kwargs) as pdf:
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()


def load_local_pdfs(folder: str) -> List:
    """Load all PDFs from *folder*, decrypting with env-var passwords as needed."""
    from src.email_fetcher import PDFResult

    folder_path = Path(folder)
    if not folder_path.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    results = []
    for pdf_file in sorted(folder_path.glob("*.pdf")):
        try:
            raw_bytes = pdf_file.read_bytes()
            password = _get_password(pdf_file.name)

            try:
                clean_bytes = _open_pdf(raw_bytes, password)
            except Exception as exc:
                err = str(exc).lower()
                if "password" in err and not password:
                    logger.warning(
                        f"Skipping {pdf_file.name}: password-protected — "
                        f"add PASS_{pdf_file.name[:4]}=<password> to .env"
                    )
                elif "password" in err:
                    logger.error(
                        f"Skipping {pdf_file.name}: wrong password (PASS_{pdf_file.name[:4]})"
                    )
                else:
                    logger.error(f"Skipping {pdf_file.name}: {exc}")
                continue

            results.append(
                PDFResult(
                    bytes=clean_bytes,
                    subject=pdf_file.stem,
                    email_date=date.today(),
                    account="local",
                )
            )
            logger.info(f"Loaded: {pdf_file.name}")

        except OSError as exc:
            logger.error(f"Could not read {pdf_file.name}: {exc}")

    return results
