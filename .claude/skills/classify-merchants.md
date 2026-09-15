---
name: classify-merchants
description: 用联网检索为 merchant_kb.csv 中未分类的商户判定 category。用户说"分类商户"、"联网分类"、"新商户分类"、"merchant_kb 分类"、"/classify-merchants" 时触发。
---

# Classify Merchants via Web Search

> 本 skill 由独立的 `Merchant-Extraction-new` 项目迁入（见
> `docs/superpowers/specs/2026-09-14-monorepo-merge-design.md` §4.1）。
> **迁入时做了三处修正**：
> 1. 路径改为**相对仓库根**（原为相对 `modules/merchant_kb/`）
> 2. 移除 `category_source` / `category_updated_at` 写入 —— KB 自 2026-08-27 起是
>    **3 列**（`merchant_name, keywords, category`），这两个列已不存在
> 3. 两段脚本都加上 `csv.field_size_limit(10_000_000)` —— KB 单行 field 最大 137,335 字符，
>    超过 Python csv 模块默认上限 131,072，**不加必抛 `_csv.Error`**

You are an autonomous batch processor. Every invocation selects merchants, searches the web, writes results, then stops with a summary. Do not ask questions; use conservative judgment and run.

## Core Principle

This skill is self-contained. It reads `raw/initial_rule/merchant_kb.csv` directly, uses
`modules/merchant_kb/cache/web_classify_tracking.json` to remember merchants already searched, and
writes classification results directly back to the CSV. Do not call project scripts.

**所有路径相对仓库根**（`D:\project\Auto_Rule_Extension`）。

Optimize for:
- Fast: one strong search round for most merchants, second round only with a real lead.
- Accurate: classify only when web evidence confirms the real-world business activity.
- Conservative: return `""` when the activity or legal-entity-to-brand link is unclear.

## ⚠️ 写的是线上规则文件

`raw/initial_rule/merchant_kb.csv` 就是 finv `initial_engine` 加载的规则文件，改它等于改线上分类。
本 skill 只改 `category` 列（且只填空值），不动 `keywords`。**改动后要报告给用户并等确认**，
推送 finv 只能走 `scripts/apply_rules.py --sync_to`（`sync_rules.py` 是只拉不推的）。

## ⚠️ Gambling 已被人为清空

finv 于 2026-09-02 从 KB 中删除了全部 1,819 条 Gambling 商户，**当前 KB 的 Gambling 行数为 0**
（这是有意为之，不是数据丢失）。`Gambling` 仍在下方 VALID 集合里（类别本身合法），
但**给新商户判 Gambling 前必须先问用户**，不要自作主张把清理掉的类别加回去。

## Tracking File

`modules/merchant_kb/cache/web_classify_tracking.json`:
```json
{
  "searched": ["merchant name 1", "merchant name 2"],
  "last_updated": "2026-08-01T12:00:00+08:00"
}
```

Merchants in `searched` are skipped forever because they were web-searched and either received a category or were confirmed unfindable.

## Workflow

### Step 0: Parse Arguments

`$batch_size` defaults to 10. `$max_batches` defaults to 1. If `$max_batches` is 0, keep going until there are no uncategorized and unsearched merchants. If `$max_batches` is omitted, default to 1.

Run the steps below as a loop, up to `$max_batches` batches.

### Step 1: Find Next Batch

First, ensure the tracking file exists:

```bash
python -c "import json, os; os.makedirs('modules/merchant_kb/cache', exist_ok=True); p='modules/merchant_kb/cache/web_classify_tracking.json'; (not os.path.exists(p)) and json.dump({'searched':[],'last_updated':''}, open(p,'w',encoding='utf-8'), ensure_ascii=False, indent=2)"
```

Then find uncategorized and unsearched merchants:

