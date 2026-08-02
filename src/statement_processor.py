"""Processes bank statement PDFs using Claude API to extract transactions."""

import base64
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional
import anthropic
import logging

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"


@dataclass
class Transaction:
    date: date
    merchant: str
    amount: float           # positive = spend/debit, negative = refund/credit
    description: str
    source_bank: str
    source_account: str
    category: str = ""
    merchant_short: str = ""
    owner: str = ""         # sub-account holder when statement splits by person (e.g. "ROHAN AGRAWAL")
    usd_amount: Optional[float] = None  # foreign currency spend amount, when available


BANK_HINTS = {
    "HDFC Infinia": (
        "HDFC Bank Infinia Credit Card statement (card ending 5619). "
        "Extract all debit/purchase transactions from the 'Domestic Transactions' and "
        "'International Transactions' sections. The Debit column shows spend amounts (positive). "
        "Also extract refunds/reversals in the Credit column as negative amounts. "
        "IMPORTANT: The 'International Transactions' section may contain sub-sections headed by "
        "person names, specifically 'SACHIN AGRAWAL' and 'ROHAN AGRAWAL'. "
        "For every transaction, set the 'owner' field to the name of the sub-section it falls under "
        "(e.g. \"SACHIN AGRAWAL\" or \"ROHAN AGRAWAL\"). Leave owner empty for domestic transactions. "
        "For international transactions, also extract the foreign currency amount shown "
        "(e.g. 'USD 20.00') into 'foreign_amount' (number only, e.g. 20.00) and "
        "'foreign_currency' (e.g. 'USD'). "
        "Do NOT skip the FCY markup fee rows — include them as transactions under their owner. "
        "Some transactions may have an 'EMI' badge/tag — this only means they are eligible for EMI "
        "conversion; extract them as normal purchase transactions, not as EMI rows."
    ),
    "HDFC Swiggy": (
        "HDFC Bank Swiggy Credit Card statement. "
        "Extract all purchase/debit transactions (positive) and refunds/reversals (negative)."
    ),
    "HDFC": (
        "HDFC Bank Credit Card statement. "
        "Extract all purchase/debit transactions (positive) and refunds/reversals (negative)."
    ),
    "ICICI": (
        "ICICI Bank Credit Card statement. "
        "Look for the transaction details table. Debit entries are positive, credit/refund entries are negative."
    ),
    "ICICI Emeralde": (
        "ICICI Bank Emeralde Private Metal Credit Card statement. "
        "Look for the transaction details section. Debit amounts are spends (positive). "
        "Credit amounts are refunds/reversals (negative)."
    ),
    "Paytm": (
        "Paytm payment statement. "
        "Extract all outgoing payments as positive amounts: UPI transfers, wallet debits, bill payments. "
        "Extract incoming refunds/credits as negative amounts. "
        "IMPORTANT: Paytm transactions often have hashtag category tags (e.g. #Taxi, #Food, #Fuel, "
        "#Bill Payments, #Money Received, #Shopping). Include these tags verbatim in the description field."
    ),
    "PhonePe": (
        "PhonePe UPI transaction statement. "
        "Each entry shows 'Type: Debit' (outgoing payment) or 'Type: Credit' (incoming). "
        "Extract only Debit transactions as positive amounts — these are real expenses. "
        "Skip Credit rows (e.g. 'Received from ...') — they are incoming money transfers, not expenses. "
        "The merchant name follows 'Paid to' in the transaction details. "
        "Amount format is 'INR 123.45' — extract only the numeric value."
    ),
    "SBI": "SBI Credit Card statement. Extract all debit transactions (positive) and refunds (negative).",
    "Axis": "Axis Bank Credit Card statement. Extract all debit transactions (positive) and refunds (negative).",
    "Kotak": "Kotak Bank Credit Card statement. Extract all debit transactions (positive) and refunds (negative).",
}

