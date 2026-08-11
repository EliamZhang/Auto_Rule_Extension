#!/usr/bin/env python3
"""Revise candidates: move brand-name patterns from catch_all to initial engine."""
import csv, os

review_dir = 'reviews/2026-08-11'

# ============================================================
# INITIAL: New merchants to add (brand/platform names NOT in KB)
# ============================================================
initial_new = [
    # Gambling platforms - NOT in merchant_kb
    ('PLAYTKA', 'PLAYTKA', '', 'Gambling', 'illion',
     'Online casino games platform (Caesars Slots VIP). 155 gap occurrences, 0 conflicts.'),
    ('GARETON BV', 'GARETON', '', 'Gambling', 'illion',
     'Gambling payment processor, Limassol Cyprus. 241 gap occurrences, 0 conflicts.'),
    ('PUNTIQ', 'PUNTIQ', '', 'Gambling', 'illion',
     'Sports betting website (puntiq.com). 23 gap occurrences, 0 conflicts.'),
    ('CASINY', 'CASINY', '', 'Gambling', 'illion',
     'Online casino (casiny.com). 11 gap occurrences, 0 conflicts.'),
    ('SURGE AU AMUSEDGROUP', 'SURGE AU', '', 'Gambling', 'illion',
     'Gambling platform (amusedgroup.com). 15 gap occurrences. Not SOCIALSURGE/SALES SURGE/SOLAR SURGE in KB.'),
    ('BETR GAMBLING', 'BETR', '', 'Gambling', 'illion',
     'Sports betting platform (betr.com.au). 174 gap occurrences. Existing BETR ENTERTAINMENT LIMITED->Entertainment has longer keyword, longest-match handles priority.'),
    ('FRVN GAMBLING', 'FRVN', '', 'Gambling', 'AI',
     'Gambling payment processor, Limassol Cyprus (TCP*FRVN). 63 gap occurrences.'),
    ('SCRN GAMBLING', 'SCRN', '', 'Gambling', 'AI',
     'Gambling payment processor, Limassol Cyprus (TCP*SCRN). 17 gap occurrences.'),
    ('BTZ GAMBLING', 'BTZ', '', 'Gambling', 'AI',
     'Gambling payment processor, Limassol Cyprus (TCP*BTZ). 7 gap occurrences. Existing BTZ PTY LTD in KB is different company.'),
    # Online service platforms
    ('AVIAGAMES', 'AVIAGAMES', '', 'Entertainment', 'AI',
     'Mobile gaming platform. 33 new + 180 currently classified as Dining Out. HIGH CONFLICT - needs review.'),
    ('TG TALLINN', 'TG TALLINN', '', 'Entertainment', 'AI',
     'Online service/platform, Estonia. 118 gap occurrences. In config online_indicators.'),
    ('INCEUNION LONDON', 'INCEUNION', '', 'Entertainment', 'AI',
     'Online service, London. 44 gap occurrences. In config online_indicators.'),
    ('SKMG GROUP', 'SKMG GROUP|SKMG', '', 'Entertainment', 'AI',
     'Online service. 28 gap occurrences. In config online_indicators.'),
    ('IMGVIBES PRAGUE', 'IMGVIBES|IMGVIDES', '', 'Entertainment', 'AI',
     'Online media platform, Prague. 24 gap occurrences. In config online_indicators.'),
    ('DEGABEAT LONDON', 'DEGABEAT', '', 'Entertainment', 'AI',
     'Online music/platform, London. 13 gap occurrences. In config online_indicators.'),
    ('COURTNEY TIESTO', 'COURTNEY TIESTO', '', 'Entertainment', 'AI',
     'Online service/platform. 33 gap occurrences in catch_all. In config online_indicators.'),
    ('CLIPVIDOREU MANCHESTER', 'CLIPVIDOREU', '', 'Entertainment', 'AI',
     'Online video platform, Manchester. 6 gap occurrences. In config online_indicators.'),
    ('1D3 DIGITECH TALLINN', '1D3 DIGITECH', '', 'Entertainment', 'AI',
     'Online service, Estonia. 17 gap occurrences.'),
    ('SPRIGGY', 'SPRIGGY', '', 'Personal Care', 'AI',
     'Children financial app/debit card. 15 gap occurrences. Category uncertain.'),
]

# ============================================================
# INITIAL: Category updates for EXISTING merchants
# ============================================================
initial_updates = [
    ('BET RIGHT PTY LIMITED', 'Gambling',
     'Currently has NO category. Sports betting platform. 6 gap occurrences with illion=Gambling.'),
    ('MEKONG DELTA CORPORATION PTY LTD', 'Dining Out',
     'Currently has NO category. Vietnamese restaurant. 49 gap occurrences with illion=Dining Out.'),
    ('MEKONG DELTA PTY LTD', 'Dining Out',
     'Currently has NO category. Vietnamese restaurant. Related to MEKONG DELTA CORPORATION.'),
]

# ============================================================
# CATCH_ALL: Only keep truly GENERIC patterns (not brand names)
# ============================================================
catch_all_keep = [
    {
        'rule_name': 'interest_paid_credit', 'category': 'All Other Credits',
        'pattern': 'INTEREST PAID', 'match_type': 'keyword', 'confidence': 0.80,
        'status': 'confirm', 'hit_count': 26, 'risk_level': 'low',
        'illion_category': 'All Other Credits',
        'samples': 'INTEREST PAID | INTEREST PAID (INCLUDES BONUS)',
    },
]

# ============================================================
# Write all revised candidates
# ============================================================

# 1. Initial new merchants
initial_cols = ['merchant_name','keywords','link','category','category_source',
                'keyword_updated_at','category_updated_at','status','hit_count',
                'risk_level','illion_category','samples','notes']
with open(f'{review_dir}/initial_candidates.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(initial_cols)
    for row in initial_new:
        w.writerow(list(row) + ['confirm', 0, 'low', row[3], ''])
print(f'Initial new merchants: {len(initial_new)} written')

# 2. Initial category updates
update_cols = ['merchant_name','new_category','reason','status']
with open(f'{review_dir}/initial_category_updates.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(update_cols)
    for row in initial_updates:
        w.writerow(list(row) + ['confirm'])
print(f'Initial category updates: {len(initial_updates)} written')

# 3. Catch-all (generic only)
catch_all_cols = ['rule_name','category','pattern','match_type','confidence','status',
                  'hit_count','risk_level','illion_category','samples']
with open(f'{review_dir}/catch_all_candidates.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=catch_all_cols, extrasaction='ignore')
    w.writeheader()
    w.writerows(catch_all_keep)
print(f'Catch-all (generic only): {len(catch_all_keep)} written')

# Summary
print(f'\n=== REVISED CANDIDATE SUMMARY ===')
print(f'Initial new merchants:      {len(initial_new)}')
print(f'Initial category updates:   {len(initial_updates)}')
print(f'Catch-all (generic only):   {len(catch_all_keep)}')
print(f'Fee rules:                  2 (unchanged)')
print(f'All other credit:           2 (unchanged, minus DEPOSIT INTL)')
print(f'Transfer counterparty:       1 (unchanged)')

# === CATEGORY BREAKDOWN ===
from collections import Counter
cats = Counter(m[3] for m in initial_new)
print(f'\nInitial new merchants by category:')
for cat, count in cats.most_common():
    print(f'  {cat}: {count}')