```bash
python -c "
import csv, json, sys

csv.field_size_limit(10_000_000)   # KB 单行 field 可达 137 KB，超过 csv 默认上限

csv_path = 'raw/initial_rule/merchant_kb.csv'
tracking_path = 'modules/merchant_kb/cache/web_classify_tracking.json'
batch_size = int(sys.argv[1])

def norm(value):
    return ' '.join((value or '').strip().split()).casefold()

with open(tracking_path, encoding='utf-8') as f:
    searched = {norm(name) for name in json.load(f).get('searched', [])}

candidates = []
seen = set()
with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
    for row in csv.DictReader(f):
        name = ' '.join((row.get('merchant_name') or '').strip().split())
        key = norm(name)
        category = (row.get('category') or '').strip()
        if name and not category and key not in searched and key not in seen:
            candidates.append(name)
            seen.add(key)
            if len(candidates) >= batch_size:
                break

if not candidates:
    print('ALL_DONE')
else:
    for name in candidates:
        print(name)
" $batch_size
```

If output is `ALL_DONE`, stop and report: `All done - every merchant has a category or has been searched.`

> 当前 KB 空 category 行数为 **0**，所以正常会直接 `ALL_DONE`。
> 这条流程只在 `build_knowledge_base.py` 导入新 ABR 商户后才有实际工作量。

### Step 2: Search and Classify

Process the batch as a single classification unit. Search queries for different merchants and query variants should be run in parallel whenever the environment supports it.

#### Search Budget

Default budget per merchant:
- Round 1: one parallel search set.
- Round 2: only if Round 1 produced a plausible lead but not enough evidence.
- Stop after Round 2. Do not keep exploring.

Fast-skip names still require one search round before returning `""`, but they never get Round 2 unless a strong public-facing lead appears.

#### Round 1 Query Plan

For each merchant, build a compact set of high-signal queries:
- Exact legal name: `"{merchant_name}"`
- Australia-biased exact name when Australian context is likely: `"{merchant_name}" Australia`
- If the name contains a legal suffix, also search the suffix-stripped version in the same round.
- The CSV's `keywords` field (if read into context) is supporting context only, not proof.

Legal suffixes to strip for variant searches:
- `Pty Ltd`, `Proprietary Limited`, `Ltd`, `Limited`, `No Liability`, `NL`
- `Trading Pty Ltd`, `Holdings Pty Ltd`, `Group Pty Ltd`, `Nominees Pty Ltd`
- punctuation-only suffix noise after stripping

Australian context is likely when the name contains:
- `Pty`, `ABN`, `ACN`, Australian state abbreviations, or Australian place names.

#### Fast-Skip Patterns

These patterns are usually non-public corporate entities. Search Round 1 only; if no clear consumer-facing business, return `""`:
- Contains `Holdings`, `Nominees`, `Investments`, `Acquisitions`, `Pastoral`, `Superannuation`, `Family Trust`, `Trustee`
- Contains `Group Pty Ltd` without a known brand result
- Personal name plus `Enterprises`, `Trading`, `Consulting`, or `Services`
- Generic location/word plus `Enterprises`, `Acquisitions`, `Investments`, or `Holdings`
- ABN/ASIC-only results with no registered trading/business name

#### Evidence Rules

Classify only with one of these evidence patterns:
- Official website or brand page clearly shows the activity and matches the merchant/legal name.
- ABN Lookup/ABR shows a registered business or trading name; that trading name is then found as a real business with an activity.
- Franchisee/store/operator list, shopping-centre tenant page, map listing, or reputable directory links the legal entity or trading name to an operating business.
- Legal PDF/news/database links the legal entity to a brand, and another source confirms the brand activity.

Return `""` when:
- Results are only ABN/ASIC/company-registration pages with no trading name.
- A brand exists but cannot be linked to the searched legal entity or trading name.
- The name is too generic and search results conflict.
- The activity cannot be mapped confidently to exactly one valid category.
- Only social media or weak directory snippets exist and no corroborating source is found.

#### Round 2 Triggers

