# BS-CAT Category Performance Report

**Income, Expense, Transfer and Liability Category Performance — Illion vs finv**

| Sample scope | Categories | Comparison | Report date |
| :--- | :--- | :--- | :--- |
| 13,559,362 transactions | 36 categories | Illion vs finv \| BS-CAT | 2026-09-09 |

> This report is generated automatically from workbook category_difference_report_v2.xlsx and covers 36 categories across income, expenses, transfers and liabilities. Every figure is computed dynamically from the workbook; liability detail is kept at metric level because a dedicated Liability deep-dive report already exists.

## Data source

Excel workbook: sheet 00_核心对比 (headline and per-category metrics), 01_差异诊断地图 (top 20 difference flows), 03_排查明细 (1,048,572 difference rows with amounts and direction).

Basis of preparation: every figure in this report comes from the 00_核心对比, 01_差异诊断地图 and 02_业务聚类对比 sheets. 03_排查明细 is used only to pick the “typical transaction samples” (the 5 largest rows per category); it is never used as a denominator or a count. Any share described below as being of “difference samples” therefore describes the sample make-up, not the full distribution of differences.

## Contents

  - [Data source](#data-source)
- [1. Executive Summary](#1-executive-summary)
- [2. Coverage and Agreement Overview](#2-coverage-and-agreement-overview)
  - [2.1 Overall Coverage, Agreement and Differences](#21-overall-coverage-agreement-and-differences)
  - [2.2 Business Segments: Coverage and Agreement](#22-business-segments-coverage-and-agreement)
  - [2.3 Category-Level Coverage and Agreement](#23-category-level-coverage-and-agreement)
- [3. Category Review by Business Segment](#3-category-review-by-business-segment)
  - [3.1 Income Segment](#31-income-segment)
  - [3.2 Expenses Segment](#32-expenses-segment)
  - [3.3 Transfers Segment](#33-transfers-segment)
  - [3.4 Liabilities Segment](#34-liabilities-segment)
  - [3.5 Cross-Segment Recommendations](#35-cross-segment-recommendations)
- [4. Appendix: Category Metrics and Roll-ups](#4-appendix-category-metrics-and-roll-ups)

---

# 1. Executive Summary

This assessment compares the category labels that Illion and finv assign to the same transactions. finv assigns a category to 89.96% of all transactions — slightly above Illion's 89.50% (a 0.46 pp gap), so coverage is broadly on a par, with finv holding a modest edge. Where both sides classify the same transaction, the two sides' labels agree in 88.70% of cases, showing that the two engines classify the rows they share consistently. Among the 2,869,569 difference rows, classification disagreement is the largest single source at 44.81%, followed by finv-only recognition (28.69%) and Illion-only recognition (26.50%) — so the gap is driven first by how the two sides label the rows they both classify, with one-sided coverage secondary. Difference rows concentrate in transfers and consumption. The headline issue is therefore not coverage quantity but inconsistent rules and boundaries in specific categories; follow-up work should target transfers and high-frequency consumption, and align the classification standard.

> Bottom line: the coverage comparison, the agreement on jointly classified rows, the composition of differences, and the primary source of the gap are summarised above; full detail follows in Sections 2–3.

---

# 2. Coverage and Agreement Overview

This section works top-down: the overall agreement rate and the nature of the differences, then the four business segments, then the category level. Coverage means the share of all transactions that Illion/finv assigns to a category; the agreement rate is the share of transactions with at least one side classified on which both sides assign the same category. Full per-category metrics are rolled up in the Appendix (Section 4).

## 2.1 Overall Coverage, Agreement and Differences

| Metric | Value | Definition |
| :--- | :---: | :--- |
| Illion coverage | 89.50% | transactions classified by Illion / all transactions |
| finv coverage | 89.96% | transactions classified by finv / all transactions |
| Coverage gap (finv − Illion) | 0.46% | how much wider finv is than Illion |
| Both classified | 83.89% | transactions classified by both / all transactions |
| Agreement (both classified) | 88.70% | same category on both sides / classified by both |
| Coverage-adjusted agreement | 77.86% | same category / at least one side classified |
| Difference rate (≥ one side) | 22.14% | difference rows / at least one side classified |

On coverage, finv (89.96%) runs 0.46 pp ahead of Illion (89.50%), so its coverage is slightly wider. Across all transactions, 83.89% are classified by both sides, and within that set the two sides' labels agree 88.70% of the time — the two engines judge the rows they share quite consistently. Adding the rows that only one side classifies (the “at least one side” basis) drops the agreement rate to 77.86%, with a difference rate of 22.14%. The 10.84 pp gap between the two bases comes from one-sided rows, which make up 12.22% of the union — counting them as differences pulls the coverage-adjusted agreement rate down.

Splitting the difference rows by nature gives two buckets: classification disagreement (both sides classified the row, but with different labels — a rules or boundary issue) and one-sided recognition (only one side classified the row — a coverage gap). The table shows the distribution:

| Nature of difference | Share of all diffs | What it means |
| :--- | :---: | :--- |
| Classification disagreement | 44.81% | both sides classified, labels differ |
| Finv-only recognition | 28.69% | finv recognised a category Illion did not |
| Illion-only recognition | 26.50% | Illion recognised a category finv did not |
| Total difference rows | 100.00% | disagreements plus one-sided rows |

Classification disagreement is the largest source at 44.81%, then finv-only recognition (28.69%) and Illion-only recognition (26.50%). The first two together explain about 73.50% of all differences. Rows with neither side classified (4.43% of all transactions) are excluded from difference analysis because no label comparison is possible. In short, finv's coverage is slightly better, agreement within the shared set is high, and the overall gap is driven first by label disagreement, then by extra recognition on the finv side, with Illion-only rows contributing least.

## 2.2 Business Segments: Coverage and Agreement

The 36 categories form four segments: Income (3), Expenses (23), Transfers (2) and Liabilities (8). Transfers are tracked separately as a neutral flow rather than being folded into income or expenses. The segment agreement denominator is the segment union (at least one side classified into the segment), matching the overall coverage-adjusted agreement basis.

| Segment | Cats | Illion cov. | finv cov. | Agreement (union) | Agreement (in segment) | Seg. union % |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Income | 3 | 2.47% | 4.83% | 35.82% | 38.34% | 5.28% |
| Expenses | 23 | 34.56% | 36.67% | 76.17% | 79.42% | 39.70% |
| Transfers | 2 | 37.67% | 33.25% | 75.80% | 85.09% | 38.31% |
| Liabilities | 8 | 14.80% | 15.21% | 85.08% | 92.95% | 15.55% |

“Agreement (union)” = same category on both sides / at least one side classified into the segment; “Agreement (in segment)” = both sides in the segment (labels may differ) / at least one side in it — the looser basis. “Seg. union %” = segment union / all transactions; segment unions overlap on cross-segment rows, so the per-segment rates do not sum to the overall rate — use the table for horizontal comparison.

> Segment-level performance splits sharply. Liabilities leads on both agreement bases (85.08% union-based and 92.95% in-segment), the strongest of the four segments either way; it is the segment where the two sides converge most. Income has the lowest agreement, at 35.82% — but this is mostly a scope difference: within the segment finv covers 4.83% versus Illion's 2.47%, and finv-only rows make up 35.43% of the segment's differences. A low rate here reflects finv's wider net more than weak recognition. Income is a small segment (union 5.28% of all transactions), so its low agreement moves the overall picture little. Expenses is the largest segment by volume, with a union of 39.70% of all transactions (Illion 34.56%, finv 36.67%), and its internal differences are the most concentrated of any segment, contributing 44.69% of all difference rows. Transfers is where the two sides' coverage diverges most — Illion sits 4.42 pp above finv, the largest gap of any segment, signalling a real disagreement about what belongs in Transfers.

## 2.3 Category-Level Coverage and Agreement

The full per-category table (agreement on the union basis = intersection / category union) is in Appendix 4.1; important categories are analysed in Section 3 and the remaining ones are rolled up in Appendices 4.2–4.3. Traffic-light marks: red (bold) = flag (agreement < 50% or an only-share > 30%); green (bold) = strong (agreement > 80%).

The two charts below give the category-level view — Chart 2.1 compares per-category coverage and Chart 2.2 shows the distribution of agreement rates — followed by the category-level conclusions.

![md_chart_2_1_coverage](md_chart_2_1_coverage.png)

**Chart 2.1 Category coverage comparison (first two columns: bar length = that side's coverage, i.e. rows classified by the side / all transactions; blue = Illion, orange = finv. Right column: finv − Illion in pp; orange = finv wider, blue = Illion wider)**

![md_chart_2_2_dotplot](md_chart_2_2_dotplot.png)

**Chart 2.2 Category agreement distribution by segment (agreement = intersection / union; red < 50%, amber 50–80%, green ≥ 80%)**

> Among categories with meaningful volume (union ≥ 500), the lowest-agreement 5 categories are Unknown Loans, Information, All Other Credits, Pet Care and SACC Loans: Unknown Loans agrees 0.00%, with a union of 0.66% of all transactions; Information agrees 0.45% (0.93% of all transactions); All Other Credits agrees 7.50% (1.76% of all transactions); Pet Care agrees 29.45% (0.23% of all transactions); SACC Loans agrees 41.50% (1.25% of all transactions). At the other end, the three strongest are Overdrawn, Department Stores and Telecommunications, agreeing 98.85%, 94.18% and 92.55% respectively — mostly clearly-ruled liability and fixed-spend categories. Separately, Unknown Loans has no Illion coverage at all — an Illion blind spot.

---

# 3. Category Review by Business Segment

This section answers, for each segment: which categories are stable, which show one-sided coverage, and which need investigation. Each category is described with the same seven ratios: 1 Illion coverage; 2 finv coverage; 3 union share of all transactions; 4 intersection share of all transactions; 5 agreement (category union); 6 Illion-only share of the union; 7 finv-only share of the union. Union and intersection shares use all transactions as the denominator, agreement uses the category union; because a row the two sides classify differently is counted in both category unions, category agreement rates do not add up to the overall coverage-adjusted rate (77.86%, transaction-deduplicated).

## 3.1 Income Segment

Income differences concentrate in Wages and All Other Credits; the segment's differences are driven mainly by finv's broader Income recognition; the External Transfers boundary is the main cross-segment leak, with the two sides disagreeing on how some transfer inflows should be classified.

### 3.1.1 All Other Credits

All Other Credits: agreement is only 7.50%, and the core tension is finv classifying "All Other Credits" more broadly. Differences concentrate in the (blank) → All Other Credits flow (3.27% of all differences, — in value).

**Category detail**

**Definition:** Other credit inflows — non-payroll, non-benefit receipts and refunds.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| All Other Credits | 0.61% | 1.28% | 🔴 1.76% | 0.13% | 🔴 7.50% | 26.97% | 65.53% |

**Interpretation:** Illion covers 0.61% of all transactions and finv 1.28%; the union of the two sides spans 1.76% of all transactions and the intersection 0.13%, giving an agreement rate of 7.50% on the union basis. Illion-only and finv-only shares of the union are 26.97% and 65.53% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | (blank) | All Other Credits | finv only | 42.52% | — |
| 2 | External Transfers | All Other Credits | Both classified, labels differ | 22.34% | $19,085,209.21 |
| 3 | All Other Credits | (blank) | Illion only | 19.34% | $19,398,911.39 |
| 4 | All Other Credits | Wages | Both classified, labels differ | 6.10% | $6,122,974.64 |
| 5 | Wages | All Other Credits | Both classified, labels differ | 1.65% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is $44,607,095.24; the workbook gives an amount for only 3 of these flows, so the other 60 are not covered.

**Sample transactions**

### 3.1.2 Wages

Wages: agreement is only 43.67%, and the core tension is finv classifying "Wages" more broadly. Differences concentrate in the External Transfers → Wages flow (4.51% of all differences, $41,035,434.64 in value). A typical case, "PAYMENT FROM SANTHOSH BEERAM" ($20,000.00), is classified External Transfers → Wages and is consistent with that view.

**Category detail**

**Definition:** Salary and wage inflows, including payroll credits, bonuses and severance payments.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Wages | 1.51% | 3.23% | 🔴 3.30% | 1.44% | 🔴 43.67% | 2.09% | 54.24% |

**Interpretation:** Illion covers 1.51% of all transactions and finv 3.23%; the union of the two sides spans 3.30% of all transactions and the intersection 1.44%, giving an agreement rate of 43.67% on the union basis. Illion-only and finv-only shares of the union are 2.09% and 54.24% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | External Transfers | Wages | Both classified, labels differ | 51.41% | $41,035,434.64 |
| 2 | (blank) | Wages | finv only | 27.34% | — |
| 3 | All Other Credits | Wages | Both classified, labels differ | 5.35% | $6,122,974.64 |
| 4 | Internal Transfer | Wages | Both classified, labels differ | 4.70% | — |
| 5 | Non SACC Loans | Wages | Both classified, labels differ | 2.38% | — |

**Direction & value:** Of the difference rows, 100.00% are credit and 0.00% debit; combined difference value is $47,158,409.28; the workbook gives an amount for only 2 of these flows, so the other 60 are not covered.

**Sample transactions**

| Date | Transaction description | Counterparty | Dir. | Amount | Illion → finv |
| :---: | :--- | :--- | :---: | ---: | :---: |
| 2026-03-12 | PAYMENT FROM SANTHOSH BEERAM | SANTHOSH BEERAM | credit | $20,000.00 | External Transfers → Wages |
| 2026-03-02 | PAYMENT FROM MISS CAROLINE KUCERA | MISS CAROLINE KUCERA | credit | $20,000.00 | External Transfers → Wages |
| 2026-03-30 | NIRAV H TRIVEDI TRANSFER CREDIT | NIRAV TRIVEDI | credit | $20,000.00 | External Transfers → Wages |
| 2026-06-10 | TRANSFER CREDIT HOPEPA FAKAOFORent/bills/shopping | HOPEPA FAKAOFORENT BILLS SHOPPING | credit | $20,000.00 | External Transfers → Wages |
| 2026-06-11 | TRANSFER CREDIT HOPEPA FAKAOFORent/bills/shopping | HOPEPA FAKAOFORENT BILLS SHOPPING | credit | $20,000.00 | External Transfers → Wages |

### 3.1.3 Centrelink

Centrelink: agreement is 89.35%, and the core tension is Illion classifying "Centrelink" more broadly. Differences concentrate in the Centrelink → (blank) flow (0.08% of all differences, — in value).

**Category detail**

**Definition:** Government benefit inflows, including Centrelink payments and other subsidies.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Centrelink | 0.35% | 0.32% | 🟢 0.36% | 0.32% | 89.35% | 9.90% | 0.75% |

**Interpretation:** Illion covers 0.35% of all transactions and finv 0.32%; the union of the two sides spans 0.36% of all transactions and the intersection 0.32%, giving an agreement rate of 89.35% on the union basis. Illion-only and finv-only shares of the union are 9.90% and 0.75% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | Centrelink | (blank) | Illion only | 42.82% | — |
| 2 | Centrelink | External Transfers | Both classified, labels differ | 29.71% | — |
| 3 | Centrelink | Wages | Both classified, labels differ | 9.39% | — |
| 4 | Centrelink | All Other Credits | Both classified, labels differ | 7.40% | — |
| 5 | (blank) | Centrelink | finv only | 2.85% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is —; the workbook gives an amount for only 0 of these flows, so the other 19 are not covered.

**Sample transactions**

**Income boundary notes**

Wages vs All Other Credits: confusion runs both ways; 1 of the 2 directions carry $6,122,974.64, and the workbook gives no amount for the other 1.

Wages vs External Transfers: confusion runs both ways; 1 of the 2 directions carry $41,035,434.64, and the workbook gives no amount for the other 1.

All Other Credits vs External Transfers: confusion runs both ways; 1 of the 2 directions carry $19,085,209.21, and the workbook gives no amount for the other 1.

Income differences are credit-led, as expected for an income-recognition exercise. finv-only income recognition lands mostly on All Other Credits, so sample those rows to confirm the descriptions meet the income definitions.

---

## 3.2 Expenses Segment

Expenses differences concentrate in Dining Out and Groceries; the segment's differences are driven mainly by finv's broader Expenses recognition; the boundary with External Transfers is the main cross-segment leak. By contrast, Department Stores shows strong consensus (94.18% agreement) and low risk.

The expenses segment holds 23 categories. Two are given full deep dives with top difference flows, direction, amounts and samples — Gambling (3.2.1) and Rent (3.2.2). Four flagged categories with meaningful difference volume — Retail, Information, Donations and Automotive (3.2.3–3.2.6) — receive the same treatment. The remaining categories are rolled up in Appendix 4.2.

### 3.2.1 Gambling Deep Dive

Gambling: agreement is 88.13%, and the core tension is a boundary misalignment between the two sides on "Gambling". Differences concentrate in the Gambling → (blank) flow (1.18% of all differences, $2,853,025.18 in value).

**Category detail**

**Definition:** Gambling spend — betting, lottery and casino transactions.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Gambling | 3.99% | 3.82% | 🟢 4.15% | 3.66% | 88.13% | 8.01% | 3.86% |

**Interpretation:** Illion covers 3.99% of all transactions and finv 3.82%; the union of the two sides spans 4.15% of all transactions and the intersection 3.66%, giving an agreement rate of 88.13% on the union basis. Illion-only and finv-only shares of the union are 8.01% and 3.86% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | Gambling | (blank) | Illion only | 50.82% | $2,853,025.18 |
| 2 | (blank) | Gambling | finv only | 25.86% | — |
| 3 | Gambling | External Transfers | Both classified, labels differ | 6.19% | — |
| 4 | External Transfers | Gambling | Both classified, labels differ | 4.51% | — |
| 5 | Gambling | Wages | Both classified, labels differ | 4.13% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is $2,853,025.18; the workbook gives an amount for only 1 of these flows, so the other 40 are not covered.

**Sample transactions**

**Gambling difference analysis summary:**

Gambling agreement on the union basis is 88.13%, and the union covers 4.15% of all transactions. Among mismatches the one-sided bias is clearly toward Illion: Illion-only recognition is 8.01% versus 3.86% for finv-only.

Gambling-related differences are 2.33% of all differences. Differences concentrate in the Gambling → (blank) flow (50.82% of Gambling-related differences); finv-only recognition (no Illion label) is 25.86%; Illion-only recognition (no finv label) is 50.82%; transfer inflows from the transfer categories (External Transfers → Gambling, Internal Transfer → Gambling) are 4.52%. Overall, Gambling differences are dispersed; sample the main flows above before changing rules.

---

### 3.2.2 Rent Deep Dive

Rent: agreement is 73.34%, and the core tension is finv classifying "Rent" more broadly. Differences concentrate in the External Transfers → Rent flow (0.27% of all differences, — in value).

**Category detail**

**Definition:** Periodic rental payments.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Rent | 0.54% | 0.63% | 0.68% | 0.50% | 73.34% | 7.19% | 19.47% |

**Interpretation:** Illion covers 0.54% of all transactions and finv 0.63%; the union of the two sides spans 0.68% of all transactions and the intersection 0.50%, giving an agreement rate of 73.34% on the union basis. Illion-only and finv-only shares of the union are 7.19% and 19.47% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | External Transfers | Rent | Both classified, labels differ | 31.50% | — |
| 2 | Internal Transfer | Rent | Both classified, labels differ | 25.87% | — |
| 3 | Rent | (blank) | Illion only | 9.75% | — |
| 4 | Rent | External Transfers | Both classified, labels differ | 8.72% | — |
| 5 | Rent | Wages | Both classified, labels differ | 5.46% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is —; the workbook gives an amount for only 0 of these flows, so the other 53 are not covered.

**Sample transactions**

**Flows between Rent and adjacent categories:**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | External Transfers | Rent | Both classified, labels differ | 31.50% | — |
| 2 | Internal Transfer | Rent | Both classified, labels differ | 25.87% | — |
| 3 | Rent | External Transfers | Both classified, labels differ | 8.72% | — |
| 4 | Home Improvement | Rent | Both classified, labels differ | 1.72% | — |
| 5 | Utilities | Rent | Both classified, labels differ | 0.39% | — |
| 6 | Rent | Internal Transfer | Both classified, labels differ | 0.36% | — |
| 7 | Rent | Home Improvement | Both classified, labels differ | 0.01% | — |
| 8 | Rent | Utilities | Both classified, labels differ | 0.00% | — |

**Rent difference analysis summary:**

Rent aligns well: agreement on the union basis is 73.34%, and the union covers only 0.68% of all transactions. Among mismatches the one-sided bias is clearly toward finv: finv-only recognition is 19.47% versus 7.19% for Illion-only.

Rent-related differences account for 0.85% of all differences. The two dominant flows are transfer inflows into Rent (Internal Transfer → Rent and External Transfers → Rent) at 57.37% of Rent-related differences and finv-only recognition (no Illion label) at 4.48%; Internal Transfer → Rent alone accounts for 25.87% of Rent-related differences (worth —); Illion-only recognition adds 9.75% of Rent-related differences. Transfer inflows together are 57.37% of Rent-related differences — close to three in five — so the root cause is the boundary between rent payments and transfers: a meaningful share of rent is being paid by transfer and lands outside Rent in Illion. We recommend reviewing rules for transfer counterparties that look like rent payees (property managers, strata and real-estate agents) and treating likely-rent payees as Rent first.

---

### 3.2.3 Retail Deep Dive

Retail: agreement is only 43.96%, and the core tension is finv classifying "Retail" more broadly. Differences concentrate in the (blank) → Retail flow (2.24% of all differences, — in value).

**Category detail**

**Definition:** Retail purchases other than department stores.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Retail | 1.21% | 1.67% | 🔴 2.00% | 0.88% | 🔴 43.96% | 16.49% | 39.55% |

**Interpretation:** Illion covers 1.21% of all transactions and finv 1.67%; the union of the two sides spans 2.00% of all transactions and the intersection 0.88%, giving an agreement rate of 43.96% on the union basis. Illion-only and finv-only shares of the union are 16.49% and 39.55% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | (blank) | Retail | finv only | 42.28% | — |
| 2 | Retail | (blank) | Illion only | 20.05% | — |
| 3 | External Transfers | Retail | Both classified, labels differ | 9.66% | $2,560,148.62 |
| 4 | Groceries | Retail | Both classified, labels differ | 4.40% | — |
| 5 | Dining Out | Retail | Both classified, labels differ | 3.33% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is $2,560,148.62; the workbook gives an amount for only 1 of these flows, so the other 62 are not covered.

**Sample transactions**

**Retail difference analysis summary:**

Retail agreement on the union basis is 43.96%, and the union covers 2.00% of all transactions. Among mismatches the one-sided bias is clearly toward finv: finv-only recognition is 39.55% versus 16.49% for Illion-only.

Retail-related differences are 5.30% of all differences. Differences are dominated by finv-only recognition (no Illion label), at 42.28% of Retail-related differences; Illion-only recognition (no finv label) is 20.05%; transfer inflows from the transfer categories (External Transfers → Retail, Internal Transfer → Retail) are 10.55%. The root cause of Retail's gap is the boundary between retail payments and transfers: retail purchases paid by transfer are booked as Retail by finv but as transfers by Illion, and finv's one-sided rows extend that broader treatment to retail merchants Illion leaves unclassified; the net effect is that finv recognises retail spend more broadly. We recommend checking rules for transfer counterparties with retail characteristics (supermarkets, convenience stores) and sampling finv-only Retail rows to confirm they are reasonable.

### 3.2.4 Information Deep Dive

Information: agreement is only 0.45%, and the core tension is finv classifying "Information" more broadly. Differences concentrate in the (blank) → Information flow (3.34% of all differences, — in value).

**Category detail**

**Definition:** Information and media spend — newspapers, news/info subscriptions and paid online content.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Information | 0.20% | 0.74% | 🔴 0.93% | 0.00% | 🔴 0.45% | 20.54% | 79.01% |

**Interpretation:** Illion covers 0.20% of all transactions and finv 0.74%; the union of the two sides spans 0.93% of all transactions and the intersection 0.00%, giving an agreement rate of 0.45% on the union basis. Illion-only and finv-only shares of the union are 20.54% and 79.01% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | (blank) | Information | finv only | 76.14% | — |
| 2 | Information | (blank) | Illion only | 12.82% | — |
| 3 | Information | Non SACC Loans | Both classified, labels differ | 2.17% | — |
| 4 | Information | Subscription TV | Both classified, labels differ | 1.68% | — |
| 5 | Information | SACC Loans | Both classified, labels differ | 1.26% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is —; the workbook gives an amount for only 0 of these flows, so the other 57 are not covered.

**Sample transactions**

**Information difference analysis summary:**

Information is the widest mismatch in the expense segment: agreement on the union basis is only 0.45%, and the union covers 0.93% of all transactions. Among mismatches the one-sided bias is clearly toward finv: finv-only recognition is 79.01% versus 20.54% for Illion-only.

Information-related differences are 4.39% of all differences, where finv-only recognition (no Illion label) is 76.14% and Illion-only recognition (no finv label) is 12.82%.  Overall, finv's Information recognition is materially broader than Illion's (coverage 0.74% vs 0.20%). We recommend adopting finv's basis, deciding explicitly whether Information may include zero-amount items, reviewing finv's information-service rules and Illion's boundary, and either confirming the added recognition or re-classifying fee-waiver/notification rows separately.

### 3.2.5 Donations Deep Dive

Donations: agreement is only 49.68%, and the core tension is Illion classifying "Donations" more broadly. Differences concentrate in the Donations → (blank) flow (0.14% of all differences, — in value).

**Category detail**

**Definition:** Charitable giving — donations and giving subscriptions.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Donations | 0.10% | 0.06% | 🔴 0.11% | 🔴 0.05% | 49.68% | 46.21% | 4.11% |

**Interpretation:** Illion covers 0.10% of all transactions and finv 0.06%; the union of the two sides spans 0.11% of all transactions and the intersection 0.05%, giving an agreement rate of 49.68% on the union basis. Illion-only and finv-only shares of the union are 46.21% and 4.11% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | Donations | (blank) | Illion only | 54.83% | — |
| 2 | Donations | External Transfers | Both classified, labels differ | 12.98% | — |
| 3 | Donations | Retail | Both classified, labels differ | 6.46% | — |
| 4 | Donations | Dining Out | Both classified, labels differ | 3.45% | — |
| 5 | External Transfers | Donations | Both classified, labels differ | 2.74% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is —; the workbook gives an amount for only 0 of these flows, so the other 45 are not covered.

**Sample transactions**

**Donations difference analysis summary:**

Donations agreement on the union basis is 49.68%, and the union covers 0.11% of all transactions. Among mismatches the one-sided bias is clearly toward Illion: Illion-only recognition is 46.21% versus 4.11% for finv-only.

Donations-related differences are 0.25% of all differences. Differences are dominated by Illion-only recognition (no finv label), at 54.83% of Donations-related differences; finv-only recognition (no Illion label) is 2.58%; transfer inflows from the transfer categories (External Transfers → Donations, Internal Transfer → Donations) are 2.76%. The root cause of Donations' gap runs the other way from most red categories: finv under-recognises charitable giving — Illion flags giving-style transactions that finv leaves unlabelled, so the gap is a finv coverage shortfall rather than a boundary dispute. Given the category is small (union 0.11% of all transactions), we recommend adding giving-style merchants/keywords to finv and re-running the comparison.

### 3.2.6 Automotive Deep Dive

Automotive: agreement is 72.88%, and the core tension is finv classifying "Automotive" more broadly. Differences concentrate in the External Transfers → Automotive flow (1.26% of all differences, $6,463,658.47 in value).

**Category detail**

**Definition:** Vehicle-related spend — fuel, servicing, parking and non-insurance car costs.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Automotive | 2.78% | 3.05% | 3.37% | 2.45% | 72.88% | 9.56% | 17.56% |

**Interpretation:** Illion covers 2.78% of all transactions and finv 3.05%; the union of the two sides spans 3.37% of all transactions and the intersection 2.45%, giving an agreement rate of 72.88% on the union basis. Illion-only and finv-only shares of the union are 9.56% and 17.56% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | External Transfers | Automotive | Both classified, labels differ | 29.15% | $6,463,658.47 |
| 2 | (blank) | Automotive | finv only | 15.76% | — |
| 3 | Groceries | Automotive | Both classified, labels differ | 12.52% | $293,917.19 |
| 4 | Automotive | (blank) | Illion only | 12.41% | — |
| 5 | Automotive | Groceries | Both classified, labels differ | 4.71% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is $6,757,575.66; the workbook gives an amount for only 2 of these flows, so the other 61 are not covered.

**Sample transactions**

**Automotive difference analysis summary:**

Automotive agreement on the union basis is 72.88%, and the union covers 3.37% of all transactions. Among mismatches one-sided recognition is balanced (Illion-only 9.56% vs finv-only 17.56%).

Automotive-related differences are 4.32% of all differences. Differences concentrate in the External Transfers → Automotive flow (29.15% of Automotive-related differences); finv-only recognition (no Illion label) is 15.76%; Illion-only recognition (no finv label) is 12.41%. Automotive differences have no single root cause; they reflect systematic boundary disagreements in how vehicle-related spend (fuel, servicing, insurance) is assigned by the two rule books: the main flows are bidirectional between Groceries and Automotive (17.23% combined) and between the transfer categories and Automotive (33.86%), plus Automotive → Entertainment (2.77%), while one-sided rows on both sides add further volume, so sampling before rule changes is the right next step: start with Groceries (car accessories bought at supermarkets) and transfers (cars paid by transfer), then align the rules.

---

## 3.3 Transfers Segment

Transfers differences concentrate in External Transfers (1,200,127 related rows, 41.82% of all differences) and Internal Transfer (539,791 rows, 18.81%); the largest split inside the segment is the internal-vs-external definition: External Transfers → Internal Transfer carries 449,441 movements ($90,522,059.95, 15.66% of all differences) that Illion reads as external transfers while finv, using transaction associations across a customer's own cards/accounts, reads as transfers internal to the same bank; the remainder is mostly Illion-only External Transfers (338,434 rows) plus the income boundaries with All Other Credits and Wages; Internal Transfer itself agrees strongly (81.69%); its differences are largely this reassignment.

The transfers segment holds External Transfers and Internal Transfer, tracked as a neutral flow: they are excluded from income/expense totals but included in overall coverage, difference-rate and classification-migration analysis. Beyond the seven metrics we add the credit/debit split and the in/out direction, plus boundary checks against Wages, All Other Credits, Rent and the other transfer category.

### 3.3.1 External Transfers

External Transfers: agreement is only 56.04%, and the core tension is a definitional split over internal-vs-external transfers rather than a plain coverage gap: 449,441 movements ($90,522,059.95, 37.45% of External Transfers-related differences and 15.66% of all differences) are classified as external transfers on the Illion side, while finv links the same batch through transaction associations across a customer's own cards/accounts and reads them as transfers internal to the same bank; Illion-only rows (no finv category) add 338,434, so Illion's coverage looks broader (19.28% vs 12.13%), but that breadth is mostly finv's reassignment to internal transfers plus Illion-only recognition rather than genuine over-coverage. A typical case, "From my account 0053262953 - Internal Transfer - Receipt 221373" ($160,282.74), is classified External Transfers → Internal Transfer and is consistent with that view.

**Category detail**

**Definition:** External transfers — movements between the account population and accounts outside it.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| External Transfers | 19.28% | 12.13% | 20.13% | 🔴 11.28% | 56.04% | 39.74% | 4.23% |

**Interpretation:** Illion covers 19.28% of all transactions and finv 12.13%; the union of the two sides spans 20.13% of all transactions and the intersection 11.28%, giving an agreement rate of 56.04% on the union basis. Illion-only and finv-only shares of the union are 39.74% and 4.23% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | External Transfers | Internal Transfer | Both classified, labels differ | 37.45% | $90,522,059.95 |
| 2 | External Transfers | (blank) | Illion only | 28.20% | $75,974,937.40 |
| 3 | External Transfers | Wages | Both classified, labels differ | 10.78% | $41,035,434.64 |
| 4 | External Transfers | All Other Credits | Both classified, labels differ | 4.11% | $19,085,209.21 |
| 5 | External Transfers | Automotive | Both classified, labels differ | 3.01% | $6,463,658.47 |

**Direction & value:** Of the difference rows, 60.00% are credit and 40.00% debit; combined difference value is $239,135,027.95; the workbook gives an amount for only 7 of these flows, so the other 61 are not covered.

**Sample transactions**

| Date | Transaction description | Counterparty | Dir. | Amount | Illion → finv |
| :---: | :--- | :--- | :---: | ---: | :---: |
| 2026-07-13 | MISCELLANEOUS DEBIT WITHDRAWAL | (blank) | debit | $175,000.00 | External Transfers → (blank) |
| 2026-03-23 | From my account 0053262953 - Internal Transfer - Receipt 221373 | Miscellaneous Funds Transfer | credit | $160,282.74 | External Transfers → Internal Transfer |
| 2026-03-13 | Transfer from xx4842 CommBank app, N631367780714 | CBA Funds Transfer | credit | $145,000.00 | External Transfers → Internal Transfer |
| 2026-03-13 | Transfer to xx4842 CommBank app, N631367888635 | CBA Funds Transfer | debit | $145,000.00 | External Transfers → Internal Transfer |
| 2026-02-19 | SWIFT DEPOSIT - SWIFT transfer - Receipt 771287 | (blank) | credit | $140,274.28 | External Transfers → (blank) |

### 3.3.2 Internal Transfer

Internal Transfer: agreement is 81.69%, a strong consensus, but the core tension is how each side recognises internal transfers: finv links a customer's own cards/accounts and recognises in-bank movements as internal transfers (External Transfers → Internal Transfer: 449,441 movements worth $90,522,059.95, 83.26% of Internal Transfer-related differences and 15.66% of all differences), whereas Illion reads the same batch as external transfers. finv's one-sided share (15.44%) exceeds Illion's (2.87%), so finv's coverage is somewhat broader (21.12% vs 18.38%) — an extension of finv's card-linkage recognition, not Illion under-recognition. A typical case, "From my account 0053262953 - Internal Transfer - Receipt 221373" ($160,282.74), is classified External Transfers → Internal Transfer and is consistent with that view.

**Category detail**

**Definition:** Internal transfers — movements between accounts within the same population.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Internal Transfer | 18.38% | 21.12% | 🟢 21.74% | 17.76% | 81.69% | 2.87% | 15.44% |

**Interpretation:** Illion covers 18.38% of all transactions and finv 21.12%; the union of the two sides spans 21.74% of all transactions and the intersection 17.76%, giving an agreement rate of 81.69% on the union basis. Illion-only and finv-only shares of the union are 2.87% and 15.44% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | External Transfers | Internal Transfer | Both classified, labels differ | 83.26% | $90,522,059.95 |
| 2 | Internal Transfer | External Transfers | Both classified, labels differ | 6.15% | — |
| 3 | Internal Transfer | (blank) | Illion only | 2.98% | — |
| 4 | Internal Transfer | Wages | Both classified, labels differ | 2.19% | — |
| 5 | Internal Transfer | Fees | Both classified, labels differ | 1.84% | — |

**Direction & value:** Of the difference rows, 80.00% are credit and 20.00% debit; combined difference value is $90,522,059.95; the workbook gives an amount for only 1 of these flows, so the other 63 are not covered.

**Sample transactions**

| Date | Transaction description | Counterparty | Dir. | Amount | Illion → finv |
| :---: | :--- | :--- | :---: | ---: | :---: |
| 2026-03-23 | From my account 0053262953 - Internal Transfer - Receipt 221373 | Miscellaneous Funds Transfer | credit | $160,282.74 | External Transfers → Internal Transfer |
| 2026-03-13 | Transfer from xx4842 CommBank app, N631367780714 | CBA Funds Transfer | credit | $145,000.00 | External Transfers → Internal Transfer |
| 2026-03-13 | Transfer to xx4842 CommBank app, N631367888635 | CBA Funds Transfer | debit | $145,000.00 | External Transfers → Internal Transfer |
| 2026-06-02 | ANZ M-BANKING FUNDS TFER TRANSFER 978206 FROM 802928468 | ANZ Funds Transfer | credit | $109,143.42 | External Transfers → Internal Transfer |
| 2026-03-15 | Transfer from xx4842 CommBank app, N831566196604 | CBA Funds Transfer | credit | $100,000.00 | External Transfers → Internal Transfer |

**Transfer boundary notes**

External Transfers vs Wages: confusion runs both ways; 1 of the 2 directions carry $41,035,434.64, and the workbook gives no amount for the other 1.

External Transfers vs All Other Credits: confusion runs both ways; 1 of the 2 directions carry $19,085,209.21, and the workbook gives no amount for the other 1.

External Transfers vs Rent: confusion runs both ways; the workbook gives no amount for either direction.

External Transfers vs Internal Transfer: confusion runs both ways; 1 of the 2 directions carry $90,522,059.95, and the workbook gives no amount for the other 1.

**Transfer differences summary:**

The two transfer categories diverge sharply: External Transfers agrees at 56.04% on the union basis (2,729,837-row union, 20.13% of all transactions), whereas Internal Transfer reaches 81.69% (2,947,823 rows, 21.74%). Their related differences are 41.82% and 18.81% of all differences respectively, and the External Transfers → Internal Transfer flow alone carries 449,441 movements worth $90,522,059.95 — the largest single disputed flow in value across the whole report; its rows split 75.00% credit / 25.00% debit — genuine two-way movement rather than one-directional outward payment.

Mechanism and how to fix it. The disputed rows carry clear signatures — same-amount paired Internet Deposit/Withdrawal entries to/from the same account (e.g. 2026-03-23 "From my account 0053262953 - Internal Transfer - Receipt 221373", $160,282.74) and card-to-card CommBank app transfers ("Transfer to/from xx…"): finv links such movements across a customer's own cards/accounts and recognises them as internal to the bank, while Illion, without that linkage, reads the counterparty/description (Funds Transfer etc.) as external — so the same batch ends up classified differently on the two sides. Recommended actions: 1) sample the External Transfers → Internal Transfer rows to confirm the two linked cards/accounts genuinely belong to the same customer; if they do, take finv's linkage-based result as the reference for internal transfers, or feed the card-group linkage back to Illion so both sides align; 2) handle the Illion-only External Transfers rows (338,434) separately from the internal-vs-external definition dispute, and check whether they are Illion over-recognition or finv misses; 3) treat this flow as P1 — it is the segment's largest by both volume and value.

Differences between transfers and income (Wages / All Other Credits) concentrate on the credit side, so the receipts scenario is where the transfer-vs-income boundary blurs; against Rent they concentrate on the debit side, suggesting rent paid by transfer is being labelled as a transfer.

> Handling principle for transfers: treated as a neutral flow, excluded from income and expense totals and from net-income calculations, but kept in overall coverage, difference-rate and classification-migration analysis.

---

## 3.4 Liabilities Segment

Liabilities differences concentrate in Non SACC Loans and SACC Loans; the segment's differences are driven mainly by finv's broader Liabilities recognition; the boundary with External Transfers is the main cross-segment leak. By contrast, Overdrawn shows strong consensus (98.85% agreement) and low risk.

The liabilities segment holds 8 categories; SACC Loans, Non SACC Loans, Dishonours and Credit Card Repayments are analysed below (top flows, direction, amounts and samples). The remaining four (Debt Collection, Overdrawn, Debt Consolidation, Unknown Loans) are rolled up in Appendix 4.3. Loan lifecycle, counterparty matching, Dishonours cases and the SACC / Non-SACC cross-matrix are covered by the dedicated Liability report.

### 3.4.1 SACC Loans

SACC Loans: agreement is only 41.50%, and the core tension is Illion classifying "SACC Loans" more broadly. Differences concentrate in the SACC Loans → Non SACC Loans flow (1.12% of all differences, $6,204,336.72 in value).

**Category detail**

**Definition:** SACC loan activity (in-house lending products, including repayment and drawdown).

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| SACC Loans | 0.97% | 0.79% | 🔴 1.25% | 🔴 0.52% | 41.50% | 36.61% | 21.89% |

**Interpretation:** Illion covers 0.97% of all transactions and finv 0.79%; the union of the two sides spans 1.25% of all transactions and the intersection 0.52%, giving an agreement rate of 41.50% on the union basis. Illion-only and finv-only shares of the union are 36.61% and 21.89% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | SACC Loans | Non SACC Loans | Both classified, labels differ | 32.45% | $6,204,336.72 |
| 2 | Non SACC Loans | SACC Loans | Both classified, labels differ | 30.34% | $5,297,718.44 |
| 3 | SACC Loans | Unknown Loans | Both classified, labels differ | 29.41% | $2,952,338.33 |
| 4 | Dishonours | SACC Loans | Both classified, labels differ | 2.07% | — |
| 5 | Information | SACC Loans | Both classified, labels differ | 1.60% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is $14,454,393.49; the workbook gives an amount for only 3 of these flows, so the other 37 are not covered.

**Sample transactions**

### 3.4.2 Credit Card Repayments

Credit Card Repayments: agreement is 70.19%, and the core tension is finv classifying "Credit Card Repayments" more broadly. Differences concentrate in the External Transfers → Credit Card Repayments flow (0.51% of all differences, $3,493,579.66 in value).

**Category detail**

**Definition:** Credit-card repayments.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Credit Card Repayments | 0.48% | 0.60% | 0.64% | 0.45% | 70.19% | 6.03% | 23.78% |

**Interpretation:** Illion covers 0.48% of all transactions and finv 0.60%; the union of the two sides spans 0.64% of all transactions and the intersection 0.45%, giving an agreement rate of 70.19% on the union basis. Illion-only and finv-only shares of the union are 6.03% and 23.78% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | External Transfers | Credit Card Repayments | Both classified, labels differ | 56.85% | $3,493,579.66 |
| 2 | Non SACC Loans | Credit Card Repayments | Both classified, labels differ | 12.48% | — |
| 3 | Credit Card Repayments | (blank) | Illion only | 6.13% | — |
| 4 | Credit Card Repayments | Non SACC Loans | Both classified, labels differ | 4.17% | — |
| 5 | Credit Card Repayments | External Transfers | Both classified, labels differ | 3.23% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is $3,493,579.66; the workbook gives an amount for only 1 of these flows, so the other 36 are not covered.

**Sample transactions**

### 3.4.3 Dishonours

Dishonours: agreement is 81.00%, and the core tension is Illion classifying "Dishonours" more broadly. Differences concentrate in the Dishonours → Non SACC Loans flow (0.25% of all differences, — in value).

**Category detail**

**Definition:** Dishonours — bounced cheques and failed direct debits.

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Dishonours | 0.83% | 0.68% | 🟢 0.84% | 0.68% | 81.00% | 18.40% | 0.59% |

**Interpretation:** Illion covers 0.83% of all transactions and finv 0.68%; the union of the two sides spans 0.84% of all transactions and the intersection 0.68%, giving an agreement rate of 81.00% on the union basis. Illion-only and finv-only shares of the union are 18.40% and 0.59% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | Dishonours | Non SACC Loans | Both classified, labels differ | 33.24% | — |
| 2 | Dishonours | (blank) | Illion only | 26.82% | — |
| 3 | Dishonours | SACC Loans | Both classified, labels differ | 9.50% | — |
| 4 | Dishonours | Insurance | Both classified, labels differ | 5.25% | — |
| 5 | Dishonours | Gyms and other memberships | Both classified, labels differ | 5.06% | — |

**Direction & value:** The difference rows carry no recorded direction; combined difference value is —; the workbook gives an amount for only 0 of these flows, so the other 38 are not covered.

**Sample transactions**

### 3.4.4 Non SACC Loans

Non SACC Loans: agreement is 86.37%, and the core tension is a boundary misalignment between the two sides on "Non SACC Loans". Differences concentrate in the (blank) → Non SACC Loans flow (2.08% of all differences, — in value).

**Category detail**

**Definition:** Non-SACC loan activity (other lending products).

**Key indicators**

| Category | Illion cov. | finv cov. | Union % | Intersec. % | Agreement (union) | Illion-only | finv-only |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Non SACC Loans | 11.98% | 11.95% | 🟢 12.84% | 11.09% | 86.37% | 6.95% | 6.68% |

**Interpretation:** Illion covers 11.98% of all transactions and finv 11.95%; the union of the two sides spans 12.84% of all transactions and the intersection 11.09%, giving an agreement rate of 86.37% on the union basis. Illion-only and finv-only shares of the union are 6.95% and 6.68% respectively.

**Top difference flows**

| Rank | Illion category | finv category | Difference type | Share of cat. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | (blank) | Non SACC Loans | finv only | 25.10% | — |
| 2 | Non SACC Loans | Unknown Loans | Both classified, labels differ | 24.21% | $6,215,723.61 |
| 3 | SACC Loans | Non SACC Loans | Both classified, labels differ | 13.52% | $6,204,336.72 |
| 4 | Non SACC Loans | SACC Loans | Both classified, labels differ | 12.65% | $5,297,718.44 |
| 5 | Non SACC Loans | (blank) | Illion only | 4.39% | $2,881,235.75 |

**Direction & value:** Of the difference rows, 0.00% are credit and 100.00% debit; combined difference value is $20,599,014.52; the workbook gives an amount for only 4 of these flows, so the other 54 are not covered.

**Sample transactions**

| Date | Transaction description | Counterparty | Dir. | Amount | Illion → finv |
| :---: | :--- | :--- | :---: | ---: | :---: |
| 2026-02-19 | BPAY BPAY Pepper Asset Finance REF: 20260219144756689 | Pepper Money | debit | $26,949.69 | Non SACC Loans → Unknown Loans |
| 2026-07-17 | ANZ MOBILE BANKING PAYMENT 580117 TO Credit24 | Credit24 | debit | $6,254.53 | Non SACC Loans → Unknown Loans |
| 2026-06-24 | CCFS 1503370765 - BPAY Bill Payment - Receipt 327564 To CCFS 1503370765 | Credit Corp | debit | $5,830.00 | Non SACC Loans → Unknown Loans |
| 2026-03-25 | PAYMENT BY AUTHORITY TO SECURE FUNDING P 4654506 | Liberty Financial | debit | $5,567.42 | Non SACC Loans → Unknown Loans |
| 2026-02-19 | Jacaranda Finance INTERNET AUS | Jacaranda Finance | debit | $5,117.58 | Non SACC Loans → Unknown Loans |

**Liability segment difference ranking and top flows:**

| Rank | Illion category | finv category | Difference type | Share of seg. diffs | Diff amount |
| :---: | :--- | :--- | :--- | :---: | ---: |
| 1 | (blank) | Non SACC Loans | finv only | 18.93% | — |
| 2 | Non SACC Loans | Unknown Loans | Both classified, labels differ | 18.26% | $6,215,723.61 |
| 3 | SACC Loans | Non SACC Loans | Both classified, labels differ | 10.20% | $6,204,336.72 |
| 4 | Non SACC Loans | SACC Loans | Both classified, labels differ | 9.54% | $5,297,718.44 |
| 5 | SACC Loans | Unknown Loans | Both classified, labels differ | 9.24% | $2,952,338.33 |
| 6 | External Transfers | Credit Card Repayments | Both classified, labels differ | 4.64% | $3,493,579.66 |
| 7 | Non SACC Loans | (blank) | Illion only | 3.31% | $2,881,235.75 |
| 8 | Dishonours | Non SACC Loans | Both classified, labels differ | 2.28% | — |

> Root-cause analysis of the liability segment (loan lifecycle, counterparty matching, Dishonours cases, Unknown Loans drivers) is in the dedicated Liability report; this report keeps the deep dives on important liability categories, the segment difference ranking and the category-level positioning.

---

## 3.5 Cross-Segment Recommendations

Combining the overall difference structure with the category deep dives, we recommend the following, in priority order:

| Priority | Focus | Recommended action | Supporting data |
| :---: | :--- | :--- | :--- |
| P1 | Validate finv's added recognition | Sample the finv-only rows (28.69% of differences) to separate genuine coverage expansion from over-recognition | All Other Credits, Retail, Dining Out, Unknown Loans |
| P1 | Fix the transfer boundary | Split credit/debit handling and set recognition priority between transfers and income (Wages / All Other Credits) | External Transfers → Internal Transfer (15.66%); External Transfers → All Other Credits (1.72%) |
| P1 | Fix loan-category boundaries | Re-check the knowledge base and aliases around Non SACC / SACC Loans and Unknown Loans | Non SACC Loans → Unknown Loans (2.00%); SACC Loans → Unknown Loans (1.01%) |
| P2 | Fix the Rent boundary | Review Rent vs transfers / Utilities / Home Improvement rules and build a Rent regression set | External Transfers → Rent (0.27%) and similar flows |
| P2 | Fix high-frequency spend boundaries | Build regression sets for Groceries, Dining Out, Retail, Automotive and Gambling | Groceries → Dining Out (0.68%); Groceries → Retail (0.23%); Groceries → Automotive (0.54%) |
| P3 | Re-check remaining categories | Read low-volume category ratios with sample size in mind; check the flagged ratios in the appendix | Appendix per-category ratios |

Going forward: after every model or knowledge-base change, recompute the seven ratios for all 36 categories and watch agreement (union), union share, finv-only and Illion-only shares, and the Rent and transfer difference flows in particular.

---

# 4. Appendix: Category Metrics and Roll-ups

4.1 lists all 36 categories with their seven ratios; 4.2 covers the 21 expense categories outside the Rent and Gambling deep dives (Retail, Information, Donations and Automotive were already analysed in 3.2.3–3.2.6 and are repeated here for reference, without commentary); 4.3 covers the remaining 4 liability categories. The three sections use exactly the same definitions as Sections 2 and 3 and are intended for quick scanning and rule-checking.

**4.1 Full Category Metrics (36 categories)**

**Income:**

| Category | Illion cov. | finv cov. | Agreement (union) | Illion-only | finv-only | Priority |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| All Other Credits | 0.61% | 1.28% | 🔴 7.50% | 26.97% | 🔴 65.53% | P1 |
| Wages | 1.51% | 3.23% | 🔴 43.67% | 2.09% | 🔴 54.24% | P2 |
| Centrelink | 0.35% | 0.32% | 🟢 89.35% | 9.90% | 0.75% | P3 |

**Expenses:**

| Category | Illion cov. | finv cov. | Agreement (union) | Illion-only | finv-only | Priority |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Information | 0.20% | 0.74% | 🔴 0.45% | 20.54% | 🔴 79.01% | P2 |
| Pet Care | 0.08% | 0.22% | 🔴 29.45% | 5.69% | 🔴 64.87% | P2 |
| Retail | 1.21% | 1.67% | 🔴 43.96% | 16.49% | 🔴 39.55% | P2 |
| Entertainment | 0.51% | 0.76% | 🔴 44.68% | 13.43% | 🔴 41.89% | P2 |
| Education | 0.21% | 0.21% | 🔴 46.17% | 26.77% | 27.06% | P2 |
| Donations | 0.10% | 0.06% | 🔴 49.68% | 🔴 46.21% | 4.11% | P2 |
| Gyms and other memberships | 0.83% | 0.92% | 51.23% | 20.70% | 28.06% | P2 |
| Subscription TV | 1.12% | 1.93% | 55.57% | 1.60% | 🔴 42.83% | P3 |
| Travel | 0.35% | 0.39% | 55.96% | 17.59% | 26.45% | P2 |
| Personal Care | 0.32% | 0.32% | 62.18% | 18.54% | 19.29% | P2 |
| Home Improvement | 0.32% | 0.27% | 71.76% | 21.32% | 6.92% | P2 |
| Health | 0.72% | 0.70% | 71.93% | 15.37% | 12.70% | P2 |
| Automotive | 2.78% | 3.05% | 72.88% | 9.56% | 17.56% | P2 |
| Rent | 0.54% | 0.63% | 73.34% | 7.19% | 19.47% | P2 |
| Utilities | 0.30% | 0.28% | 73.87% | 15.28% | 10.85% | P2 |
| Dining Out | 8.20% | 8.13% | 77.11% | 11.83% | 11.06% | P1 |
| Insurance | 0.45% | 0.40% | 77.22% | 16.50% | 6.28% | P2 |
| Groceries | 7.22% | 6.83% | 🟢 84.62% | 10.29% | 5.09% | P1 |
| Transport | 1.63% | 1.65% | 🟢 86.21% | 6.42% | 7.37% | P2 |
| Fees | 2.20% | 2.46% | 🟢 86.80% | 1.30% | 11.90% | P3 |
| Gambling | 3.99% | 3.82% | 🟢 88.13% | 8.01% | 3.86% | P1 |
| Telecommunications | 0.71% | 0.70% | 🟢 92.55% | 4.72% | 2.73% | P3 |
| Department Stores | 0.59% | 0.57% | 🟢 94.18% | 4.84% | 0.98% | P3 |

**Transfers:**

| Category | Illion cov. | finv cov. | Agreement (union) | Illion-only | finv-only | Priority |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| External Transfers | 19.28% | 12.13% | 56.04% | 🔴 39.74% | 4.23% | P1 |
| Internal Transfer | 18.38% | 21.12% | 🟢 81.69% | 2.87% | 15.44% | P2 |

**Liabilities:**

| Category | Illion cov. | finv cov. | Agreement (union) | Illion-only | finv-only | Priority |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Unknown Loans | 0.00% | 0.66% | 🔴 0.00% | 0.00% | 🔴 100.00% | P3 |
| SACC Loans | 0.97% | 0.79% | 🔴 41.50% | 🔴 36.61% | 21.89% | P1 |
| Credit Card Repayments | 0.48% | 0.60% | 70.19% | 6.03% | 23.78% | P2 |
| Debt Consolidation | 0.04% | 0.04% | 79.73% | 3.30% | 16.97% | P3 |
| Dishonours | 0.83% | 0.68% | 🟢 81.00% | 18.40% | 0.59% | P2 |
| Debt Collection | 0.23% | 0.22% | 🟢 81.70% | 10.57% | 7.74% | P3 |
| Non SACC Loans | 11.98% | 11.95% | 🟢 86.37% | 6.95% | 6.68% | P1 |
| Overdrawn | 0.26% | 0.26% | 🟢 98.85% | 0.57% | 0.58% | P3 |

**4.2 Expenses — remaining categories (21)**

| Category | Illion cov. | finv cov. | Agreement (union) | Illion-only | finv-only | Priority |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| Information | 0.20% | 0.74% | 🔴 0.45% | 20.54% | 🔴 79.01% | P2 |
| Pet Care | 0.08% | 0.22% | 🔴 29.45% | 5.69% | 🔴 64.87% | P2 |
| Retail | 1.21% | 1.67% | 🔴 43.96% | 16.49% | 🔴 39.55% | P2 |
| Entertainment | 0.51% | 0.76% | 🔴 44.68% | 13.43% | 🔴 41.89% | P2 |
| Education | 0.21% | 0.21% | 🔴 46.17% | 26.77% | 27.06% | P2 |
| Donations | 0.10% | 0.06% | 🔴 49.68% | 🔴 46.21% | 4.11% | P2 |
| Gyms and other memberships | 0.83% | 0.92% | 51.23% | 20.70% | 28.06% | P2 |
| Subscription TV | 1.12% | 1.93% | 55.57% | 1.60% | 🔴 42.83% | P3 |
| Travel | 0.35% | 0.39% | 55.96% | 17.59% | 26.45% | P2 |
| Personal Care | 0.32% | 0.32% | 62.18% | 18.54% | 19.29% | P2 |
| Home Improvement | 0.32% | 0.27% | 71.76% | 21.32% | 6.92% | P2 |
| Health | 0.72% | 0.70% | 71.93% | 15.37% | 12.70% | P2 |
| Automotive | 2.78% | 3.05% | 72.88% | 9.56% | 17.56% | P2 |
| Utilities | 0.30% | 0.28% | 73.87% | 15.28% | 10.85% | P2 |
| Dining Out | 8.20% | 8.13% | 77.11% | 11.83% | 11.06% | P1 |
| Insurance | 0.45% | 0.40% | 77.22% | 16.50% | 6.28% | P2 |
| Groceries | 7.22% | 6.83% | 🟢 84.62% | 10.29% | 5.09% | P1 |
| Transport | 1.63% | 1.65% | 🟢 86.21% | 6.42% | 7.37% | P2 |
| Fees | 2.20% | 2.46% | 🟢 86.80% | 1.30% | 11.90% | P3 |
| Telecommunications | 0.71% | 0.70% | 🟢 92.55% | 4.72% | 2.73% | P3 |
| Department Stores | 0.59% | 0.57% | 🟢 94.18% | 4.84% | 0.98% | P3 |

**4.3 Liabilities — remaining categories (4)**

| Category | Illion cov. | finv cov. | Agreement (union) | Illion-only | finv-only | Priority |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| Unknown Loans | 0.00% | 0.66% | 🔴 0.00% | 0.00% | 🔴 100.00% | P3 |
| Debt Consolidation | 0.04% | 0.04% | 79.73% | 3.30% | 16.97% | P3 |
| Debt Collection | 0.23% | 0.22% | 🟢 81.70% | 10.57% | 7.74% | P3 |
| Overdrawn | 0.26% | 0.26% | 🟢 98.85% | 0.57% | 0.58% | P3 |

Notes on definitions: union share (of all transactions) = category union / all transactions; agreement (union) = intersection / category union; Illion-only and finv-only shares are one-sided rows as a share of the category union. In every “difference flows” table the share column uses that table's own context as the denominator (category detail = the category's related differences; the liability segment table = the segment's differences), i.e. it shows how the category/segment differences are made up, not the share of all differences. Rows the two sides classify differently are counted in both category unions, so per-category agreement rates do not sum to the overall coverage-adjusted rate (77.86%, transaction-deduplicated). Priority comes from the workbook's 建议优先级 column, shown as “-” when missing (the workbook column is '建议优先级'). Traffic lights: red (bold) = flag (agreement < 50% or an only-share > 30%); green (bold) = strong (agreement > 80%). Flow arrows “X → Y” mean Illion classified the row as X and finv as Y; “(blank)” means that side did not classify the row.
