from dataclasses import dataclass
from enum import StrEnum


class Exchange(StrEnum):
    NSE_CM = "nse_cm"     # NSE cash market
    BSE_CM = "bse_cm"     # BSE cash market
    NSE_FO = "nse_fo"     # NSE F&O
    BSE_FO = "bse_fo"     # BSE F&O


class Segment(StrEnum):
    EQUITY = "equity"
    FNO = "fno"
    PENNY = "penny"
    INDEX = "index"


@dataclass(frozen=True)
class Instrument:
    symbol: str           # NSE trading symbol
    name: str             # Full company name
    exchange: Exchange
    segment: Segment
    sector: str
    lot_size: int = 1     # For F&O; 1 for equity


# ── Nifty 50 ──────────────────────────────────────────────────────────────────
NIFTY_50: list[Instrument] = [
    Instrument("RELIANCE",    "Reliance Industries",       Exchange.NSE_CM, Segment.EQUITY, "Energy"),
    Instrument("TCS",         "Tata Consultancy Services", Exchange.NSE_CM, Segment.EQUITY, "IT"),
    Instrument("HDFCBANK",    "HDFC Bank",                 Exchange.NSE_CM, Segment.EQUITY, "Banking"),
    Instrument("BHARTIARTL",  "Bharti Airtel",             Exchange.NSE_CM, Segment.EQUITY, "Telecom"),
    Instrument("ICICIBANK",   "ICICI Bank",                Exchange.NSE_CM, Segment.EQUITY, "Banking"),
    Instrument("INFOSYS",     "Infosys",                   Exchange.NSE_CM, Segment.EQUITY, "IT"),
    Instrument("SBIN",        "State Bank of India",       Exchange.NSE_CM, Segment.EQUITY, "Banking"),
    Instrument("HINDUNILVR",  "Hindustan Unilever",        Exchange.NSE_CM, Segment.EQUITY, "FMCG"),
    Instrument("ITC",         "ITC Limited",               Exchange.NSE_CM, Segment.EQUITY, "FMCG"),
    Instrument("LT",          "Larsen & Toubro",           Exchange.NSE_CM, Segment.EQUITY, "Capital Goods"),
    Instrument("KOTAKBANK",   "Kotak Mahindra Bank",       Exchange.NSE_CM, Segment.EQUITY, "Banking"),
    Instrument("AXISBANK",    "Axis Bank",                 Exchange.NSE_CM, Segment.EQUITY, "Banking"),
    Instrument("BAJFINANCE",  "Bajaj Finance",             Exchange.NSE_CM, Segment.EQUITY, "NBFC"),
    Instrument("MARUTI",      "Maruti Suzuki",             Exchange.NSE_CM, Segment.EQUITY, "Auto"),
    Instrument("SUNPHARMA",   "Sun Pharmaceutical",        Exchange.NSE_CM, Segment.EQUITY, "Pharma"),
    Instrument("TATAMOTORS",  "Tata Motors",               Exchange.NSE_CM, Segment.EQUITY, "Auto"),
    Instrument("WIPRO",       "Wipro",                     Exchange.NSE_CM, Segment.EQUITY, "IT"),
    Instrument("HCLTECH",     "HCL Technologies",          Exchange.NSE_CM, Segment.EQUITY, "IT"),
    Instrument("ADANIENT",    "Adani Enterprises",         Exchange.NSE_CM, Segment.EQUITY, "Conglomerate"),
    Instrument("ADANIPORTS",  "Adani Ports & SEZ",         Exchange.NSE_CM, Segment.EQUITY, "Infrastructure"),
    Instrument("ULTRACEMCO",  "UltraTech Cement",          Exchange.NSE_CM, Segment.EQUITY, "Cement"),
    Instrument("JSWSTEEL",    "JSW Steel",                 Exchange.NSE_CM, Segment.EQUITY, "Metals"),
    Instrument("TATASTEEL",   "Tata Steel",                Exchange.NSE_CM, Segment.EQUITY, "Metals"),
    Instrument("NTPC",        "NTPC",                      Exchange.NSE_CM, Segment.EQUITY, "Power"),
    Instrument("POWERGRID",   "Power Grid Corporation",    Exchange.NSE_CM, Segment.EQUITY, "Power"),
    Instrument("ONGC",        "ONGC",                      Exchange.NSE_CM, Segment.EQUITY, "Oil & Gas"),
    Instrument("COALINDIA",   "Coal India",                Exchange.NSE_CM, Segment.EQUITY, "Mining"),
    Instrument("BAJAJFINSV",  "Bajaj Finserv",             Exchange.NSE_CM, Segment.EQUITY, "NBFC"),
    Instrument("M&M",         "Mahindra & Mahindra",       Exchange.NSE_CM, Segment.EQUITY, "Auto"),
    Instrument("TITAN",       "Titan Company",             Exchange.NSE_CM, Segment.EQUITY, "Consumer"),
    Instrument("ASIANPAINT",  "Asian Paints",              Exchange.NSE_CM, Segment.EQUITY, "Consumer"),
    Instrument("NESTLEIND",   "Nestle India",              Exchange.NSE_CM, Segment.EQUITY, "FMCG"),
    Instrument("DRREDDY",     "Dr Reddy's Laboratories",   Exchange.NSE_CM, Segment.EQUITY, "Pharma"),
    Instrument("CIPLA",       "Cipla",                     Exchange.NSE_CM, Segment.EQUITY, "Pharma"),
    Instrument("DIVISLAB",    "Divi's Laboratories",       Exchange.NSE_CM, Segment.EQUITY, "Pharma"),
    Instrument("TECHM",       "Tech Mahindra",             Exchange.NSE_CM, Segment.EQUITY, "IT"),
    Instrument("APOLLOHOSP",  "Apollo Hospitals",          Exchange.NSE_CM, Segment.EQUITY, "Healthcare"),
    Instrument("GRASIM",      "Grasim Industries",         Exchange.NSE_CM, Segment.EQUITY, "Cement"),
    Instrument("SHRIRAMFIN",  "Shriram Finance",           Exchange.NSE_CM, Segment.EQUITY, "NBFC"),
    Instrument("TATACONSUM",  "Tata Consumer Products",    Exchange.NSE_CM, Segment.EQUITY, "FMCG"),
    Instrument("EICHERMOT",   "Eicher Motors",             Exchange.NSE_CM, Segment.EQUITY, "Auto"),
    Instrument("HEROMOTOCO",  "Hero MotoCorp",             Exchange.NSE_CM, Segment.EQUITY, "Auto"),
    Instrument("BPCL",        "Bharat Petroleum",          Exchange.NSE_CM, Segment.EQUITY, "Oil & Gas"),
    Instrument("INDUSINDBK",  "IndusInd Bank",             Exchange.NSE_CM, Segment.EQUITY, "Banking"),
    Instrument("HINDALCO",    "Hindalco Industries",       Exchange.NSE_CM, Segment.EQUITY, "Metals"),
    Instrument("VEDL",        "Vedanta",                   Exchange.NSE_CM, Segment.EQUITY, "Metals"),
    Instrument("BEL",         "Bharat Electronics",        Exchange.NSE_CM, Segment.EQUITY, "Defence"),
    Instrument("HDFCLIFE",    "HDFC Life Insurance",       Exchange.NSE_CM, Segment.EQUITY, "Insurance"),
    Instrument("SBILIFE",     "SBI Life Insurance",        Exchange.NSE_CM, Segment.EQUITY, "Insurance"),
    Instrument("HAL",         "Hindustan Aeronautics",     Exchange.NSE_CM, Segment.EQUITY, "Defence"),
]

