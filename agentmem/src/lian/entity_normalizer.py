"""
Financial entity normalization for the keyed supersession router.

Translates company names, ISINs, CUSIPs, and common aliases to their canonical
ticker symbol before comparing structured metadata keys.  This makes the keyed
fast path resilient to multi-source ingestion where different vendors use
different identifiers for the same security.

Examples that resolve to the same canonical form:
  "Apple"       → AAPL
  "Apple Inc."  → AAPL
  "US0378331005" (ISIN)  → AAPL
  "037833100"   (CUSIP)  → AAPL

Structural keys normalized: ticker, entity, isin, cusip.
All other metadata keys are returned unchanged.

To add securities not in the static table, set the AGENTMEM_ENTITY_OVERRIDES
environment variable to a JSON file path containing a dict of
canonical_ticker → [list_of_additional_aliases].
"""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache

logger = logging.getLogger("agentmem.entity_normalizer")

# ── Equity catalogue ──────────────────────────────────────────────────────────
# Format: canonical_ticker → (frozenset_of_name_aliases, isin, cusip)
# ISINs and CUSIPs are stored without the check digit for CUSIP to keep
# comparisons forgiving of data-vendor formatting differences.

_EQUITY_DATA: dict[str, tuple[frozenset[str], str, str]] = {
    # ── Mega-cap tech ────────────────────────────────────────────────────────
    "AAPL": (frozenset({"apple", "apple inc", "apple inc.", "apple computer", "apple computers"}),
             "US0378331005", "037833100"),
    "MSFT": (frozenset({"microsoft", "microsoft corp", "microsoft corporation"}),
             "US5949181045", "594918104"),
    "GOOGL": (frozenset({"google", "alphabet", "alphabet inc", "alphabet inc.", "googl", "google class a"}),
              "US02079K3059", "02079K305"),
    "GOOG":  (frozenset({"google class c", "alphabet class c", "goog"}),
              "US02079K1079", "02079K107"),
    "AMZN": (frozenset({"amazon", "amazon.com", "amazon com", "amazon inc", "amazon inc."}),
             "US0231351067", "023135106"),
    "NVDA": (frozenset({"nvidia", "nvidia corp", "nvidia corporation"}),
             "US67066G1040", "67066G104"),
    "META": (frozenset({"meta", "meta platforms", "meta platforms inc", "facebook", "fb"}),
             "US30303M1027", "30303M102"),
    "TSLA": (frozenset({"tesla", "tesla inc", "tesla motors", "tesla motor"}),
             "US88160R1014", "88160R101"),
    "AVGO": (frozenset({"broadcom", "broadcom inc", "broadcom corp"}),
             "US11135F1012", "11135F101"),
    "AMD":  (frozenset({"amd", "advanced micro devices", "advanced micro devices inc"}),
             "US0079031078", "007903107"),
    "QCOM": (frozenset({"qualcomm", "qualcomm inc"}),
             "US7475251036", "747525103"),
    "INTC": (frozenset({"intel", "intel corp", "intel corporation"}),
             "US4581401001", "458140100"),
    "CSCO": (frozenset({"cisco", "cisco systems", "cisco systems inc"}),
             "US17275R1023", "17275R102"),
    "CRM":  (frozenset({"salesforce", "salesforce inc", "salesforce.com"}),
             "US79466L3024", "79466L302"),
    "ORCL": (frozenset({"oracle", "oracle corp", "oracle corporation"}),
             "US68389X1054", "68389X105"),
    "IBM":  (frozenset({"ibm", "international business machines", "international business machines corp"}),
             "US4592001014", "459200101"),
    "NOW":  (frozenset({"servicenow", "service now", "servicenow inc"}),
             "US81762P1021", "81762P102"),

    # ── Financial ────────────────────────────────────────────────────────────
    "JPM":  (frozenset({"jpmorgan", "jp morgan", "jpmorgan chase", "jpmorgan chase & co", "j.p. morgan"}),
             "US46625H1005", "46625H100"),
    "BAC":  (frozenset({"bank of america", "bank of america corp", "bofa", "merrill lynch"}),
             "US0605051046", "060505104"),
    "WFC":  (frozenset({"wells fargo", "wells fargo & company", "wells fargo bank"}),
             "US9497461015", "949746101"),
    "GS":   (frozenset({"goldman sachs", "goldman sachs group", "goldman"}),
             "US38141G1040", "38141G104"),
    "MS":   (frozenset({"morgan stanley", "morgan stanley & co"}),
             "US6174464486", "617446448"),
    "BLK":  (frozenset({"blackrock", "blackrock inc"}),
             "US09248X1081", "09248X108"),
    "SCHW": (frozenset({"charles schwab", "schwab", "charles schwab corp"}),
             "US8085131055", "808513105"),
    "AXP":  (frozenset({"american express", "amex", "american express company"}),
             "US0258161092", "025816109"),
    "V":    (frozenset({"visa", "visa inc"}),
             "US92826C8394", "92826C839"),
    "MA":   (frozenset({"mastercard", "mastercard inc", "mastercard incorporated"}),
             "US57636Q1040", "57636Q104"),
    "C":    (frozenset({"citigroup", "citi", "citibank", "citigroup inc"}),
             "US1729674242", "172967424"),

    # ── Healthcare / biotech ─────────────────────────────────────────────────
    "JNJ":  (frozenset({"johnson & johnson", "johnson and johnson", "j&j", "jnj"}),
             "US4781601046", "478160104"),
    "LLY":  (frozenset({"eli lilly", "lilly", "eli lilly and company"}),
             "US5324571083", "532457108"),
    "ABBV": (frozenset({"abbvie", "abbvie inc"}),
             "US00287Y1091", "00287Y109"),
    "MRK":  (frozenset({"merck", "merck & co", "merck and co"}),
             "US58933Y1055", "58933Y105"),
    "PFE":  (frozenset({"pfizer", "pfizer inc"}),
             "US7170811035", "717081103"),
    "UNH":  (frozenset({"unitedhealth", "united health", "unitedhealth group", "uhg"}),
             "US91324P1021", "91324P102"),
    "TMO":  (frozenset({"thermo fisher", "thermo fisher scientific", "thermo fisher scientific inc"}),
             "US8835561023", "883556102"),
    "ABT":  (frozenset({"abbott", "abbott laboratories", "abbott labs"}),
             "US0028241000", "002824100"),
    "AMGN": (frozenset({"amgen", "amgen inc"}),
             "US0311621009", "031162100"),
    "GILD": (frozenset({"gilead", "gilead sciences", "gilead sciences inc"}),
             "US3755581036", "375558103"),
    "BIIB": (frozenset({"biogen", "biogen inc"}),
             "US09062X1037", "09062X103"),
    "MRNA": (frozenset({"moderna", "moderna inc"}),
             "US60770K1079", "60770K107"),

    # ── Consumer / retail ────────────────────────────────────────────────────
    "WMT":  (frozenset({"walmart", "wal-mart", "wal mart", "walmart inc"}),
             "US9311421039", "931142103"),
    "KO":   (frozenset({"coca-cola", "coca cola", "the coca-cola company", "coke"}),
             "US1912161007", "191216100"),
    "PEP":  (frozenset({"pepsi", "pepsico", "pepsico inc"}),
             "US7134481081", "713448108"),
    "MCD":  (frozenset({"mcdonald's", "mcdonalds", "mcdonald's corp"}),
             "US5801351017", "580135101"),
    "COST": (frozenset({"costco", "costco wholesale", "costco wholesale corp"}),
             "US22160K1051", "22160K105"),
    "HD":   (frozenset({"home depot", "the home depot", "home depot inc"}),
             "US4370761029", "437076102"),
    "NKE":  (frozenset({"nike", "nike inc"}),
             "US6541061031", "654106103"),
    "SBUX": (frozenset({"starbucks", "starbucks corp", "starbucks corporation"}),
             "US8552441094", "855244109"),
    "TGT":  (frozenset({"target", "target corp", "target corporation"}),
             "US8745371025", "874537102"),
    "AMZN": (frozenset({"amazon", "amazon.com", "amazon com", "amazon inc"}),
             "US0231351067", "023135106"),

    # ── Industrial / energy ──────────────────────────────────────────────────
    "XOM":  (frozenset({"exxon", "exxonmobil", "exxon mobil", "exxon mobil corporation"}),
             "US30231G1022", "30231G102"),
    "CVX":  (frozenset({"chevron", "chevron corp", "chevron corporation"}),
             "US1667641005", "166764100"),
    "BA":   (frozenset({"boeing", "boeing company", "the boeing company"}),
             "US0970231058", "097023105"),
    "CAT":  (frozenset({"caterpillar", "caterpillar inc"}),
             "US1491231015", "149123101"),
    "GE":   (frozenset({"general electric", "ge vernova", "ge aerospace"}),
             "US3696043013", "369604301"),
    "HON":  (frozenset({"honeywell", "honeywell international", "honeywell international inc"}),
             "US4385161066", "438516106"),
    "RTX":  (frozenset({"raytheon", "rtx", "raytheon technologies"}),
             "US75513E1010", "75513E101"),
    "UPS":  (frozenset({"ups", "united parcel service", "united parcel service inc"}),
             "US9113121068", "911312106"),
    "NEE":  (frozenset({"nextera", "nextera energy", "nextera energy inc"}),
             "US65339F1012", "65339F101"),
    "PG":   (frozenset({"procter & gamble", "procter and gamble", "p&g", "pg"}),
             "US7427181091", "742718109"),

    # ── Communication / media ────────────────────────────────────────────────
    "T":    (frozenset({"at&t", "att", "at&t inc"}),
             "US00206R1023", "00206R102"),
    "VZ":   (frozenset({"verizon", "verizon communications", "verizon communications inc"}),
             "US92343V1044", "92343V104"),
    "NFLX": (frozenset({"netflix", "netflix inc"}),
             "US64110L1061", "64110L106"),
    "DIS":  (frozenset({"disney", "the walt disney company", "walt disney", "disney inc"}),
             "US2546871060", "254687106"),
    "CMCSA":(frozenset({"comcast", "comcast corp", "comcast corporation"}),
             "US20212M1027", "20212M102"),

    # ── ETFs ─────────────────────────────────────────────────────────────────
    "SPY":  (frozenset({"s&p 500 etf", "spdr s&p 500", "spdr s&p 500 etf trust", "sp500 etf"}),
             "US78462F1030", "78462F103"),
    "QQQ":  (frozenset({"nasdaq etf", "invesco qqq", "qqq trust", "nasdaq 100 etf"}),
             "US46090E1038", "46090E103"),
    "IWM":  (frozenset({"russell 2000 etf", "ishares russell 2000", "small cap etf"}),
             "US4642876555", "464287655"),
    "GLD":  (frozenset({"gold etf", "spdr gold shares", "gold trust"}),
             "US78463V1070", "78463V107"),
    "TLT":  (frozenset({"treasury etf", "20+ year treasury", "long bond etf", "ishares 20+ year treasury"}),
             "US4642874329", "464287432"),
    "VTI":  (frozenset({"total market etf", "vanguard total market", "vanguard total stock market"}),
             "US9229087690", "922908769"),
    "EEM":  (frozenset({"emerging markets etf", "ishares emerging markets", "msci emerging markets"}),
             "US4642872349", "464287234"),

    # ── Commodities / FX (treat as synthetic tickers) ────────────────────────
    "XAUUSD": (frozenset({"gold", "xau", "xau/usd", "spot gold", "gold spot"}),
               "", ""),
    "XAGUSD": (frozenset({"silver", "xag", "xag/usd", "spot silver"}),
               "", ""),
    "WTIUSD":  (frozenset({"wti", "crude oil", "wti crude", "light sweet crude", "cl"}),
                "", ""),
    "BRTUSD":  (frozenset({"brent", "brent crude", "brent oil", "co"}),
                "", ""),
    "NATGAS":  (frozenset({"natural gas", "ng", "nat gas", "natgas"}),
                "", ""),
    "EURUSD":  (frozenset({"eur/usd", "euro dollar", "eurodollar"}),
                "", ""),
    "GBPUSD":  (frozenset({"gbp/usd", "cable", "sterling"}),
                "", ""),
    "USDJPY":  (frozenset({"usd/jpy", "dollar yen", "dollar/yen"}),
                "", ""),
    "BTCUSD":  (frozenset({"bitcoin", "btc", "btc/usd", "xbt"}),
                "", ""),
    "ETHUSD":  (frozenset({"ethereum", "eth", "eth/usd"}),
                "", ""),

    # ── Indices ──────────────────────────────────────────────────────────────
    "SPX":   (frozenset({"s&p 500", "sp500", "s&p500", "spx", "standard & poor's 500"}),
              "", ""),
    "NDX":   (frozenset({"nasdaq 100", "nasdaq-100", "qqq index", "ndx", "nasdaq composite"}),
              "", ""),
    "DJI":   (frozenset({"dow jones", "dow", "djia", "dow jones industrial average"}),
              "", ""),
    "RUT":   (frozenset({"russell 2000", "small cap index", "rut"}),
              "", ""),
    "VIX":   (frozenset({"vix", "volatility index", "cboe vix", "fear index"}),
              "", ""),
    "TNX":   (frozenset({"10 year treasury", "10yr treasury", "10-year treasury yield", "tnx"}),
              "", ""),
    "TYX":   (frozenset({"30 year treasury", "30yr treasury", "30-year treasury yield", "tyx"}),
              "", ""),

    # ── Macro indicators ──────────────────────────────────────────────────────
    "FEDFUNDS": (frozenset({"fed funds rate", "federal funds rate", "fed rate", "ffr"}),
                 "", ""),
    "CPI":     (frozenset({"consumer price index", "inflation", "cpi rate"}),
                "", ""),
    "PCE":     (frozenset({"personal consumption expenditures", "pce deflator"}),
                "", ""),
    "GDP":     (frozenset({"gross domestic product", "gdp growth", "gdp rate"}),
                "", ""),
}