EXTRACTION_PROMPT = """\
Extract ALL transactions from this {bank} statement — both debits (spends) and credits (refunds).

{hint}

Return JSON in exactly this format (no markdown fences):
{{
  "statement_period": {{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}},
  "transactions": [
    {{
      "date": "YYYY-MM-DD",
      "merchant": "Merchant or payee name as shown in statement",
      "description": "Full transaction description",
      "amount": 1234.56,
      "owner": "",
      "foreign_amount": null,
      "foreign_currency": null
    }}
  ]
}}

Field notes:
- owner: sub-account holder name when the statement groups by person (e.g. "SACHIN AGRAWAL"). Empty string otherwise.
- foreign_amount: numeric foreign-currency spend amount for international transactions (e.g. 20.00). null if not applicable.
- foreign_currency: ISO currency code for international transactions (e.g. "USD"). null if not applicable.

Rules:
- Debits/purchases/spends: amount is a POSITIVE number
- Credits/refunds/reversals/cashbacks credited back: amount is a NEGATIVE number
- Skip: opening/closing balances, credit card bill payments made by customer, interest charges on outstanding
- Include debits: all purchases, payments, EMI debits, fee charges
- Include credits: refunds, reversals, merchant cashbacks, goods/service credits
- date must be YYYY-MM-DD (use the actual transaction date, not the posting date)
- Extract every single transaction row — do not summarize or group
- merchant: use the name shown in the statement, not your own knowledge of the business"""


CACHE_DIR = Path("cache")

# Bank-specific keywords that uniquely identify transaction pages.
# A page is kept only when ALL keywords in the set are present (case-insensitive).
_TXN_PAGE_KEYWORDS: dict = {
    "ICICI Emeralde": {"transaction details", "reward points"},
    "ICICI":          {"transaction details", "reward points"},
    "HDFC Infinia":   {"transaction description"},
    "HDFC Swiggy":    {"transaction description"},
    "HDFC":           {"transaction description"},
}


