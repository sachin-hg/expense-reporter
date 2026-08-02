"""Classifies transactions into expense categories using Claude API."""

import json
import re
from pathlib import Path
from typing import List, Optional
import anthropic
import logging

from .statement_processor import Transaction

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-7"

CATEGORIES = [
    "travel",
    "trip",
    "shopping",
    "baby",
    "outside_food",
    "grocery",
    "utilities",
    "health",
    "upskill_ai",
    "rohan",
    "papa",
    "one_time",
    "investments",
    "tax",
    "others",
]

CLASSIFICATION_RULES = """\
Classify each Indian bank/UPI transaction into exactly one category.

━━━ TIER 1 — NAMED MERCHANT OVERRIDES (apply first, always win) ━━━
These exact merchants must always map to the given category regardless of other rules:
- "Tim Tim Fresh" → OUTSIDE_FOOD, merchant_short = "Tim Tim Fresh"
- "Multitech Motors" (any spelling/case) → TRAVEL, merchant_short = "Multitech Motors"
- "S Sons" (any spelling/case) → GROCERY, merchant_short = "S Sons"
- "Vijay Thakur" (any spelling/case) → HEALTH, merchant_short = "Salon"
- "Netflix" → UTILITIES, merchant_short = "Netflix"
- "LinkedIn" → UPSKILL_AI, merchant_short = "LinkedIn"
- "Anthropic" → UPSKILL_AI, merchant_short = "Anthropic"
- "OpenAI" or "ChatGPT" → UPSKILL_AI, merchant_short = "ChatGPT / OpenAI"
- "Claude" (as a subscription/service, not a person's name) → UPSKILL_AI, merchant_short = "Claude"
- "GreatFrontend" → UPSKILL_AI, merchant_short = "GreatFrontend"
- "Cursor" (AI code editor, as a subscription) → UPSKILL_AI, merchant_short = "Cursor"
- "Patel Bhavesh" (any case/spelling) → PAPA, merchant_short = "Patel Bhavesh"
- "Parmod Bhatia" (any case/spelling) → HEALTH, merchant_short = "Parmod Bhatia (Medicine)"
- "PolicyBazaar" (any case) → UTILITIES, merchant_short = "PolicyBazaar (Insurance)"
- "Gyftr" (any case) → SHOPPING, merchant_short = "Gyftr"
- "iShop" / "ishop" (any case) → SHOPPING if amount < 5000, TRIP if amount >= 5000
- "SmartBuy" (any case) → SHOPPING if amount < 5000, TRIP if amount >= 5000
- "Kuleep Pal" / "Kuldeep Pal" / "Kulip Pal" (any similar spelling) → TRAVEL, merchant_short = "Parking"
- "Fuel Surcharge" in merchant/description (any case) → TRAVEL, merchant_short = "Fuel Surcharge"
- "Petro Surcharge" / "Petrol Surcharge" (any case) → TRAVEL, merchant_short = "Fuel Surcharge"
- "Uber" (any case) → TRAVEL, merchant_short = "Uber"
- "National Highways" (any case) → TRAVEL, merchant_short = "National Highways"
- "Hopscotch" (any case) → BABY, merchant_short = "Hopscotch"
- "Myntra" (any case) → SHOPPING, merchant_short = "Myntra"
- "Flipkart" (any case) → SHOPPING, merchant_short = "Flipkart"
- "Trent Limited" / "TRENT" (any case) → SHOPPING, merchant_short = "Trent"
- "Innovative Retail" (any case) → GROCERY, merchant_short = "Innovative Retail"
- "BigBasket" / "BIG BASKET" (any case) → GROCERY, merchant_short = "BigBasket"
- "M S D P Saini" / "MSDP SAINI" (any case/spacing) → GROCERY, merchant_short = "M S D P Saini"
- "PharmEasy" / "PHARMEASY" (any case) → MEDICINE, merchant_short = "PharmEasy"
- "CBDT" / "CBDTGURGAON" (any case) → TAX, merchant_short = "Income Tax (CBDT)"
- "OEBB" → TRIP, merchant_short = "OEBB"
- "VakaTrip" / "VAKATRIP" (any case) → TRIP, merchant_short = "VakaTrip"
- "Visa Agent" (any case) → TRIP, merchant_short = "Visa Agent Fee"
- "VFS" (any case) → TRIP, merchant_short = "VFS Visa Fee"
- "Yes Madam" / "YESMADAM" (any case) → HEALTH, merchant_short = "Salon"
- "Everyday Fitness" (any case) → HEALTH, merchant_short = "Everyday Fitness"
- "Global Value" and "Cash Back" in the same merchant name → ROHAN, merchant_short = "Global Value Cash Back"
- "Aseem Rastogi" (any case) → UPSKILL_AI, merchant_short = "Aseem Rastogi"
- "Rainbow Toys" (any case) → BABY, merchant_short = "Rainbow Toys"
- "NPS Trust" / "NPS" (any case) → INVESTMENTS, merchant_short = "NPS Trust"
- "Tin 2 O" (any case) → TAX, merchant_short = "Tin 2 O"
- "Bureau of Energy" (any case) → PAPA, merchant_short = "Bureau of Energy"
- "Jiana Adventures" (any case) → OUTSIDE_FOOD, merchant_short = "Jiana Adventures"

Keyword rules — apply when the merchant name CONTAINS these words (case-insensitive substring):
- contains "hotel" or "resort" → TRIP (use the hotel/resort name as merchant_short)
- contains "flight" → TRIP, merchant_short = short airline or booking name

━━━ TIER 2 — GENERAL CATEGORY RULES ━━━

TRAVEL: Fuel stations (HP, Indian Oil, BPCL, IOCL, Shell, Bharat Petroleum, Hindustan Petroleum,
  any petrol pump / service station / filling station), FastTag, toll plazas, parking payments.
  Fuel surcharge waivers and related IGST reversals are also TRAVEL.

TRIP: Airlines (IndiGo, Air India, SpiceJet, Vistara, Akasa, GoAir, GoFirst), hotels (Marriott,
  OYO, Taj, ITC, Leela, Hilton, Hyatt, Radisson), Airbnb, travel booking platforms
  (MakeMyTrip, Cleartrip, Yatra, booking.com, IRCTC, redBus).

SHOPPING: Myntra, Nykaa, Ajio, Meesho, Snapdeal, Tata CLiQ, Lifestyle, Westside, Zara, H&M,
  Reliance Trends, fashion/apparel, cosmetics/beauty (non-baby), electronics (Croma, Vijay Sales),
  Amazon or Flipkart purchases that are clearly non-grocery and non-baby.

BABY: FirstCry, Cloudnine (hospital or store), Hopscotch, Mothercare, Mamas & Papas, Nuby,
  any brand clearly for infants/toddlers, diapers (Pampers, Huggies, MamyPoko), baby formula,
  baby care products, pediatric doctor/clinic visits, baby medicines.

OUTSIDE_FOOD: Swiggy, Zomato, Dunzo (food orders), restaurants, cafes, dhabas, cloud kitchens,
  Starbucks, McDonald's, KFC, Domino's, Pizza Hut, Subway, Barbeque Nation, food courts.

GROCERY: BigBasket, Zepto, Blinkit, Instamart (Swiggy grocery), Grofers, DMart, More Supermarket,
  Reliance Fresh, Nature Basket, JioMart, Lulu hypermarket, Spar, local kiryana stores,
  any supermarket/grocery store purchase.

UTILITIES: DHBVN, MCG, BSES, Tata Power, Adani Electricity, any state electricity board,
  water board, piped gas (MGL, IGL, Adani Gas), broadband/internet (Airtel, Jio, ACT),
  property tax, government fee payments. Also: Netflix, Spotify, Amazon Prime, Disney+ Hotstar,
  and other streaming/subscription services.

HEALTH: Pharmacies (MedPlus, Netmeds, 1mg, Tata 1mg, Apollo Pharmacy, PharmEasy, any chemist),
  doctor consultations (non-pediatric), diagnostic labs (Dr. Lal Pathlabs, SRL, Thyrocare),
  hospitals that are NOT Cloudnine and NOT baby-related.
  Also: gyms, fitness centres, yoga studios, sports clubs, personal trainers.
  Also: salons, spas, beauty parlours, grooming, massage centres, self-care services.
  NOTE: baby-related medical (Cloudnine, pediatric visits, baby medicines) → BABY, not HEALTH.

UPSKILL_AI: LinkedIn Learning, LinkedIn Premium, Anthropic, Claude.ai, OpenAI, ChatGPT Plus,
  GreatFrontend, Coursera, Udemy, any professional learning platform or AI tool subscription.

OTHERS: Insurance premiums, bank charges/fees, gym/fitness, mutual fund/SIP, transfers between
  accounts, ATM withdrawals, gifts, anything not clearly fitting the above.

━━━ TIER 3 — PAYTM TAG FALLBACK (use only when bank = "Paytm" and no Tier 1/2 rule matched) ━━━
If the description contains a Paytm hashtag, map it:
- "#Taxi" or "#Auto" → TRAVEL
- "#Fuel" or "#️ Fuel" → TRAVEL
- "#Food" → OUTSIDE_FOOD
- "#Shopping" → SHOPPING
- "#Grocery" or "#Groceries" → GROCERY
- "#Bills" or "#Bill Payments" → UTILITIES
- "#Entertainment" → UTILITIES
- "#Health" or "#Medical" → MEDICINE
- Any other tag → OTHERS

━━━ REFUNDS ━━━
Negative amounts are refunds — classify into the same category the original purchase belongs to.

━━━ MERCHANT SHORT NAMES ━━━
Return a clear, brief merchant_short a human would recognise.
Examples: "Swiggy" not "SWIGGY INTERNET PVT LTD", "Zepto" not "KIRANAKART TECHNOLOGIES".
For Tier 1 overrides, use the merchant_short specified above exactly."""