# ── Additional Nifty Next 50 / Midcap picks to reach ~200 universe ─────────────
_NIFTY_NEXT_150: list[Instrument] = [
    # IT
    Instrument("MPHASIS",     "Mphasis",                   Exchange.NSE_CM, Segment.EQUITY, "IT"),
    Instrument("LTIM",        "LTIMindtree",               Exchange.NSE_CM, Segment.EQUITY, "IT"),
    Instrument("PERSISTENT",  "Persistent Systems",        Exchange.NSE_CM, Segment.EQUITY, "IT"),
    Instrument("COFORGE",     "Coforge",                   Exchange.NSE_CM, Segment.EQUITY, "IT"),
    # Banking / Finance
    Instrument("FEDERALBNK",  "Federal Bank",              Exchange.NSE_CM, Segment.EQUITY, "Banking"),
    Instrument("BANDHANBNK",  "Bandhan Bank",              Exchange.NSE_CM, Segment.EQUITY, "Banking"),
    Instrument("IDFCFIRSTB",  "IDFC First Bank",           Exchange.NSE_CM, Segment.EQUITY, "Banking"),
    Instrument("CHOLAFIN",    "Cholamandalam Finance",     Exchange.NSE_CM, Segment.EQUITY, "NBFC"),
    Instrument("LICHSGFIN",   "LIC Housing Finance",       Exchange.NSE_CM, Segment.EQUITY, "NBFC"),
    # Auto / Auto-ancillary
    Instrument("BAJAJ-AUTO",  "Bajaj Auto",                Exchange.NSE_CM, Segment.EQUITY, "Auto"),
    Instrument("TVSMOTOR",    "TVS Motor Company",         Exchange.NSE_CM, Segment.EQUITY, "Auto"),
    Instrument("BOSCHLTD",    "Bosch",                     Exchange.NSE_CM, Segment.EQUITY, "Auto Ancillary"),
    Instrument("MOTHERSON",   "Samvardhana Motherson",     Exchange.NSE_CM, Segment.EQUITY, "Auto Ancillary"),
    Instrument("BALKRISIND",  "Balkrishna Industries",     Exchange.NSE_CM, Segment.EQUITY, "Auto Ancillary"),
    # Pharma
    Instrument("TORNTPHARM", "Torrent Pharmaceuticals",    Exchange.NSE_CM, Segment.EQUITY, "Pharma"),
    Instrument("AUROPHARMA",  "Aurobindo Pharma",          Exchange.NSE_CM, Segment.EQUITY, "Pharma"),
    Instrument("BIOCON",      "Biocon",                    Exchange.NSE_CM, Segment.EQUITY, "Pharma"),
    Instrument("LUPIN",       "Lupin",                     Exchange.NSE_CM, Segment.EQUITY, "Pharma"),
    # FMCG / Consumer
    Instrument("DABUR",       "Dabur India",               Exchange.NSE_CM, Segment.EQUITY, "FMCG"),
    Instrument("MARICO",      "Marico",                    Exchange.NSE_CM, Segment.EQUITY, "FMCG"),
    Instrument("GODREJCP",    "Godrej Consumer Products",  Exchange.NSE_CM, Segment.EQUITY, "FMCG"),
    Instrument("COLPAL",      "Colgate-Palmolive India",   Exchange.NSE_CM, Segment.EQUITY, "FMCG"),
    Instrument("PIDILITIND",  "Pidilite Industries",       Exchange.NSE_CM, Segment.EQUITY, "Consumer"),
    Instrument("VOLTAS",      "Voltas",                    Exchange.NSE_CM, Segment.EQUITY, "Consumer"),
    # Infrastructure / Real Estate
    Instrument("DLF",         "DLF",                       Exchange.NSE_CM, Segment.EQUITY, "Real Estate"),
    Instrument("GODREJPROP",  "Godrej Properties",         Exchange.NSE_CM, Segment.EQUITY, "Real Estate"),
    Instrument("OBEROIRLTY",  "Oberoi Realty",             Exchange.NSE_CM, Segment.EQUITY, "Real Estate"),
    Instrument("IRCTC",       "Indian Railway Catering",   Exchange.NSE_CM, Segment.EQUITY, "Infrastructure"),
    Instrument("IRFC",        "Indian Railway Finance",    Exchange.NSE_CM, Segment.EQUITY, "Infrastructure"),
    # Metals / Mining
    Instrument("NMDC",        "NMDC",                      Exchange.NSE_CM, Segment.EQUITY, "Mining"),
    Instrument("SAIL",        "Steel Authority of India",  Exchange.NSE_CM, Segment.EQUITY, "Metals"),
    Instrument("NATIONALUM",  "National Aluminium",        Exchange.NSE_CM, Segment.EQUITY, "Metals"),
    # Energy / Power
    Instrument("TATAPOWER",   "Tata Power",                Exchange.NSE_CM, Segment.EQUITY, "Power"),
    Instrument("ADANIGREEN",  "Adani Green Energy",        Exchange.NSE_CM, Segment.EQUITY, "Renewable Energy"),
    Instrument("ADANIENSOL",  "Adani Energy Solutions",    Exchange.NSE_CM, Segment.EQUITY, "Power"),
    Instrument("TORNTPOWER",  "Torrent Power",             Exchange.NSE_CM, Segment.EQUITY, "Power"),
    Instrument("CESC",        "CESC",                      Exchange.NSE_CM, Segment.EQUITY, "Power"),
    # Defence
    Instrument("BHEL",        "Bharat Heavy Electricals",  Exchange.NSE_CM, Segment.EQUITY, "Capital Goods"),
    Instrument("COCHINSHIP",  "Cochin Shipyard",           Exchange.NSE_CM, Segment.EQUITY, "Defence"),
    Instrument("MAZDOCK",     "Mazagon Dock",              Exchange.NSE_CM, Segment.EQUITY, "Defence"),
    Instrument("GRSE",        "Garden Reach Shipbuilders", Exchange.NSE_CM, Segment.EQUITY, "Defence"),
    # Specialty Chemicals
    Instrument("SRF",         "SRF",                       Exchange.NSE_CM, Segment.EQUITY, "Chemicals"),
    Instrument("NAVINFLUOR",  "Navin Fluorine",            Exchange.NSE_CM, Segment.EQUITY, "Chemicals"),
    Instrument("FLUOROCHEM",  "Gujarat Fluorochemicals",   Exchange.NSE_CM, Segment.EQUITY, "Chemicals"),
    Instrument("AARTI",       "Aarti Industries",          Exchange.NSE_CM, Segment.EQUITY, "Chemicals"),
    # Hospitals / Healthcare
    Instrument("FORTIS",      "Fortis Healthcare",         Exchange.NSE_CM, Segment.EQUITY, "Healthcare"),
    Instrument("MAXHEALTH",   "Max Healthcare",            Exchange.NSE_CM, Segment.EQUITY, "Healthcare"),
    # Telecom
    Instrument("IDEA",        "Vodafone Idea",             Exchange.NSE_CM, Segment.EQUITY, "Telecom"),
    # Hotels / Tourism
    Instrument("INDHOTEL",    "Indian Hotels",             Exchange.NSE_CM, Segment.EQUITY, "Hotels"),
    Instrument("LEMONTREE",   "Lemon Tree Hotels",         Exchange.NSE_CM, Segment.EQUITY, "Hotels"),
    # Retail / E-commerce
    Instrument("DMART",       "Avenue Supermarts",         Exchange.NSE_CM, Segment.EQUITY, "Retail"),
    Instrument("TRENT",       "Trent",                     Exchange.NSE_CM, Segment.EQUITY, "Retail"),
    # Insurance
    Instrument("ICICIGI",     "ICICI Lombard",             Exchange.NSE_CM, Segment.EQUITY, "Insurance"),
    Instrument("ICICIPRULI",  "ICICI Prudential Life",     Exchange.NSE_CM, Segment.EQUITY, "Insurance"),
    Instrument("LICI",        "Life Insurance Corporation",Exchange.NSE_CM, Segment.EQUITY, "Insurance"),
]

