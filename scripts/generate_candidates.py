#!/usr/bin/env python3
"""Generate candidate rules for all engines from gap analysis."""
import csv, os, json

review_dir = 'reviews/2026-08-11'
os.makedirs(review_dir, exist_ok=True)

# ============================================================
# 1. FEE ENGINE CANDIDATES
# ============================================================
fee_rules = [
    {
        'priority': 41, 'rule_name': 'international_txn_fee_upper', 'category': 'fee',
        'pattern': r'^INTERNATIONAL\s+TRANSACTION\s+FEE',
        'counterparty': 'International Transaction Fee', 'match_type': 'regex',
        'zero_amount_reject': 'false',
        'description': 'INTERNATIONAL TRANSACTION FEE (uppercase variant - fee engine is case-sensitive)',
        'status': 'confirm', 'hit_count': 12, 'risk_level': 'low',
        'illion_category': 'Fees',
        'samples': 'INTERNATIONAL TRANSACTION FEE',
    },
    {
        'priority': 89, 'rule_name': 'monthly_platinum_debit_fee', 'category': 'fee',
        'pattern': r'^MISCELLANEOUS\s+DEBIT\s+V\d{4}\s+\d{2}/\d{2}\s+MNTHLY\s+PLATINUM\s+DEBIT\s+FEE',
        'counterparty': 'Monthly Platinum Debit Fee', 'match_type': 'regex',
        'zero_amount_reject': 'false',
        'description': 'MNTHLY PLATINUM DEBIT FEE (misspelled MONTHLY) via Miscellaneous Debit',
        'status': 'confirm', 'hit_count': 11, 'risk_level': 'low',
        'illion_category': 'Fees',
        'samples': 'MISCELLANEOUS DEBIT V4380 21/01 MNTHLY PLATINUM DEBIT FEE 74902926021',
    },
]
fee_cols = ['priority','rule_name','category','pattern','counterparty','match_type',
            'zero_amount_reject','description','status','hit_count','risk_level',
            'illion_category','samples']