class StatementProcessor:
    def __init__(self, force_fresh: bool = False):
        self.client = anthropic.Anthropic()
        self.force_fresh = force_fresh
        CACHE_DIR.mkdir(exist_ok=True)

    def process(self, pdf_result) -> List[Transaction]:
        """Extract transactions from a PDF statement, using cached JSON when available."""
        bank = self._detect_bank(pdf_result.subject)
        cache_path = CACHE_DIR / f"{pdf_result.subject}.json"

        if not self.force_fresh and cache_path.exists():
            logger.info(f"    (cache hit) {cache_path.name}")
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            data = self._call_claude(pdf_result, bank)
            cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.debug(f"    Saved extraction cache → {cache_path.name}")

        transactions = []

        for t in data.get("transactions", []):
            try:
                txn_date = self._parse_date(t.get("date", ""))
                if txn_date is None:
                    logger.warning(f"Skipping — unparseable date: {t}")
                    continue

                amount_raw = str(t.get("amount", 0)).replace(",", "").strip()
                amount = float(amount_raw)
                if amount == 0:
                    continue

                if bank == "Paytm" and self._is_paytm_ignored(t):
                    logger.debug(f"Skipping Paytm ignored transaction: {t.get('merchant')}")
                    continue

                if bank == "PhonePe" and self._is_phonepe_ignored(t):
                    logger.debug(f"Skipping PhonePe credit transaction: {t.get('merchant')}")
                    continue

                if bank.startswith("ICICI") and self._is_icici_ignored(t):
                    logger.debug(f"Skipping ICICI bill payment: {t.get('merchant')}")
                    continue

                usd_amount = None
                if t.get("foreign_amount") and t.get("foreign_currency", "").upper() == "USD":
                    try:
                        usd_amount = float(str(t["foreign_amount"]).replace(",", ""))
                    except (ValueError, TypeError):
                        pass

                transactions.append(
                    Transaction(
                        date=txn_date,
                        merchant=t.get("merchant") or t.get("description", "Unknown"),
                        amount=amount,
                        description=t.get("description", ""),
                        source_bank=bank,
                        source_account=pdf_result.account,
                        owner=t.get("owner", ""),
                        usd_amount=usd_amount,
                    )
                )
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Skipping transaction {t}: {e}")

        return transactions

    # Known card prefixes whose bill payments via Paytm should be ignored
    _CARD_PREFIXES = re.compile(r"\b(5619|5524|5268|5523)\b")

    def _is_paytm_ignored(self, t: dict) -> bool:
        """Return True for Paytm transactions that are not real expenses."""
        combined = (
            t.get("description", "") + " " + t.get("merchant", "")
        ).lower()

        # Incoming money — not an expense
        if "money received" in combined:
            return True

        # Credit card bill payments — "bill payment of <bank> credit card ..."
        if re.search(r'bill payment of .+ credit card', combined):
            return True

        # Fallback: bill payment to a known card prefix
        if "bill payment" in combined and self._CARD_PREFIXES.search(combined):
            return True

        # Generic CC payment (e.g. "BPPY CC PAYMENT BD016101...")
        if re.search(r'\bcc payment\b', combined):
            return True

        return False

    def _is_icici_ignored(self, t: dict) -> bool:
        """Return True for ICICI credit entries that are card bill payments, not refunds."""
        combined = (t.get("description", "") + " " + t.get("merchant", "")).lower()
        # "Payment Received" = customer paid their card bill — skip
        if "payment received" in combined:
            return True
        # "Refund Amt Transfer to Bank Acc" = settlement leg of a merchant refund already
        # captured as a negative transaction — skip to avoid double-counting
        if "refund amt transfer to bank" in combined:
            return True
        return False

    def _is_phonepe_ignored(self, t: dict) -> bool:
        """Return True for PhonePe credit/incoming transactions that are not expenses."""
        combined = (t.get("description", "") + " " + t.get("merchant", "")).lower()
        return "received from" in combined or t.get("amount", 0) < 0

    def _filter_transaction_pages(self, pdf_bytes: bytes, bank: str) -> bytes:
        """Return a PDF with only pages that contain transaction tables.

        Strips cover pages, T&C, reward summaries, MAD calculation pages, etc.
        Paytm and PhonePe statements are single-page and returned unchanged.
        Falls back to the full PDF on any error.
        """
        if bank in ("Paytm", "PhonePe"):
            return pdf_bytes
        try:
            import pikepdf
            import pdfminer.high_level

            with pikepdf.open(io.BytesIO(pdf_bytes)) as src:
                total = len(src.pages)
                if total <= 2:
                    return pdf_bytes

                keep = []
                for i, page in enumerate(src.pages):
                    tmp = pikepdf.Pdf.new()
                    tmp.pages.append(page)
                    buf = io.BytesIO()
                    tmp.save(buf)
                    buf.seek(0)
                    text = pdfminer.high_level.extract_text(buf)
                    if self._page_has_transactions(text, bank):
                        keep.append(i)

                if not keep:
                    logger.warning("  Page filter: no transaction pages detected — sending all %d", total)
                    return pdf_bytes

                filtered = pikepdf.Pdf.new()
                for i in keep:
                    filtered.pages.append(src.pages[i])

                out = io.BytesIO()
                filtered.save(out)
                logger.info("  Page filter: keeping %d/%d pages", len(keep), total)
                return out.getvalue()

        except Exception as exc:
            logger.warning("  Page filtering failed (%s) — sending full PDF", exc)
            return pdf_bytes

    def _page_has_transactions(self, text: str, bank: str) -> bool:
        """True if this page contains a transaction table, using bank-specific keywords."""
        keywords = _TXN_PAGE_KEYWORDS.get(bank)
        if not keywords:
            return True  # unknown bank — keep every page
        lower = text.lower()
        return all(kw in lower for kw in keywords)

    def _call_claude(self, pdf_result, bank: str) -> dict:
        hint = BANK_HINTS.get(bank, "Extract all debit spend transactions from this bank statement.")
        prompt = EXTRACTION_PROMPT.format(bank=bank, hint=hint)
        filtered_bytes = self._filter_transaction_pages(pdf_result.bytes, bank)
        pdf_b64 = base64.standard_b64encode(filtered_bytes).decode("utf-8")

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=(
                "You are a financial data extraction specialist. Extract transactions from bank "
                "statements with perfect accuracy. Return only valid JSON — no markdown, no explanation."
            ),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)

    def _detect_bank(self, subject: str) -> str:
        s = subject.lower()
        if "phonepe" in s:
            return "PhonePe"
        if "paytm" in s:
            return "Paytm"
        if "emeralde" in s or "emerald" in s:
            return "ICICI Emeralde"
        if "icici" in s:
            return "ICICI"
        if "hdfc" in s and "swiggy" in s:
            return "HDFC Swiggy"
        if "hdfc" in s and "infinia" in s:
            return "HDFC Infinia"
        if "hdfc" in s:
            return "HDFC"
        if "sbi" in s:
            return "SBI"
        if "axis" in s:
            return "Axis"
        if "kotak" in s:
            return "Kotak"
        return "Unknown"

    def _parse_date(self, date_str: str) -> Optional[date]:
        if not date_str:
            return None
        for fmt in (
            "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
            "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
        ):
            try:
                return datetime.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        logger.warning(f"Could not parse date: {date_str!r}")
        return None