NIFTY_200: list[Instrument] = NIFTY_50 + _NIFTY_NEXT_150

# ── F&O Tradeable Universe (liquid stocks with option chains) ──────────────────
FNO_UNIVERSE: list[str] = [
    # Indices
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    # Large cap
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFOSYS", "SBIN",
    "HINDUNILVR", "ITC", "LT", "KOTAKBANK", "AXISBANK", "BAJFINANCE",
    "MARUTI", "SUNPHARMA", "TATAMOTORS", "WIPRO", "HCLTECH",
    "ADANIENT", "ADANIPORTS", "ULTRACEMCO", "JSWSTEEL", "TATASTEEL",
    "NTPC", "POWERGRID", "ONGC", "COALINDIA", "BAJAJFINSV",
    "M&M", "TITAN", "ASIANPAINT", "DRREDDY", "CIPLA", "TECHM",
    "APOLLOHOSP", "GRASIM", "TATACONSUM", "EICHERMOT", "HEROMOTOCO",
    "BPCL", "INDUSINDBK", "HINDALCO", "VEDL", "BEL", "HAL",
    # Mid cap F&O
    "BAJAJ-AUTO", "TVSMOTOR", "DLF", "GODREJPROP", "IRCTC",
    "TATAPOWER", "ADANIGREEN", "SRF", "TORNTPHARM", "LUPIN",
    "AUROPHARMA", "BIOCON", "CHOLAFIN", "FEDERALBNK", "IDFCFIRSTB",
    "MPHASIS", "LTIM", "PERSISTENT", "COFORGE",
]