with open(f'{review_dir}/fee_candidates.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=fee_cols, extrasaction='ignore')
    w.writeheader()
    w.writerows(fee_rules)
print(f'Fee: {len(fee_rules)} candidates')

# ============================================================
# 2. ALL_OTHER_CREDIT CANDIDATES
# ============================================================
aoc_rules = [
    {
        'rule_type': 'keyword', 'pattern': 'OSKO PAYMENT RECEIVED',
        'required_terms': '',
        'status': 'confirm', 'hit_count': 19, 'risk_level': 'low',
        'illion_category': 'All Other Credits',
        'samples': 'Osko Payment Received / Leo Blennerhassett',
    },
    {
        'rule_type': 'keyword', 'pattern': 'DEPOSIT INTL',
        'required_terms': '',
        'status': 'confirm', 'hit_count': 13, 'risk_level': 'low',
        'illion_category': 'All Other Credits',
        'samples': 'DEPOSIT 2085463 INTL Ari Arthur Just',
    },
    {
        'rule_type': 'keyword', 'pattern': 'OSKO PAYMENT RECEIVED / FOOD',
        'required_terms': '',
        'status': 'confirm', 'hit_count': 10, 'risk_level': 'low',
        'illion_category': 'All Other Credits',
        'samples': 'Osko Payment Received / Food / WILLIAM LESTRO',
    },
]
aoc_cols = ['rule_type','pattern','required_terms','status','hit_count','risk_level',
            'illion_category','samples']
with open(f'{review_dir}/all_other_credit_candidates.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=aoc_cols, extrasaction='ignore')
    w.writeheader()
    w.writerows(aoc_rules)
print(f'All Other Credit: {len(aoc_rules)} candidates')

# ============================================================
# 3. CATCH_ALL CANDIDATES
# ============================================================
catch_all_rules = [
    {
        'rule_name': 'playtka_gambling', 'category': 'Gambling',
        'pattern': 'PLAYTKA', 'match_type': 'keyword', 'confidence': 0.85,
        'status': 'confirm', 'hit_count': 149, 'risk_level': 'low',
        'illion_category': 'Gambling',
        'samples': 'PLAYTKA*CAESARSLOTSVIP 3106227380 NV',
    },
    {
        'rule_name': 'gareton_gambling', 'category': 'Gambling',
        'pattern': 'GARETON', 'match_type': 'keyword', 'confidence': 0.85,
        'status': 'confirm', 'hit_count': 236, 'risk_level': 'low',
        'illion_category': 'Gambling',
        'samples': 'DEBIT CARD PURCHASE Gareton BV Limassol CYP',
    },
    {
        'rule_name': 'betr_gambling', 'category': 'Gambling',
        'pattern': 'BETR', 'match_type': 'keyword', 'confidence': 0.75,
        'status': 'confirm', 'hit_count': 173, 'risk_level': 'medium',
        'illion_category': 'Gambling',
        'samples': 'BETR 1800002387 AU AUS Card xx6957 Value Date',
    },
    {
        'rule_name': 'puntiq_gambling', 'category': 'Gambling',
        'pattern': 'PUNTIQ', 'match_type': 'keyword', 'confidence': 0.85,
        'status': 'confirm', 'hit_count': 23, 'risk_level': 'low',
        'illion_category': 'Gambling',
        'samples': 'EFTPOS PUNTIQ.COM',
    },
    {
        'rule_name': 'casiny_gambling', 'category': 'Gambling',
        'pattern': 'CASINY', 'match_type': 'keyword', 'confidence': 0.80,
        'status': 'confirm', 'hit_count': 8, 'risk_level': 'low',
        'illion_category': 'Gambling',
        'samples': 'casiny.com Nicosia CY CYP Card xx4771',
    },
    {
        'rule_name': 'bet_right_gambling', 'category': 'Gambling',
        'pattern': 'BET RIGHT', 'match_type': 'keyword', 'confidence': 0.85,
        'status': 'confirm', 'hit_count': 6, 'risk_level': 'low',
        'illion_category': 'Gambling',
        'samples': 'VISA DEBIT PURCHASE CARD 8543 BET RIGHT SYDNEY',
    },
    {
        'rule_name': 'surge_au_gambling', 'category': 'Gambling',
        'pattern': 'SURGE AU', 'match_type': 'keyword', 'confidence': 0.85,
        'status': 'confirm', 'hit_count': 15, 'risk_level': 'low',
        'illion_category': 'Gambling',
        'samples': 'MISCELLANEOUS DEBIT V8178 SURGE AU AMUSEDGROU',
    },
    {
        'rule_name': 'aviagames_entertainment', 'category': 'Entertainment',
        'pattern': 'AVIAGAMES', 'match_type': 'keyword', 'confidence': 0.80,
        'status': 'confirm', 'hit_count': 33, 'risk_level': 'medium',
        'illion_category': 'nan',
        'samples': 'AVIAGAMES SAN MATEO CA USA Card xx1183 AUD 15.00',
    },
    {
        'rule_name': 'interest_paid_credit', 'category': 'All Other Credits',
        'pattern': 'INTEREST PAID', 'match_type': 'keyword', 'confidence': 0.80,
        'status': 'confirm', 'hit_count': 26, 'risk_level': 'low',
        'illion_category': 'All Other Credits',
        'samples': 'INTEREST PAID INTEREST',
    },
]
catch_all_cols = ['rule_name','category','pattern','match_type','confidence','status',
                  'hit_count','risk_level','illion_category','samples']
with open(f'{review_dir}/catch_all_candidates.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=catch_all_cols, extrasaction='ignore')
    w.writeheader()
    w.writerows(catch_all_rules)
print(f'Catch All: {len(catch_all_rules)} candidates')

# ============================================================
# 4. TRANSFER COUNTERPARTY CANDIDATES
# ============================================================
transfer_rules = [
    {
        'keyword': 'GEORGIA ROYDS;GEORGIA E ROYDS;GEORGIA ELLEN ROYDS',
        'counterparty': 'Georgia Royds',
        'match_type': 'keyword',
        'status': 'confirm', 'hit_count': 261, 'risk_level': 'low',
        'illion_category': 'External Transfers',
        'target_file': 'transfer_counterparty_rules.csv',
        'samples': 'GEORGIA E ROYDS | Georgia Royds G | GEORGIA ELLEN ROYDS',
    },
]
transfer_cols = ['keyword','counterparty','match_type','status','hit_count','risk_level',
                 'illion_category','target_file','samples']
with open(f'{review_dir}/transfer_candidates.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=transfer_cols, extrasaction='ignore')
    w.writeheader()
    w.writerows(transfer_rules)
print(f'Transfer: {len(transfer_rules)} candidates')

# ============================================================
# 5. INITIAL ENGINE (merchant_kb) CANDIDATES
# ============================================================
# These are new merchants to add to merchant_kb.csv
# Schema: merchant_name, keywords, link, category, category_source, keyword_updated_at, category_updated_at
initial_rules = [
    # High count, clear illion label
    ('KAMBALDA VILLAGE', 'KAMBALDA VILLAGE', '', 'Groceries', 'illion', '', ''),
    ('LAVERTON SUPERMARKE', 'LAVERTON SUPERMARKE', '', 'Groceries', 'illion', '', ''),
    ('SELF SERVICE MARKETS BONDI JUNCTION', 'SELF SERVICE MARKETS|SELFSERVICE MARKETS', '', 'Groceries', 'illion', '', ''),
    ('DORSETT GOLD COAST HOTEL', 'DORSETT GOLD COAST HOTEL', '', 'Dining Out', 'illion', '', ''),
    ('BROKEN HILL MUSICIANS', 'BROKEN HILL MUSICIANS', '', 'Entertainment', 'illion', '', ''),
    ('CECIL HOTEL GOODNA', 'CECIL HOTEL GOODNA', '', 'Dining Out', 'illion', '', ''),
    ('THE DEMO CLUB BROKEN HILL', 'THE DEMO CLUB BROKEN HILL|DEMO CLUB BROKEN HILL', '', 'Dining Out', 'illion', '', ''),
    ('KINGS CREEK HOTEL HASTINGS', 'KINGS CREEK HOTEL HASTINGS|KINGS CREEK HOTEL', '', 'Dining Out', 'illion', '', ''),
    ('PARACHINAR HALAL FOOD', 'PARACHINAR HALAL FOOD', '', 'Groceries', 'illion', '', ''),
    ('HASTINGS DENTAL', 'HASTINGS D', '', 'Health', 'illion', '', ''),
    ('BACKYARD SUPERMARKET FOOTSCRAY', 'BACKYARD SUPERMARKET', '', 'Groceries', 'illion', '', ''),
    ('NEXT GEN MEMORIAL DR', 'NEXT GEN MEMORIAL DR', '', 'Gyms and other memberships', 'illion', '', ''),
    ('AAA ONE ENTERPRISE PTY', 'AAA ONE ENTERPRISE PTY', '', 'Groceries', 'illion', '', ''),
    ('GOODNA SERVICES CLUB', 'GOODNA SERVICES CLUB', '', 'Dining Out', 'illion', '', ''),
    ('BLUE HIPPO CORP', 'BLUE HIPPO CORP', '', 'Personal Care', 'illion', '', ''),
    ('BOXHILL FRUIT WORLD', 'BOXHILL FRUIT WORLD', '', 'Groceries', 'illion', '', ''),
    ('MA BLEND LAVERTON', 'MA BLEND LAVERTON', '', 'Dining Out', 'illion', '', ''),
    ('LEONORA SUPPLIES', 'LEONORA SUPPLIES', '', 'Groceries', 'illion', '', ''),
    ('TVG GIFT SHOP ANNERLEY', 'TVG GIFT SHOP', '', 'Groceries', 'illion', '', ''),
    ('SPORTING GLOBE KNOX', 'SPORTING GLOBE KNOX', '', 'Dining Out', 'illion', '', ''),
    ('MAMBOURIN FRUIT AND VEGE', 'MAMBOURIN FRUIT AND VE', '', 'Groceries', 'illion', '', ''),
    ('PLUSFITNESS MITCHELL', 'PLUSFITNESS', '', 'Gyms and other memberships', 'illion', '', ''),
    ('GLENGALA HOTEL SUNSHINE', 'GLENGALA HTL RC|GLENGALA HOTEL', '', 'Dining Out', 'illion', '', ''),
    ('SQ THREE HUNGRY BIRDS DERRIMUT', 'THREE HUNGRY BIR|THREE HUNGRY BIRD', '', 'Dining Out', 'illion', '', ''),
    ('WEST END HOTEL TOWNSVILLE', 'WEST END HOTEL TOWNSV', '', 'Dining Out', 'illion', '', ''),
    ('STACKS TULLAWONG PTY LTD', 'STACKS TULLAWONG PTY L', '', 'Automotive', 'illion', '', ''),
    ('BROKEN HILL HOLDINGS', 'BROKEN HILL HOLDINGS P', '', 'Automotive', 'illion', '', ''),
    ('RED BLUEBELLS FDC CHERMSIDE', 'BLUEBELLSFDC', '', 'Education', 'illion', '', ''),
    ('LAVERTON LPO', 'LAVERTON LPO', '', 'Retail', 'illion', '', ''),
    ('ADELAIDE METROCARD', 'ADELAIDE METROCARD', '', 'Transport', 'illion', '', ''),
    ('BWC BRISBANE HAMILTON', 'BWC BRISBANE', '', 'Transport', 'illion', '', ''),
    ('LEO COUNTRY KITCHEN LEONORA', 'LEOS COUNTRY KITCHEN|LEO COUNTRY KITCHEN', '', 'Dining Out', 'illion', '', ''),
    ('THE COOROY HOTEL', 'THE COOROY HOTEL', '', 'Dining Out', 'illion', '', ''),
    ('YUM SING HOUSE MELBOURNE', 'YUM SING HOUSE', '', 'Dining Out', 'illion', '', ''),
    ('NORTH RINGWOOD FOOTY CLUB', 'NORTH RINGWOOD FOOTY', '', 'Gyms and other memberships', 'illion', '', ''),
    ('SQ IPSWICH NETBALL', 'IPSWICH NETBALL', '', 'Gyms and other memberships', 'illion', '', ''),
    ('MARYBOROUGH HIGHLAND SOCIETY', 'MARYBOROUGH HIGHLAND S', '', 'Dining Out', 'illion', '', ''),
    ('TASTY MALATANG BRISBANE', 'TASTY MALATANG', '', 'Dining Out', 'illion', '', ''),
    ('EXPRESSO CARWASH CAFE ARUNDEL', 'EXPRESSO CARWASH', '', 'Automotive', 'illion', '', ''),
    ('THE HARBOUR SECRET', 'THE HARBOUR SECRET', '', 'Dining Out', 'illion', '', ''),
    ('MALVERN MEX', 'MALVERN MEX PL', '', 'Dining Out', 'illion', '', ''),
    ('SQ LABOUR IN VAIN FITZROY', 'LABOUR IN VAIN', '', 'Dining Out', 'illion', '', ''),
    ('MACARTHUR FRESH CAMPBELLTOWN', 'MACARTHUR FRESHSCORAIL', '', 'Groceries', 'illion', '', ''),
    ('C AND T NEWS CABRAMATTA', 'C AND T NEWS|C & T NEWS', '', 'Retail', 'illion', '', ''),
    ('MARYBOROUGH GIANTS FOOTBALL', 'MARYBOROUGH GIANTS FO', '', 'Gyms and other memberships', 'illion', '', ''),
    ('SP USAFOODS MOORABBIN', 'SP USAFOODS', '', 'Groceries', 'illion', '', ''),
    ('KEILOR DOWNS CHRISTIAN CHURCH', 'KEILOR DOWNS CHRISTIAN CH', '', 'Donations', 'illion', '', ''),
    ('SODEXO MT MORGANS LAVERTON', 'SODEXO MT MORGANS RETA', '', 'Dining Out', 'illion', '', ''),
    ('MEKONG DELTA CORPORATION', 'MEKONG DELTA CORPORATION|MEKONG DELTA', '', 'Dining Out', 'illion', '', ''),
    ('NIB HEALTH INSURANCE', 'NIB', '', 'Insurance', 'illion', '', ''),
    ('DOORDASH MELBOURNE', 'DOORDASH', '', 'Dining Out', 'illion', '', ''),
]

initial_cols = ['merchant_name','keywords','link','category','category_source',
                'keyword_updated_at','category_updated_at','status','hit_count',
                'risk_level','illion_category','samples']
with open(f'{review_dir}/initial_candidates.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(initial_cols)
    for row in initial_rules:
        w.writerow(list(row) + ['confirm', 0, 'low', row[3], ''])
print(f'Initial: {len(initial_rules)} candidates')

# Summary
total = len(fee_rules) + len(aoc_rules) + len(catch_all_rules) + len(transfer_rules) + len(initial_rules)
print(f'\nTotal candidates: {total}')
print(f'  fee: {len(fee_rules)}')
print(f'  all_other_credit: {len(aoc_rules)}')
print(f'  catch_all: {len(catch_all_rules)}')
print(f'  transfer: {len(transfer_rules)}')
print(f'  initial: {len(initial_rules)}')