Run Round 2 only if Round 1 produced a plausible lead:
- A trading/business name from ABN Lookup.
- A possible brand/store name.
- A specific location plus business listing.
- A source naming an industry but not enough to classify.

Round 2 query options:
- Search the trading/business name exactly.
- Search suffix-stripped name plus one likely industry hint found from Round 1.
- Search `"legal name" "trading as"` or `"legal name" franchise`.
- Search the candidate brand plus Australia if the result set is global/noisy.

Do not run Round 2 when Round 1 found only registry pages or no meaningful lead.

#### Source Reliability

Use sources in this order:
1. Official brand/store/franchise pages.
2. ABN Lookup/ABR trading or business names.
3. Shopping centre tenant pages, maps, reputable business directories.
4. Legal documents, PDFs, franchise schedules, court filings.
5. Industry databases/SIC/ANZSIC records as supporting evidence only.
6. News and social media as weak corroboration only.

#### Classification Output While Searching

For each merchant, keep a short internal note:
- `category`: valid category or `""`
- `confidence`: `high`, `medium`, or `empty`
- `evidence`: one short phrase naming the best evidence
- `reason`: why the category was chosen or why it is empty

The final saved JSON only needs `results`; `evidence` and `reason` are for your summary and self-check.

### Step 3: Write Results

Write `modules/merchant_kb/cache/_batch_result.json` with every merchant from the batch included exactly once:

```json
{
  "results": {
    "Merchant Name 1": "Dining Out",
    "Merchant Name 2": ""
  }
}
```

Rules:
- Empty string means searched but no category was confirmed.
- Category values must exactly match the valid category list.
- Never omit a merchant from the batch.

Then run the save script:

```bash
python -c "
import csv, json, os, time
from datetime import datetime, timezone, timedelta

csv.field_size_limit(10_000_000)   # KB 单行 field 可达 137 KB，超过 csv 默认上限

VALID = {
    'Automotive', 'Department Stores', 'Dining Out', 'Donations', 'Education',
    'Entertainment', 'Financial Institutions', 'Gambling', 'Groceries',
    'Gyms and other memberships', 'Health', 'Home Improvement', 'Information',
    'Insurance', 'Personal Care', 'Pet Care', 'Rent', 'Retail',
    'Subscription TV', 'Telecommunications', 'Transport', 'Travel', 'Utilities'
}

def norm(value):
    return ' '.join((value or '').strip().split()).casefold()

with open('modules/merchant_kb/cache/_batch_result.json', 'r', encoding='utf-8') as f:
    batch = json.load(f)
results = batch['results']
if not isinstance(results, dict) or not results:
    raise ValueError('modules/merchant_kb/cache/_batch_result.json must contain a non-empty results object')

clean_results = {}
for name, category in results.items():
    merchant_name = ' '.join(str(name).strip().split())
    category = str(category).strip()
    if not merchant_name:
        raise ValueError('Result contains an empty merchant name')
    if category and category not in VALID:
        raise ValueError(f'Invalid category for {merchant_name}: {category}')
    clean_results[merchant_name] = category

csv_path = 'raw/initial_rule/merchant_kb.csv'
tracking_path = 'modules/merchant_kb/cache/web_classify_tracking.json'
now = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M:%S+08:00')

with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
    reader = csv.DictReader(f)
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)

# KB 自 2026-08-27 起固定 3 列。多写列会被 initial_engine 的 usecols 静默忽略，
# 但会污染 raw/ 并与 finv 侧产生字节级漂移。
if fieldnames != ['merchant_name', 'keywords', 'category']:
    raise ValueError(f'merchant_kb.csv 列结构异常: {fieldnames}（期望 3 列 merchant_name, keywords, category）')

results_by_key = {norm(name): category for name, category in clean_results.items()}
updated = 0
matched = set()
for row in rows:
    key = norm(row.get('merchant_name', ''))
    if key not in results_by_key:
        continue
    matched.add(key)
    new_cat = results_by_key[key]
    if new_cat and not (row.get('category') or '').strip():
        row['category'] = new_cat
        updated += 1

unmatched = set(results_by_key) - matched
if unmatched:
    raise ValueError(f'Results did not match CSV merchants: {sorted(unmatched)[:5]}')

target = os.path.abspath(csv_path)
tmp = f'{target}.{os.getpid()}.tmp'
with open(tmp, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
for attempt in range(1, 4):
    try:
        os.replace(tmp, target)
        break
    except PermissionError:
        if attempt >= 3:
            raise
        time.sleep(1)

searched_names = []
if os.path.exists(tracking_path):
    with open(tracking_path, encoding='utf-8') as f:
        searched_names = [
            ' '.join(str(name).strip().split())
            for name in json.load(f).get('searched', [])
            if str(name).strip()
        ]
searched_keys = {norm(name) for name in searched_names}
for name in clean_results:
    key = norm(name)
    if key not in searched_keys:
        searched_names.append(name)
        searched_keys.add(key)

tracking_payload = {
    'searched': sorted(searched_names, key=lambda value: value.casefold()),
    'last_updated': now
}
tracking_tmp = f'{os.path.abspath(tracking_path)}.{os.getpid()}.tmp'
with open(tracking_tmp, 'w', encoding='utf-8') as f:
    json.dump(tracking_payload, f, ensure_ascii=False, indent=2)
os.replace(tracking_tmp, tracking_path)

os.remove('modules/merchant_kb/cache/_batch_result.json')

classified = sum(1 for v in clean_results.values() if v.strip())
empty = len(clean_results) - classified
print(f'SAVED: classified={classified} empty={empty} updated={updated} total_tracked={len(searched_names)}')
"
```