CLASSIFICATION_SYSTEM = (
    "You are an expert Indian expense classifier. Classify transactions precisely following the rules. "
    "Return only valid JSON — no markdown, no explanation."
)

EMI_CONFIG_PATH = Path("existing_emis.json")


def _ym_ordinal(year: int, month: int) -> int:
    return year * 12 + month


def _load_emi_config() -> list:
    if not EMI_CONFIG_PATH.exists():
        return []
    try:
        return json.loads(EMI_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Could not load {EMI_CONFIG_PATH}: {exc}")
        return []


def _match_emi(t: Transaction, emi_config: list) -> Optional[dict]:
    """Return the matching EMI config entry for this transaction, or None."""
    txn_ord = _ym_ordinal(t.date.year, t.date.month)
    haystack = f"{t.merchant} {t.description}".lower()

    for emi in emi_config:
        if emi.get("type", "emi") != "emi":
            continue
        identifier = emi.get("identifier", "").lower()
        if not identifier or identifier not in haystack:
            continue
        start_year, start_month = map(int, emi["start_month"].split("-"))
        start_ord = _ym_ordinal(start_year, start_month)
        end_ord = start_ord + int(emi["duration_months"]) - 1
        if start_ord <= txn_ord <= end_ord:
            return emi

    return None


def _match_txn_entry(t: Transaction, entry: dict) -> bool:
    """Return True if a transaction matches an override/suppress config entry.

    Required: identifier substring in merchant+description (case-insensitive).
    Optional filters: date (exact), amount (within ₹2), source_account (substring).
    """
    haystack = f"{t.merchant} {t.description}".lower()
    identifier = entry.get("identifier", "").lower()
    if not identifier or identifier not in haystack:
        return False
    if "date" in entry and t.date.isoformat() != entry["date"]:
        return False
    if "amount" in entry and abs(t.amount - float(entry["amount"])) > 2.0:
        return False
    if "source_account" in entry and entry["source_account"].lower() not in t.source_account.lower():
        return False
    return True


def _apply_transaction_overrides(transactions: List[Transaction], config: list) -> List[Transaction]:
    """Apply manual suppress and override rules from the config file.

    type=suppress: remove the transaction entirely (e.g. duplicate settlement legs).
    type=override: update category, label, and/or amount (wins over Rohan/EMI auto-detection).
    """
    suppress_entries = [e for e in config if e.get("type") == "suppress"]
    override_entries = [e for e in config if e.get("type") == "override"]

    result = []
    for t in transactions:
        suppressed = any(_match_txn_entry(t, e) for e in suppress_entries)
        if suppressed:
            logger.info(f"Suppressing (manual rule): {t.merchant[:60]}  ₹{t.amount:,.0f}")
            continue

        for entry in override_entries:
            if _match_txn_entry(t, entry):
                t.category = entry.get("category", t.category)
                t.merchant_short = entry.get("label", t.merchant_short)
                if "amount_override" in entry:
                    t.amount = float(entry["amount_override"])
                logger.info(
                    f"Override (manual rule): {t.merchant[:60]} → "
                    f"{t.category}/{t.merchant_short}  ₹{t.amount:,.0f}"
                )
                break

        result.append(t)

    return result


# Fuel surcharge refund patterns — used to back-identify the fuel purchase
_FUEL_SURCHARGE_RE = re.compile(
    r'fuel\s+surcharge|petro\s+surcharge|petrol\s+surcharge\s+waiver', re.IGNORECASE
)
_IGST_REV_RE = re.compile(r'igst[\s\-]*rev', re.IGNORECASE)

# Tier 1 named merchants (and obvious Tier 2 non-fuel names) that must never be
# matched as a fuel debit even if their amount happens to be ~100× a surcharge.
_NON_FUEL_MERCHANT_RE = re.compile(
    r'gyftr|smartbuy|ishop|tim.?tim|netflix|linkedin|anthropic|openai|chatgpt|'
    r'greatfrontend|cursor|patel.?bhavesh|parmod.?bhatia|policybazaar|myntra|flipkart|'
    r'trent|innovative.?retail|bigbasket|big.?basket|pharmeasy|cbdt|oebb|vakatrip|'
    r'visa.?agent|vfs|yes.?madam|yesmadam|everyday.?fitness|hopscotch|s\.?sons|'
    r'vijay.?thakur|swiggy|zomato|amazon|blinkit|zepto|meesho|firstcry|first.?cry|'
    r'cloudnine|uber|national.?highway|kuleep.?pal|kuldeep.?pal|caf[ée]|rainbow.?toys',
    re.IGNORECASE
)

# Pattern that identifies an EMI-conversion credit (not an EMI-eligible badge)
_EMI_CONVERSION_RE = re.compile(r'aggregator\s*emi|offus\s*credit', re.IGNORECASE)


def _tag_fuel_transactions(transactions: List[Transaction]) -> None:
    """Pre-classify fuel purchases and their associated surcharge/IGST refunds.

    On HDFC Infinia (5523) every fuel debit is followed within 1-2 days by a
    'PETRO SURCHARGE WAIVER' credit (~1% of fuel amount).
    On ICICI Emeralde (5524) it's 'Fuel Surcharge-Off-Us' (~1%) plus
    'IGST-Rev-Cl@18%' (18% of the 1% surcharge) on the same date.

    Matching is purely amount-based so it works even when source_bank is "Unknown":
      1. Tag each surcharge refund row → travel / "Fuel Surcharge"
      2. Find the fuel debit: unclassified debit within 2 days where
         amount ≈ 100 × |surcharge|  (0.8%–1.2% tolerance)
      3. Find the IGST refund: unclassified credit on the same date where
         amount ≈ 0.18 × |surcharge|  (only tagged when paired with a surcharge)
    """
    surcharge_rows = []
    for t in transactions:
        combined = f"{t.merchant} {t.description}"
        if _FUEL_SURCHARGE_RE.search(combined):
            t.category = "travel"
            t.merchant_short = "Fuel Surcharge"
            surcharge_rows.append(t)

    for sur in surcharge_rows:
        sur_abs = abs(sur.amount)

        # Match fuel debit: amount ≈ 100× surcharge (within ±20%)
        # Skip merchants already identified by Tier 1 / known non-fuel names.
        expected_fuel = sur_abs * 100
        candidates = [
            t for t in transactions
            if t.amount > 0
            and not t.category
            and 0 <= (sur.date - t.date).days <= 2
            and abs(t.amount - expected_fuel) / expected_fuel <= 0.20
            and not _NON_FUEL_MERCHANT_RE.search(t.merchant)
        ]
        if candidates:
            fuel_txn = min(candidates, key=lambda t: abs(t.amount - expected_fuel))
            fuel_txn.category = "travel"
            fuel_txn.merchant_short = "Fuel"
            logger.info(
                f"Fuel pre-classify: {fuel_txn.merchant[:50]}  ₹{fuel_txn.amount:,.0f}"
                f"  (surcharge {sur.amount} on {sur.date})"
            )

        # Match IGST refund: same date, amount ≈ 18% of surcharge (within ±20%)
        expected_igst = sur_abs * 0.18
        for t in transactions:
            if (
                not t.category
                and t.amount < 0
                and t.date == sur.date
                and _IGST_REV_RE.search(f"{t.merchant} {t.description}")
                and abs(abs(t.amount) - expected_igst) / expected_igst <= 0.20
            ):
                t.category = "travel"
                t.merchant_short = "Fuel Surcharge (GST)"
                break


def _suppress_emi_originals(
    transactions: List[Transaction], emi_config: list
) -> List[Transaction]:
    """Remove the original lump-sum purchase and its EMI-conversion credit reversal.

    When a purchase is converted to EMI the statement shows:
      (a) Original debit  — e.g. "EMI POLICYBAZAAR…  ₹1,06,408"
      (b) Conversion credit — "AGGREGATOR EMI - OFFUS CREDIT…  ₹1,06,408" (reverses a)
      (c) Monthly PRIN + INT rows  — these stay and are classified via existing_emis.json

    This function drops (a) and (b) so only (c) appears in the report.
    Matching is by amount (within ₹2 tolerance) + date window (start_month ± 1 month).

    NOTE: transactions with an "EMI" eligibility badge (e.g. MORTON WILLIAMS with EMI tag)
    are NOT affected — they don't match the AGGREGATOR EMI pattern or the original_amount.
    """
    suppress: set = set()

    for emi in emi_config:
        raw = emi.get("original_amount")
        if not raw:
            continue
        target = float(raw)
        start_year, start_month = map(int, emi["start_month"].split("-"))
        start_ord = _ym_ordinal(start_year, start_month)

        # (b) EMI conversion credit — identified by pattern + amount match
        for i, t in enumerate(transactions):
            haystack = f"{t.merchant} {t.description}"
            if _EMI_CONVERSION_RE.search(haystack) and abs(abs(t.amount) - target) < 2.0:
                suppress.add(i)
                logger.info(
                    f"Suppressing EMI conversion credit: {t.merchant[:60]}  ₹{abs(t.amount):,.0f}"
                )
                break

        # (a) Original purchase — same amount (positive), date in start_month or month before
        for i, t in enumerate(transactions):
            if i in suppress:
                continue
            if abs(t.amount - target) < 2.0:
                txn_ord = _ym_ordinal(t.date.year, t.date.month)
                if start_ord - 1 <= txn_ord <= start_ord:
                    suppress.add(i)
                    logger.info(
                        f"Suppressing original EMI purchase: {t.merchant[:60]}  ₹{t.amount:,.0f}"
                    )
                    break

    if not suppress:
        return transactions
    return [t for i, t in enumerate(transactions) if i not in suppress]


class ExpenseClassifier:
    def __init__(self):
        self.client = anthropic.Anthropic()

    def classify(self, transactions: List[Transaction]) -> List[Transaction]:
        if not transactions:
            return transactions

        emi_config = _load_emi_config()

        # Step 0 — drop original lump-sum purchases and their EMI conversion credits
        # (must run before anything else so suppressed rows don't reach the report)
        transactions = _suppress_emi_originals(transactions, emi_config)

        # Step 1 — tag fuel purchases via their surcharge refund fingerprint
        _tag_fuel_transactions(transactions)

        # Pre-classify without a Claude call:
        #   1. Rohan's transactions (owner field set by extractor)
        #   2. Known EMI transactions (matched by identifier string in existing_emis.json)
        for t in transactions:
            if "ROHAN" in t.owner.upper():
                t.category = "rohan"
                t.merchant_short = t.merchant  # exact name as shown in statement
                continue

            if emi_config:
                emi = _match_emi(t, emi_config)
                if emi:
                    t.category = emi["category"]
                    t.merchant_short = emi["label"]

        # Step 2 — apply manual suppress/override rules (wins over Rohan/EMI auto-detection)
        transactions = _apply_transaction_overrides(transactions, emi_config)

        # Only send non-pre-classified transactions to Claude
        normal = [t for t in transactions if not t.category]

        txn_list = [
            {
                "id": i,
                "date": t.date.isoformat(),
                "merchant": t.merchant,
                "description": t.description,
                "amount": t.amount,
                "bank": t.source_bank,
            }
            for i, t in enumerate(normal)
        ]

        # Batch in chunks of 80 to stay comfortably within context
        chunk_size = 80
        for start in range(0, len(txn_list), chunk_size):
            chunk = txn_list[start : start + chunk_size]
            try:
                classifications = self._classify_batch(chunk)
                for item in classifications:
                    idx = item.get("id")
                    if idx is not None and 0 <= idx < len(normal):
                        normal[idx].category = item.get("category", "others")
                        normal[idx].merchant_short = item.get("merchant_short", "")
            except Exception as e:
                raise RuntimeError(
                    f"Classification batch failed (ids {start}–{start + len(chunk) - 1}): {e}"
                ) from e

        for t in transactions:
            if not t.category:
                t.category = "others"
            if not t.merchant_short:
                t.merchant_short = t.merchant

        return transactions

    def _classify_batch(self, transactions: list) -> list:
        prompt = f"""{CLASSIFICATION_RULES}

Categories: {", ".join(CATEGORIES)}

Return JSON:
{{
  "classifications": [
    {{"id": 0, "category": "outside_food", "merchant_short": "Swiggy"}},
    ...
  ]
}}

Transactions to classify:
{json.dumps(transactions, indent=2)}"""

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=CLASSIFICATION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return data.get("classifications", [])