# ── NSE F&O Lot Sizes (as of May 2026 — verify before trading) ────────────────
FO_LOT_SIZES: dict[str, int] = {
    # Indices
    "NIFTY":       75,
    "BANKNIFTY":   30,
    "FINNIFTY":    65,
    "MIDCPNIFTY":  75,
    # Large caps
    "RELIANCE":    250,
    "TCS":         150,
    "HDFCBANK":    550,
    "ICICIBANK":   700,
    "INFOSYS":     300,
    "SBIN":        1500,
    "ITC":         3200,
    "LT":          150,
    "KOTAKBANK":   400,
    "AXISBANK":    625,
    "BAJFINANCE":  125,
    "MARUTI":      50,
    "SUNPHARMA":   350,
    "TATAMOTORS":  1100,
    "WIPRO":       1500,
    "HCLTECH":     350,
    "ADANIENT":    250,
    "ADANIPORTS":  1250,
    "ULTRACEMCO":  100,
    "JSWSTEEL":    675,
    "TATASTEEL":   3375,
    "NTPC":        3000,
    "POWERGRID":   2700,
    "ONGC":        1950,
    "COALINDIA":   1400,
    "BAJAJFINSV":  125,
    "M&M":         700,
    "TITAN":       375,
    "ASIANPAINT":  300,
    "DRREDDY":     125,
    "CIPLA":       650,
    "TECHM":       600,
    "APOLLOHOSP":  125,
    "GRASIM":      475,
    "TATACONSUM":  1350,
    "EICHERMOT":   200,
    "HEROMOTOCO":  300,
    "BPCL":        1800,
    "INDUSINDBK":  700,
    "HINDALCO":    2150,
    "VEDL":        2000,
    "BEL":         3700,
    "HAL":         150,
    # Mid cap
    "BAJAJ-AUTO":  250,
    "TVSMOTOR":    700,
    "DLF":         1650,
    "GODREJPROP":  325,
    "IRCTC":       1375,
    "TATAPOWER":   3375,
    "ADANIGREEN":  500,
    "SRF":         250,
    "TORNTPHARM":  150,
    "LUPIN":       500,
    "AUROPHARMA":  700,
    "BIOCON":      2900,
    "CHOLAFIN":    500,
    "FEDERALBNK":  5000,
    "IDFCFIRSTB":  7500,
    "MPHASIS":     175,
    "LTIM":        150,
    "PERSISTENT":  125,
    "COFORGE":     150,
}

