import sys, json, pandas as pd, re, os, csv
sys.stdout.reconfigure(encoding='utf-8')

OUT = 'reviews/2026-08-11_1728'

# Load gap data
with open(f'{OUT}/gap_summary.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

all_patterns = []
for engine, patterns in data['engines'].items():
    for p in patterns:
        p['assigned_engine'] = engine
        all_patterns.append(p)

def clean_text(s):
    s = str(s).upper()
    s = re.sub(r'[^A-Z0-9 ]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# Load KB efficiently - only what we need
print("Loading KB...")
kb = pd.read_csv('raw/initial_rule/merchant_kb.csv', encoding='utf-8-sig',
                 usecols=['merchant_name', 'keywords', 'category'])
print(f"Loaded {len(kb)} merchants")

# Build keyword set for fast checking
kw_to_merchants = {}
for i, row in kb.iterrows():
    kws = str(row['keywords']).split('|') if pd.notna(row['keywords']) else []
    for kw in kws:
        kw_clean = clean_text(kw).strip()
        if len(kw_clean) >= 4:
            kw_to_merchants.setdefault(kw_clean, []).append(row['merchant_name'])

print(f"Built {len(kw_to_merchants)} keyword index")

def find_kb_matches_fast(pattern_norm_clean):
    """Check by extracting tokens and looking them up in keyword dict"""
    matches = []
    seen = set()
    # Check if any keyword is a substring of the pattern
    # This is optimized: only check keywords that start with tokens from the pattern
    tokens = pattern_norm_clean.split()
    # For 2-3 word combinations, check directly
    for i in range(len(tokens)):
        for j in range(i+1, min(i+4, len(tokens)+1)):
            phrase = ' '.join(tokens[i:j])
            if phrase in kw_to_merchants:
                for m in kw_to_merchants[phrase]:
                    if m not in seen:
                        seen.add(m)
                        matches.append(m)
    return matches

# Known brand mappings (from pattern text to likely KB merchant)
BRAND_MAP = {
    'WOOLWORTHS': 'Woolworths', 'MCDONALDS': "McDonald's", "MCDONALD'S": "McDonald's",
    'BIG W': 'Big W', 'BP ': 'BP', 'BP AUSTRALIA': 'BP',
    'TELSTRA PREPAID': 'Telstra', 'OPTUS BILLING': 'Optus',
    'DIDIMOBILITY': 'DiDi', 'NEDS': 'Neds', 'KFC': 'KFC', 'KMART': 'Kmart',
    '7 ELEVEN': '7-Eleven', '7-ELEVEN': '7-Eleven',
    'RITCHIES': 'Ritchies', 'OTR': 'OTR', 'PRIME VIDE': 'Prime Video',
    'NETFLIX': 'Netflix', 'BET365': 'Bet365', 'PLAYTKA': 'PLAYTKA Caesars Slots',
    'VEGASTARS': 'Vegastars', 'POINTSBET': 'PointsBet',
    'SURGE AU': 'SURGE AU', 'CASINY': 'CASINY',
    'TAB LIMITED': 'Tabcorp', 'TAB': 'Tabcorp',
    'TRANSPORTFORNSW': 'Transport for NSW', 'TFNSW': 'Transport for NSW',
    'GOODLIFE': 'Goodlife Health Clubs', 'OURPROPERTYSA': 'OurProperty',
    'COLES': 'Coles', 'WOOLWORTHS/': 'Woolworths',
    'IGA': 'IGA', 'COMPASS GROUP': 'Compass Group',
    'PALMER BOOKMAKING': 'Palmerbet',
    'ZIP': 'Zip', 'REVOLUT': 'Revolut', 'PAYPAL': 'PayPal',
    'SOLO SMITHFIELD': 'Solo', 'LIBERTY SMITHFIELD': 'Liberty',
    'UNITED ASTRON': 'United Petroleum', 'UNITED TINTINARA': 'United Petroleum',
    'COCACOLAEPP': 'Coca Cola Amatil', 'GLENGALA HTL': 'Glengala Hotel',
    'THE DEMO CLUB': 'The Demo Club', 'BLUE HIPPO': 'Blue Hippo',
    'G2A': 'G2A.com', 'BACKYARD SUPERMARKET': 'Backyard Supermarket',
    'HASTINGS D': 'Hastings D', 'AMZNPRIMEA': 'Prime Video',
    'MEKONG DELTA': 'Mekong Delta Corporation',
    'BH IGA FRESH': 'IGA', 'BOXHILL FRUIT': None,
    'SPER QLD TREASURY': None, 'GOOGLE TINDER': None,
    'PRESTON NEWS': None, 'CAS LONDON': None, 'MIKRUS': None,
    'RED BLUEBELLS': None,
}

print("\n=== PATTERN ANALYSIS ===\n")

from collections import Counter
action_counts = Counter()

# Categorize each pattern
categorized = {
    'fee': [], 'all_other_credit': [], 'liability': [],
    'transfer_genuine': [], 'initial_new': [], 'initial_existing': [],
    'skip': [],
}

for p in all_patterns:
    pn = p['pattern_norm']
    pn_clean = clean_text(pn)
    engine = p['assigned_engine']
    ptype = p.get('pattern_type', '')
    illion = p.get('illion_category', '')
    cnt = p['count']
    samples = p.get('samples', [])
    third_parties = p.get('third_parties', [])

    # === STEP 1: Cross-engine reassignment ===

    # EVERYDAY ROUND UP = internal transfer, skip
    if 'EVERYDAY ROUND UP' in pn_clean:
        action_counts['skip'] += 1
        categorized['skip'].append(p)
        continue

    # ATM withdrawal, skip
    if pn_clean.startswith('WITHDRAWAL') and ('ATM' in pn_clean or 'HANDYBANK' in pn_clean):
        action_counts['skip'] += 1
        categorized['skip'].append(p)
        continue

    # OFI informational, skip
    if 'OFI ATM' in pn_clean:
        action_counts['skip'] += 1
        categorized['skip'].append(p)
        continue

    # RPYMT/PYMT FROM - too generic, skip
    if pn_clean == 'RPYMT PYMT FROM':
        action_counts['skip'] += 1
        categorized['skip'].append(p)
        continue

    # SPRIGGY - Financial Institutions (intentionally excluded by initial engine)
    if 'SPRIGGY' in pn_clean:
        action_counts['skip'] += 1
        categorized['skip'].append(p)
        continue

    # COURTNEY TIESTO - person name, skip
    if 'COURTNEY TIESTO' in pn_clean:
        action_counts['skip'] += 1
        categorized['skip'].append(p)
        continue

    # CBA ATM - skip
    if pn_clean.startswith('CBA ATM'):
        action_counts['skip'] += 1
        categorized['skip'].append(p)
        continue

    # ATM OPERATOR FEE -> fee engine
    if 'ATM OPERATOR FEE' in pn_clean:
        action_counts['fee'] += 1
        categorized['fee'].append(p)
        continue

    # Account servicing fee -> fee engine
    if 'ACCOUNT SERVICING FEE' in pn_clean:
        action_counts['fee'] += 1
        categorized['fee'].append(p)
        continue

    # INTEREST CREDIT -> all_other_credit
    if pn_clean.startswith('INTEREST CREDIT'):
        action_counts['all_other_credit'] += 1
        categorized['all_other_credit'].append(p)
        continue

    # CASH BACK -> all_other_credit
    if 'CASH BACK' in pn_clean and 'CREDIT' in pn_clean:
        action_counts['all_other_credit'] += 1
        categorized['all_other_credit'].append(p)
        continue

    # OSKO PAYMENT RECEIVED -> all_other_credit
    if 'OSKO PAYMENT RECEIVED' in pn_clean:
        action_counts['all_other_credit'] += 1
        categorized['all_other_credit'].append(p)
        continue

    # DEPOSIT INTL -> all_other_credit
    if 'DEPOSIT INTL' in pn_clean and engine != 'all_other_credit':
        action_counts['all_other_credit'] += 1
        categorized['all_other_credit'].append(p)
        continue

    # PAYMENT RECEIVED THANK YOU -> liability
    if 'PAYMENT RECEIVED THANK YOU' in pn_clean:
        action_counts['liability'] += 1
        categorized['liability'].append(p)
        continue

    # Transfer gambling patterns -> initial
    if engine == 'transfer' and ptype == 'gambling':
        # Check if in KB via BRAND_MAP
        found_brand = None
        for brand_key, kb_name in BRAND_MAP.items():
            if brand_key in pn_clean and kb_name:
                found_brand = kb_name
                break
        if found_brand:
            action_counts['initial_existing'] += 1
            p['brand_match'] = found_brand
            categorized['initial_existing'].append(p)
        else:
            action_counts['initial_new'] += 1
            categorized['initial_new'].append(p)
        continue

    # Transfer genuine (person names for external transfers)
    if engine == 'transfer' and ptype in ['ambiguous']:
        action_counts['transfer_genuine'] += 1
        categorized['transfer_genuine'].append(p)
        continue

    # Initial patterns
    if engine == 'initial':
        # Check BRAND_MAP first
        found_brand = None
        for brand_key, kb_name in BRAND_MAP.items():
            if brand_key in pn_clean:
                found_brand = kb_name
                break
        if found_brand:
            action_counts['initial_existing'] += 1
            p['brand_match'] = found_brand
            categorized['initial_existing'].append(p)
        elif find_kb_matches_fast(pn_clean):
            action_counts['initial_existing'] += 1
            categorized['initial_existing'].append(p)
        else:
            action_counts['initial_new'] += 1
            categorized['initial_new'].append(p)
        continue

    # Catch_all remaining -> skip (already covered or too generic)
    if engine == 'catch_all':
        action_counts['skip'] += 1
        categorized['skip'].append(p)
        continue

    # Engine-specific
    if engine == 'fee':
        action_counts['fee'] += 1
        categorized['fee'].append(p)
    elif engine == 'all_other_credit':
        action_counts['all_other_credit'] += 1
        categorized['all_other_credit'].append(p)
    elif engine == 'liability':
        action_counts['liability'] += 1
        categorized['liability'].append(p)
    else:
        action_counts['skip'] += 1
        categorized['skip'].append(p)

# Print summary
print("=== ACTION SUMMARY ===")
for cat in ['fee', 'all_other_credit', 'liability', 'transfer_genuine', 'initial_new', 'initial_existing', 'skip']:
    items = categorized[cat]
    total_hits = sum(p['count'] for p in items)
    print(f"  {cat}: {len(items)} patterns, {total_hits} transactions")

# Print details
for cat in ['fee', 'all_other_credit', 'liability', 'transfer_genuine', 'initial_new']:
    items = categorized[cat]
    if items:
        print(f"\n=== {cat.upper()} ({len(items)} patterns) ===")
        for p in items:
            print(f"  [{p['count']:>4}] illion={p['illion_category']:<25} engine={p['assigned_engine']}")
            print(f"        norm: {p['pattern_norm'][:130]}")
            if p.get('brand_match'):
                print(f"        brand_match: {p['brand_match']}")

# Print initial_existing summary
items = categorized['initial_existing']
print(f"\n=== INITIAL_EXISTING ({len(items)} patterns) ===")
for p in items[:10]:
    brand = p.get('brand_match', 'via KB keyword')
    print(f"  [{p['count']:>4}] illion={p['illion_category']:<25} brand={brand}")
    print(f"        norm: {p['pattern_norm'][:130]}")
if len(items) > 10:
    print(f"  ... and {len(items)-10} more")

print("\n=== SKIP ({}) ===".format(len(categorized['skip'])))
for p in categorized['skip'][:10]:
    print(f"  [{p['count']:>4}] illion={p['illion_category']:<25} norm: {p['pattern_norm'][:130]}")
if len(categorized['skip']) > 10:
    print(f"  ... and {len(categorized['skip'])-10} more")

# Save categorized data for CSV generation
with open(f'{OUT}/_categorized.json', 'w', encoding='utf-8') as f:
    # Convert to serializable format
    serializable = {}
    for cat, items in categorized.items():
        serializable[cat] = []
        for p in items:
            sp = {k: str(v) if not isinstance(v, (str, int, float, list, type(None))) else v
                  for k, v in p.items()}
            serializable[cat].append(sp)
    json.dump(serializable, f, ensure_ascii=False, indent=2)

print(f"\nCategorized data saved to {OUT}/_categorized.json")
