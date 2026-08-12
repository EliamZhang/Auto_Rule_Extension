import sys, json, pandas as pd, re, os, csv
sys.stdout.reconfigure(encoding='utf-8')

OUT = 'reviews/2026-08-11_1728'

def clean_text(s):
    s = str(s).upper()
    s = re.sub(r'[^A-Z0-9 ]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# Load gap data
with open(f'{OUT}/gap_summary.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

all_patterns = []
for engine, patterns in data['engines'].items():
    for p in patterns:
        p['assigned_engine'] = engine
        all_patterns.append(p)

# Load KB
kb = pd.read_csv('raw/initial_rule/merchant_kb.csv', encoding='utf-8-sig')
kb_names_lower = kb['merchant_name'].str.strip().str.lower().tolist()
kb_names_exact = kb['merchant_name'].str.strip().tolist()

# =====================================================
# Build BRAND_MAP for known brand → KB merchant_name lookup
# =====================================================
BRAND_TO_KB_NAME = {
    'WOOLWORTHS': 'Woolworths', 'MCDONALDS': "McDonald's",
    'BIG W': 'Big W', 'BP ': 'BP', 'BP AUSTRALIA': 'BP',
    'TELSTRA PREPAID': 'Telstra', 'TELSTRA': 'Telstra',
    'OPTUS BILLING': 'Optus', 'OPTUS': 'Optus',
    'DIDIMOBILITY': 'DiDi', 'NEDS': 'Neds', 'KFC': 'KFC',
    'KMART': 'Kmart', '7-ELEVEN': '7-Eleven', '7 ELEVEN': '7-Eleven',
    'RITCHIES': 'Ritchies', 'OTR': 'OTR',
    'PRIME VIDE': 'Prime Video', 'AMZNPRIMEA': 'Prime Video',
    'NETFLIX': 'Netflix',
    'BET365': 'Bet365', 'BET 365': 'Bet365',
    'PLAYTKA': 'PLAYTKA Caesars Slots',
    'VEGASTARS': 'Vegastars', 'POINTSBET': 'PointsBet',
    'SURGE AU': 'SURGE AU', 'CASINY': 'CASINY',
    'TAB LIMITED': 'Tabcorp',
    'TRANSPORTFORNSW': 'Transport for NSW',
    'TFNSW OPAL': 'Transport for NSW',
    'GOODLIFE': 'Goodlife Health Clubs',
    'OURPROPERTYSA': 'OurProperty',
    'COLES': 'Coles', 'IGA': 'IGA',
    'COMPASS GROUP': 'Compass Group',
    'PALMER BOOKMAKING': 'Palmerbet',
    'ZIP': 'Zip', 'REVOLUT': 'Revolut',
    'SOLO SMITHFIELD': 'Solo',
    'LIBERTY SMITHFIELD': 'Liberty',
    'UNITED TINTINARA': 'United Petroleum',
    'UNITED ASTRON': 'United Petroleum',
    'COCACOLAEPP': 'Coca Cola Amatil',
    'GLENGALA HTL': 'Glengala Hotel',
    'THE DEMO CLUB': 'The Demo Club',
    'BLUE HIPPO': 'Blue Hippo Corp',
    'G2A': 'G2A.com',
    'BACKYARD SUPERMARKET': 'Backyard Supermarket',
    'HASTINGS D': 'Hastings D',
    'MEKONG DELTA': 'Mekong Delta Corporation',
    'BH IGA FRESH': 'IGA',
    'MARYBOROUGH HIGHLAND': 'Maryborough Highland Society',
    'SELF SERVICE MARKETS': 'Self Service Markets',
    'SELFSERVICE MARKETS': 'Self Service Markets',
    'LAVERTON SUPERMARKE': 'Laverton Supermarket',
    'WOOLWORTHS/': 'Woolworths',
}

# =====================================================
# 1. Generate all_other_credit_candidates.csv
# =====================================================
aoc_candidates = [
    {
        'rule_type': 'keyword',
        'pattern': 'INTEREST CREDIT',
        'required_terms': '',
        'status': '☐ confirm',
        'hit_count': 8,
        'risk_level': '低',
        'illion_category': 'nan',
        'samples': 'INTEREST CREDIT',
        'notes': '利息入账。现有规则 CREDIT INTEREST 词序不同无法匹配。',
    },
    {
        'rule_type': 'keyword',
        'pattern': 'OSKO PAYMENT RECEIVED',
        'required_terms': '',
        'status': '☐ confirm',
        'hit_count': 19,
        'risk_level': '低',
        'illion_category': 'All Other Credits',
        'samples': 'OSKO PAYMENT RECEIVED / LEO BLENNERHASSETT',
        'notes': '现有规则 OSKO PAYMENT RECEIVED / FOOD 太具体，无法覆盖其他人名变体。',
    },
    {
        'rule_type': 'keyword',
        'pattern': 'PLAT CASH BACK',
        'required_terms': '',
        'status': '☐ confirm',
        'hit_count': 8,
        'risk_level': '中 ⚠',
        'illion_category': 'nan',
        'samples': 'MISCELLANEOUS CREDIT DEBIT PLAT CASH BACK',
        'notes': 'Platinum 卡返现入账。只匹配 PLAT CASH BACK 避免过于宽泛。',
    },
]

aoc_df = pd.DataFrame(aoc_candidates)
aoc_df.to_csv(f'{OUT}/all_other_credit_candidates.csv', index=False, encoding='utf-8-sig')
print(f"✓ all_other_credit_candidates.csv: {len(aoc_df)} rules")

# =====================================================
# 2. Generate fee_candidates.csv
# =====================================================
# Note: ATM OPERATOR FEE already covered by existing rule at priority 38
# REVERSAL OF ACCOUNT SERVICING FEE is a reversal (credit), not a fee
# No new fee rules needed
fee_candidates = []

# Actually, let me add a candidate note about why no fee rules
# Create empty CSV with proper schema
fee_df = pd.DataFrame(columns=['priority','rule_name','category','pattern','counterparty','match_type','zero_amount_reject','description','status','hit_count','risk_level','illion_category','samples'])
fee_df.to_csv(f'{OUT}/fee_candidates.csv', index=False, encoding='utf-8-sig')
print(f"✓ fee_candidates.csv: 0 rules (existing rules already cover ATM OPERATOR FEE)")

# =====================================================
# 3. Generate liability_candidates.csv
# =====================================================
liability_candidates = [
    {
        'target_file': 'counterparty_keyword_rules.csv',
        'keyword': 'PAYMENT RECEIVED THANK YOU',
        'counterparty': 'CBA Credit Card',
        'product_type': 'bank',
        'match_type': 'keyword',
        'status': '☐ confirm',
        'hit_count': 46,
        'risk_level': '低',
        'illion_category': 'Credit Card Repayments',
        'samples': 'PAYMENT RECEIVED THANK YOU',
        'notes': 'CBA 信用卡还款确认消息。product_type=bank 映射到 Credit Card Repayments。',
    },
]

liability_df = pd.DataFrame(liability_candidates)
liability_df.to_csv(f'{OUT}/liability_candidates.csv', index=False, encoding='utf-8-sig')
print(f"✓ liability_candidates.csv: {len(liability_df)} rules")

# =====================================================
# 4. Generate transfer_candidates.csv
# =====================================================
transfer_candidates = [
    {
        'target_file': 'transfer_counterparty_rules.csv',
        'keyword': 'GEORGIA E ROYDS;GEORGIA ELLEN ROYDS;GEORGIA ROYDS G;GEORGIA ROYDS',
        'counterparty': 'Georgia Royds',
        'match_type': 'keyword',
        'status': '☐ confirm',
        'hit_count': 253,
        'risk_level': '低',
        'illion_category': 'External Transfers',
        'samples': 'GEORGIA E ROYDS; GEORGIA ROYDS G; GEORGIA ELLEN ROYDS',
        'notes': '4 个名字变体共 253 笔外部转账。子串匹配，GEORGIA ROYDS 作为兜底覆盖所有变体。',
    },
]

transfer_df = pd.DataFrame(transfer_candidates)
transfer_df.to_csv(f'{OUT}/transfer_candidates.csv', index=False, encoding='utf-8-sig')
print(f"✓ transfer_candidates.csv: {len(transfer_df)} rules")

# =====================================================
# 5. Generate initial_candidates.csv (NEW merchants)
# =====================================================
initial_new_candidates = [
    {
        'merchant_name': 'Boxhill Fruit World',
        'keywords': 'BOXHILL FRUIT WORLD',
        'category': 'Groceries',
        'link': '',
        'category_source': 'illion',
        'keyword_updated_at': '',
        'category_updated_at': '',
        'status': '☐ confirm',
        'hit_count': 9,
        'risk_level': '低',
        'illion_category': 'Groceries',
        'samples': 'BOXHILL FRUIT WORLD MARSDEN PARK NS AUS CARD XX2793 VALUE DATE:',
        'notes': '悉尼 Marsden Park 水果店。illion third_party: Miscellaneous Fresh Produce。',
    },
    {
        'merchant_name': 'Bluebells FDC',
        'keywords': 'BLUEBELLSFDC|BLUEBELLS FDC|RED BLUEBELLS FDC',
        'category': 'Education',
        'link': '',
        'category_source': 'illion',
        'keyword_updated_at': '',
        'category_updated_at': '',
        'status': '☐ confirm',
        'hit_count': 10,
        'risk_level': '低',
        'illion_category': 'Education',
        'samples': 'DEBIT CARD PURCHASE RED*BLUEBELLSFDC CHERMSIDE AUS',
        'notes': 'RED 支付平台的托儿服务 (Family Day Care)。illion third_party: redPAY Childcare。',
    },
]

initial_new_df = pd.DataFrame(initial_new_candidates)
initial_new_df.to_csv(f'{OUT}/initial_candidates.csv', index=False, encoding='utf-8-sig')
print(f"✓ initial_candidates.csv: {len(initial_new_df)} new merchants")

# =====================================================
# 6. Generate initial_keyword_updates.csv (keyword variants for EXISTING merchants)
# =====================================================
# For each initial_existing pattern, extract missing keyword variants

def extract_keyword_from_sample(sample_text, merchant_indicator):
    """Extract a clean_text keyword from a sample, focusing on the merchant portion"""
    ct = clean_text(sample_text)
    # Remove common prefixes
    prefixes = [
        'VISA DEBIT PURCHASE CARD ', 'DEBIT CARD PURCHASE ',
        'MISCELLANEOUS DEBIT V', 'EFTPOS DEBIT ', 'EFTPOS ',
        'WITHDRAWAL ', 'VISA DEBIT PURCHASE CARD',
    ]
    for prefix in prefixes:
        if prefix in ct:
            ct = ct[ct.index(prefix) + len(prefix):]
        elif ct.startswith(prefix):
            ct = ct[len(prefix):]
    # Remove card numbers like V7631, V9924 followed by date
    ct = re.sub(r'V\d{4}\s+\d{2}/\d{2}\s+', '', ct)
    # Remove trailing card info
    ct = re.sub(r'\s+CARD\s+XX\d{4}\s+VALUE\s+DATE.*$', '', ct)
    ct = re.sub(r'\s+AUS\s+CARD\s+XX\d{4}.*$', '', ct)
    ct = re.sub(r'\s+AU\s+AUS\s+CARD\s+XX\d{4}.*$', '', ct)
    ct = re.sub(r'\s+AU\s+CARD\s+XX\d{4}.*$', '', ct)
    ct = re.sub(r'\s+CARD\s+XX\d{4}.*$', '', ct)
    # Remove location suffixes
    ct = re.sub(r'\s+\d{5,}.*$', '', ct)
    return ct.strip()

# Process the initial_existing patterns
keyword_updates = []
processed_merchants = set()

# Manually defined keyword additions for the most impactful patterns
manual_updates = [
    # High-impact Woolworths store variants
    ('Woolworths', 'WOOLWORTHS 2692 RUNAWAY BAY QL|WOOLWORTHS RUNAWAY BAY', 29, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS 3163 CARLTON VI', 25, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS 3149 RINGWOOD VI', 24, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS 5945 MUNNO PARA W SA', 22, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS 5611 SALISBURY SA', 14, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS CNR BAYVIEW ST RUNAWAY BAY', 16, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS LAKE RD GLENDALE', 18, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS WANTIRNA STH VI', 10, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS SCHOFIELDS NS', 11, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS BLAKEVIEW SA', 10, 'Groceries'),
    ('Woolworths', 'WOOLWORTHS KEPERRA QL', 13, 'Groceries'),

    # McDonald's variants
    ("McDonald's", 'MCDONALDS WAGGA GFIL WAGGA WAGG', 59, 'Dining Out'),
    ("McDonald's", 'MCDONALDS WAGGA WAGG WAGGA WAGG', 18, 'Dining Out'),
    ("McDonald's", 'MCDONALDS RIVERDALE TARNEIT VIC', 19, 'Dining Out'),
    ("McDonald's", 'MCDONALDS 951027 BIGGERA WATER', 15, 'Dining Out'),
    ("McDonald's", 'MCDONALDS PARRA LVL1 PARRAMATTA NSW', 12, 'Dining Out'),
    ("McDonald's", 'MCDONALDS HARBOURTOW BIGGERA WATER', 10, 'Dining Out'),

    # BP variants
    ('BP', 'BP KAMBALDA KAMBALDA EAST', 70, 'Automotive'),
    ('BP', 'BP LAVERTON LAVERTON WA', 56, 'Automotive'),
    ('BP', 'BP AUSTRALIA PTY LTD DOCKLANDS', 29, 'Automotive'),
    ('BP', 'BP LAVERTON LAVERTON', 17, 'Automotive'),

    # Big W variants
    ('Big W', 'BIG W SALISBURY SA', 66, 'Department Stores'),

    # Transport for NSW
    ('Transport for NSW', 'TRANSPORTFORNSW TAP SYDNEY', 75, 'Transport'),
    ('Transport for NSW', 'TRANSPORTFORNSW OPAL CHIPPENDALE', 21, 'Transport'),
    ('Transport for NSW', 'TFNSW OPAL MACHINE SYDNEY', 50, 'Transport'),

    # DiDi
    ('DiDi', 'DIDIMOBILITY SYDNEY', 57, 'Transport'),

    # OTR
    ('OTR', 'OTR ST MARYS ST MARYS', 29, 'Automotive'),

    # Ritchies
    ('Ritchies', 'RITCHIES RINGWOOD RINGWOOD NOR', 16, 'Groceries'),
    ('Ritchies', 'RITCHIES ROBINVALE', 14, 'Groceries'),

    # Kmart
    ('Kmart', 'KMART WARATAH', 19, 'Department Stores'),
    ('Kmart', 'KMART LIVERPOOL', 17, 'Department Stores'),

    # Telstra
    ('Telstra', 'TELSTRA PREPAID MELBOURNE', 33, 'Telecommunications'),

    # Optus
    ('Optus', 'OPTUS BILLING MACQUARIEPARK', 15, 'Telecommunications'),

    # Prime Video
    ('Prime Video', 'PRIME VIDE SYDNEY', 20, 'Subscription TV'),
    ('Prime Video', 'PRIME VIDE PRIMEVIDEO SYDNEY', 15, 'Subscription TV'),

    # KFC
    ('KFC', 'KFC RUNAWAY BAY', 11, 'Dining Out'),

    # Liberty
    ('Liberty', 'LIBERTY SMITHFIELD SMITHFIELD PL SA', 11, 'Automotive'),

    # Goodlife
    ('Goodlife Health Clubs', 'GOODLIFE COOMERA', 17, 'Gyms and other memberships'),

    # OurProperty
    ('OurProperty', 'OURPROPERTYSA ADELAIDE', 17, 'Rent'),

    # Compass Group
    ('Compass Group', 'COMPASS GROUP', 33, 'Dining Out'),

    # Palmerbet
    ('Palmerbet', 'PALMER BOOKMAKING PTY CARINGHBAH', 33, 'Gambling'),

    # IGA
    ('IGA', 'BH IGA FRESH BROKEN HILL', 32, 'Groceries'),

    # Three Hungry Birds
    ('Three Hungry Birds', 'SQ THREE HUNGRY BIR DSDERRIMUT|THREE HUNGRY BIRDS DERRIMUT', 24, 'Dining Out'),

    # Solo
    ('Solo', 'SOLO SMITHFIELD SMITHFIELD PL SA', 26, 'Groceries'),

    # United Petroleum
    ('United Petroleum', 'UNITED TINTINARA TINTINARA', 17, 'Automotive'),
    ('United Petroleum', 'UNITED ASTRON HASTINGS VIC', 26, 'Automotive'),

    # Revolut
    ('Revolut', 'REVOLUT', 56, 'nan'),

    # Hastings D (pharmacy/health)
    ('Hastings D', 'HASTINGS D 40 HIGH STREET HASTINGS', 16, 'Health'),

    # Backyard Supermarket
    ('Backyard Supermarket', 'BACKYARD SUPERMARKET FOOTSCRAY', 13, 'Groceries'),

    # 7-Eleven
    ('7-Eleven', '7 ELEVEN GAYTHORNE QL', 13, 'Groceries'),
    ('7-Eleven', '7 ELEVEN ST MARYS', 10, 'Groceries'),
    ('7-Eleven', '7 ELEVEN MAMBOURIN', 9, 'Groceries'),

    # Coca Cola
    ('Coca Cola Amatil', 'COCACOLAEPP LIVERPOOL', 19, 'Groceries'),

    # World Gym
    ('World Gym', 'WORLD GYM IPSWICH SPRINGWOOD', 14, 'Gyms and other memberships'),

    # Zip
    ('Zip', 'ZIP', 16, 'nan'),

    # Netflix
    ('Netflix', 'NETFLIX COM MELBOURNE', 13, 'Subscription TV'),

    # SPER QLD Treasury (government fine)
    # This is a new merchant
]

# Write keyword updates
updates_rows = []
for merchant_name, new_keywords, hit_count, illion_cat in manual_updates:
    # Find exact merchant_name in KB
    mask = kb['merchant_name'].str.strip().str.lower() == merchant_name.lower()
    if mask.any():
        idx = kb[mask].index[0]
        existing_kws = kb.at[idx, 'keywords'] if pd.notna(kb.at[idx, 'keywords']) else ''
        category = kb.at[idx, 'category'] if pd.notna(kb.at[idx, 'category']) else ''
        updates_rows.append({
            'merchant_name': kb.at[idx, 'merchant_name'],
            'existing_keywords': existing_kws,
            'new_keyword': new_keywords,
            'category': category,
            'illion_category': illion_cat,
            'hit_count': hit_count,
            'samples': '',
            'status': '☐ confirm',
        })

updates_df = pd.DataFrame(updates_rows)
# Deduplicate
updates_df = updates_df.drop_duplicates(subset=['merchant_name', 'new_keyword'])
updates_df.to_csv(f'{OUT}/initial_keyword_updates.csv', index=False, encoding='utf-8-sig')
print(f"✓ initial_keyword_updates.csv: {len(updates_df)} keyword additions for {len(updates_df['merchant_name'].unique())} merchants")

# =====================================================
# 7. Generate catch_all_candidates.csv
# =====================================================
# All catch_all patterns either reassigned to proper engines or skipped
catch_all_df = pd.DataFrame(columns=['rule_name','category','pattern','match_type','confidence','status','hit_count','risk_level','illion_category','samples'])
catch_all_df.to_csv(f'{OUT}/catch_all_candidates.csv', index=False, encoding='utf-8-sig')
print(f"✓ catch_all_candidates.csv: 0 rules (all patterns reassigned to proper engines or skipped)")

# =====================================================
# Summary
# =====================================================
print("\n" + "=" * 60)
print("CANDIDATE RULES SUMMARY")
print("=" * 60)
print(f"  fee:                      0 rules")
print(f"  all_other_credit:         {len(aoc_df)} rules (+{sum(r['hit_count'] for r in aoc_candidates)} hits)")
print(f"  liability:                {len(liability_df)} rules (+{sum(r['hit_count'] for r in liability_candidates)} hits)")
print(f"  transfer (counterparty):  {len(transfer_df)} rules (+{sum(r['hit_count'] for r in transfer_candidates)} hits)")
print(f"  initial (new merchants):  {len(initial_new_df)} rules (+{sum(r['hit_count'] for r in initial_new_candidates)} hits)")
print(f"  initial (keyword updates): {len(updates_df)} additions for {len(updates_df['merchant_name'].unique())} merchants")
print(f"  catch_all:                0 rules")
print(f"  TOTAL:                    {len(aoc_df) + len(liability_df) + len(transfer_df) + len(initial_new_df) + len(updates_df)} entries")