# ── SME / Penny Watchlist (high-potential small caps) ─────────────────────────
# These are seeded manually; penny_stocks.py will auto-scan NSE SME platform
SME_WATCHLIST: list[str] = [
    # Defence & Aerospace SMEs
    "IDEAFORGE", "DRONAYUGA", "PARAS",
    # EV / Green Energy
    "GREENPANEL", "SAKSOFT",
    # Specialty Manufacturing
    "RPGLIFE", "TGBHOTELS",
    # Tech / SaaS SME
    "KPIGREEN", "WAAREEENER",
    # Textiles / Exports
    "GANECOS", "RUPA",
]

# ── Index tokens for Kotak Neo API ────────────────────────────────────────────
INDEX_TOKENS: dict[str, str] = {
    "NIFTY 50":      "26000",
    "NIFTY BANK":    "26009",
    "NIFTY IT":      "26003",
    "NIFTY PHARMA":  "26001",
    "NIFTY AUTO":    "26002",
    "NIFTY FMCG":    "26005",
    "NIFTY METAL":   "26015",
    "INDIA VIX":     "26017",
}

# ── Sector to PE benchmark mapping ────────────────────────────────────────────
SECTOR_PE_BENCHMARKS: dict[str, tuple[float, float]] = {
    "IT":               (25.0, 35.0),
    "FMCG":             (40.0, 55.0),
    "Banking":          (15.0, 25.0),
    "NBFC":             (20.0, 35.0),
    "Auto":             (18.0, 28.0),
    "Auto Ancillary":   (15.0, 25.0),
    "Pharma":           (20.0, 30.0),
    "Energy":           (10.0, 18.0),
    "Oil & Gas":        (10.0, 18.0),
    "Metals":           (8.0,  15.0),
    "Mining":           (8.0,  15.0),
    "Real Estate":      (20.0, 30.0),
    "Infrastructure":   (20.0, 35.0),
    "Power":            (15.0, 25.0),
    "Renewable Energy": (40.0, 80.0),
    "Telecom":          (15.0, 30.0),
    "Capital Goods":    (20.0, 35.0),
    "Cement":           (18.0, 28.0),
    "Chemicals":        (25.0, 45.0),
    "Healthcare":       (25.0, 45.0),
    "Insurance":        (20.0, 40.0),
    "Consumer":         (30.0, 50.0),
    "Hotels":           (25.0, 45.0),
    "Retail":           (40.0, 60.0),
    "Conglomerate":     (20.0, 35.0),
    "Defence":          (30.0, 60.0),
}