# ── Compile lookup tables ─────────────────────────────────────────────────────

_TICKER_MAP: dict[str, str] = {}   # any alias (lowercase) → canonical ticker
_ISIN_MAP:   dict[str, str] = {}   # ISIN (uppercase) → canonical ticker
_CUSIP_MAP:  dict[str, str] = {}   # CUSIP (uppercase, 9-char) → canonical ticker

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_CUSIP_RE = re.compile(r"^[0-9A-Z]{8,9}$")


def _build_tables() -> None:
    for canonical, (aliases, isin, cusip) in _EQUITY_DATA.items():
        _TICKER_MAP[canonical.lower()] = canonical
        for alias in aliases:
            _TICKER_MAP[alias.lower()] = canonical
        if isin:
            _ISIN_MAP[isin.upper()] = canonical
        if cusip:
            # Store both 8-char and 9-char forms (some vendors drop check digit)
            c = cusip.replace("-", "").upper()
            _CUSIP_MAP[c] = canonical
            if len(c) == 9:
                _CUSIP_MAP[c[:8]] = canonical


_build_tables()


def _load_overrides() -> None:
    """Apply any extra aliases from AGENTMEM_ENTITY_OVERRIDES JSON file."""
    path = os.environ.get("AGENTMEM_ENTITY_OVERRIDES")
    if not path:
        return
    try:
        with open(path) as fh:
            overrides: dict[str, list[str]] = json.load(fh)
        for canonical, aliases in overrides.items():
            can = canonical.upper()
            _TICKER_MAP[can.lower()] = can
            for alias in aliases:
                _TICKER_MAP[alias.lower()] = can
        logger.info("Loaded %d entity overrides from %s", len(overrides), path)
    except Exception as exc:
        logger.warning("Could not load entity overrides from %s: %s", path, exc)