### Step 4: Loop or Stop

Report a one-line summary after each batch. If `$max_batches` is 0, go back to Step 1. If the configured number of batches has completed, stop. If Step 1 returned `ALL_DONE`, stop.

Final report format:
```text
Batch N: classified=X empty=Y updated=U | total tracked=Z | [CONTINUING|ALL_DONE]
```

After the loop, **报告给用户并等确认**：本批次改了多少行、改了哪些 category。
不要自行提交或同步。

## Classification Rules

- Classify by actual, real-world business activity confirmed via web search.
- Must use web search; do not guess from name fragments.
- Prefer `""` over a weak or inferred category.
- For names with legal suffixes, search both full and suffix-stripped forms in the same round.
- When ABN Lookup shows a trading/business name different from the legal name, search that trading name too.
- If a source only proves a legal entity exists, not what it operates, return `""`.
- For holding/property/investment/trust entities, do not use passive asset ownership as proof of `Rent` unless the entity operates property management, leasing, real estate agency, or storage services.
- **不要写 `Financial Institutions`** —— 该 category 的行在 initial_engine 加载时被整行丢弃
  （由 liability/dishonour 引擎处理），写进去等于白写。

## Valid Categories

Exact, case-sensitive:

Automotive, Department Stores, Dining Out, Donations, Education, Entertainment, Financial Institutions, Gambling, Groceries, Gyms and other memberships, Health, Home Improvement, Information, Insurance, Personal Care, Pet Care, Rent, Retail, Subscription TV, Telecommunications, Transport, Travel, Utilities

> 上表是引擎接受的完整类别集。但按本 skill 的用途，实际可写的子集要排除
> `Financial Institutions`（见上）；`Gambling` 需先问用户（见顶部警告）。

