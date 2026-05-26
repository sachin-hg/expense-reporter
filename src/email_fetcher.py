"""IMAP-based Gmail fetcher for bank statement emails."""

import imaplib
import email
import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from email.header import decode_header
from pathlib import Path
from typing import List
import logging

logger = logging.getLogger(__name__)


@dataclass
class PDFResult:
    bytes: bytes
    subject: str
    email_date: date
    account: str


class GmailFetcher:
    IMAP_SERVER = "imap.gmail.com"

    def __init__(self, config_path: str = "config/accounts.json"):
        with open(config_path) as f:
            self.accounts = json.load(f)

    def fetch_pdfs(self, year: int, month: int) -> List[PDFResult]:
        """Fetch all PDF attachments from statement emails relevant to the report month."""
        results = []
        # Look back 60 days to catch statements covering the target month
        search_from = date(year, month, 1) - timedelta(days=60)

        for account in self.accounts:
            email_addr = account["email"]
            password_env = account.get("password_env", "")
            password = os.environ.get(password_env, "")
            if not password:
                logger.error(f"Missing env var {password_env!r} for {email_addr}")
                continue

            label = account.get("label", "Transaction Statements")
            try:
                pdfs = self._fetch_from_account(email_addr, password, label, search_from)
                results.extend(pdfs)
                logger.info(f"Fetched {len(pdfs)} PDFs from {email_addr}")
            except Exception as e:
                logger.error(f"Failed to fetch from {email_addr}: {e}")

        return results

    def _fetch_from_account(
        self, email_addr: str, password: str, label: str, search_from: date
    ) -> List[PDFResult]:
        results = []

        mail = imaplib.IMAP4_SSL(self.IMAP_SERVER)
        mail.login(email_addr, password)

        # Gmail labels appear as IMAP folders; try both quoted and bare
        status, _ = mail.select(f'"{label}"')
        if status != "OK":
            status, _ = mail.select(label)
        if status != "OK":
            logger.warning(f"Label '{label}' not found in {email_addr}, listing folders...")
            self._log_folders(mail, email_addr)
            mail.logout()
            return results

        date_str = search_from.strftime("%d-%b-%Y")
        status, message_ids = mail.search(None, f"SINCE {date_str}")
        if status != "OK":
            mail.logout()
            return results

        ids = message_ids[0].split()
        logger.info(f"Found {len(ids)} emails in '{label}' for {email_addr}")

        for msg_id in ids:
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            subject = self._decode_header_str(msg["Subject"])
            email_date = self._parse_date(msg["Date"])

            for part in msg.walk():
                content_type = part.get_content_type()
                filename = part.get_filename() or ""
                is_pdf = content_type == "application/pdf" or (
                    content_type == "application/octet-stream"
                    and filename.lower().endswith(".pdf")
                )
                if not is_pdf:
                    continue

                pdf_bytes = part.get_payload(decode=True)
                if not pdf_bytes:
                    continue

                results.append(
                    PDFResult(
                        bytes=pdf_bytes,
                        subject=subject,
                        email_date=email_date,
                        account=email_addr,
                    )
                )

                # Cache to data/ for debugging / --skip-fetch runs
                safe_name = re.sub(r"[^\w\-_]", "_", subject)[:80]
                cache_path = Path("data") / f"{email_date}_{safe_name}.pdf"
                cache_path.write_bytes(pdf_bytes)
                logger.info(f"Cached PDF: {cache_path.name}")

        mail.logout()
        return results

    def _log_folders(self, mail, email_addr: str):
        status, folder_list = mail.list()
        if status == "OK":
            folders = [f.decode() if isinstance(f, bytes) else f for f in folder_list]
            logger.info(f"Available folders in {email_addr}:\n" + "\n".join(folders))

    def _decode_header_str(self, value: str) -> str:
        if not value:
            return ""
        parts = decode_header(value)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(str(part))
        return "".join(decoded)

    def _parse_date(self, date_str: str) -> date:
        from email.utils import parsedate_to_datetime
        try:
            return parsedate_to_datetime(date_str).date()
        except Exception:
            return date.today()