_load_overrides()


# ── Public interface ──────────────────────────────────────────────────────────

def normalize_ticker(raw: str) -> str:
    """
    Return the canonical ticker for *raw*, or ``raw.upper()`` if unknown.

    Handles: known aliases (case-insensitive), ISIN (12-char), CUSIP (9-char).
    """
    s = raw.strip()

    # ISIN check (e.g. US0378331005)
    upper = s.upper()
    if _ISIN_RE.match(upper):
        if upper in _ISIN_MAP:
            return _ISIN_MAP[upper]

    # CUSIP check (e.g. 037833100)
    no_dash = upper.replace("-", "")
    if _CUSIP_RE.match(no_dash):
        if no_dash in _CUSIP_MAP:
            return _CUSIP_MAP[no_dash]
        if len(no_dash) == 9 and no_dash[:8] in _CUSIP_MAP:
            return _CUSIP_MAP[no_dash[:8]]

    # Name / alias lookup
    lower = s.lower()
    if lower in _TICKER_MAP:
        return _TICKER_MAP[lower]

    return upper


def normalize_entity_value(key: str, value: str) -> str:
    """
    Normalize *value* according to which metadata *key* it belongs to.

    Keys "ticker", "entity", "isin", "cusip" all route through the ticker
    normalizer so different representations of the same security produce
    identical canonical strings.  All other keys are returned unchanged
    (after stripping whitespace).
    """
    if not isinstance(value, str):
        value = str(value)
    if key in ("ticker", "entity", "isin", "cusip", "instrument"):
        return normalize_ticker(value)
    return value.strip()


@lru_cache(maxsize=4096)
def cached_normalize(key: str, value: str) -> str:
    """LRU-cached wrapper around normalize_entity_value for hot paths."""
    return normalize_entity_value(key, value)