Quick reference:
- Automotive: fuel, vehicles, repairs, parts, car washes, roadside
- Department Stores: large mixed-retail, discount, supercentre
- Dining Out: restaurants, cafes, bars, fast food, food delivery, catering, prepared meals
- Donations: charities, non-profits, fundraising, religious giving
- Education: childcare, schools, universities, tutoring, training
- Entertainment: cinemas, theatres, museums, attractions, events, clubs, music, games
- Financial Institutions: banks, lenders, payment services, mortgages, securities, wealth, brokers
- Gambling: casinos, betting, wagering, lotteries, gaming venues
- Groceries: supermarkets, food shops, bakeries, butchers, seafood, liquor, bottle shops, food suppliers, wholesalers, processors
- Gyms and other memberships: gyms, fitness, yoga, pilates, sports training, member clubs
- Health: pharmacies, dentists, optometrists, clinics, hospitals, healthcare
- Home Improvement: construction, trades, hardware, cleaning, repairs, maintenance, facilities, security, building services
- Information: software, IT, computer services, data, media, publishing, digital platforms
- Insurance: insurers, brokers, policies, claims, warranties
- Personal Care: hair, beauty, nails, spas, grooming, laundry, tailoring, consumer photography
- Pet Care: vets, animal hospitals, pet shops, pet food, grooming, boarding
- Rent: rent, leases, property managers, real estate agencies, storage
- Retail: clothing, shoes, jewellery, books, florists, gifts, electronics, specialty goods
- Subscription TV: cable, satellite, streaming TV, paid television
- Telecommunications: mobile, phone, internet, broadband, network, telecom providers
- Transport: public transport, taxis, rideshare, parking, tolls, freight, logistics, couriers, vehicle registration
- Travel: hotels, holiday rentals, airlines, travel agencies, tours, cruises, car rental
- Utilities: electricity, gas, water, waste, taxes, council rates, government fees, fines, public services

## Tie-Breakers

- Bottle shops, liquor stores, bakeries, butchers, seafood shops, and food wholesalers: `Groceries`
- Cafes, restaurants, bars, catering, take-away, prepared meals: `Dining Out`
- Pharmacies: `Health`, even if they also sell retail goods
- Hardware, building supplies, trades, cleaners, maintenance, security installers: `Home Improvement`
- Passive investment/property holders: `""` unless operating a customer-facing rent/property service
- Sports clubs: `Entertainment`; gyms, fitness studios, yoga/pilates: `Gyms and other memberships`
- Streaming TV: `Subscription TV`; general software/SaaS/media platforms: `Information`
- Council rates, fines, taxes, government fees: `Utilities`

## Examples

| Merchant | Search finding | Category |
|----------|----------------|----------|
| Naked for Satan | Bar/restaurant in Melbourne | Dining Out |
| Cellarbrations | Bottle shop / liquor store chain | Groceries |
| Blackburn Football Club | Local football/sports club | Entertainment |
| Chemist Warehouse | Pharmacy chain | Health |
| BWS | Beer Wine Spirits bottle shop | Groceries |
| Uber | Rideshare platform | Transport |
| Bunnings Warehouse | Hardware store chain | Home Improvement |
| Telstra | Telco provider | Telecommunications |
| Netflix | Streaming TV service | Subscription TV |
| BENMIREN NOMINEES PTY LTD | Generic corporate entity, no public business | "" |
| GOCUP PASTORAL PTY LTD | No public-facing business found | "" |
| J SMITH ENTERPRISES PTY LTD | Personal/generic enterprise, no clear business | "" |

## Safety Rules

- Only modify the `category` column of `raw/initial_rule/merchant_kb.csv`, and only when it is
  currently empty. **Never touch `keywords`** — that is the matching surface.
- Only write to `raw/initial_rule/merchant_kb.csv`, `modules/merchant_kb/cache/web_classify_tracking.json`,
  and `modules/merchant_kb/cache/_batch_result.json`.
- Read CSV with `utf-8-sig`; write CSV with `utf-8-sig` to preserve the BOM (removing it counts as a
  local change in `sync_rules.py`).
- Always set `csv.field_size_limit(10_000_000)` before reading or writing the KB.
- Use the save script for tracking updates; do not edit tracking manually.
- If the save script fails, report the error and stop. Do not retry with different code.
