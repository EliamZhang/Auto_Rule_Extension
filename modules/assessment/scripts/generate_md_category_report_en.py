# -*- coding: utf-8 -*-
"""Generate the Markdown BS-CAT category difference report (English).

用法
----
    python scripts/generate_md_category_report_en.py
    python scripts/generate_md_category_report_en.py --input input/xxx.xlsx --output output/xxx.md
    python scripts/generate_md_category_report_en.py --check      # 只做口径校验，不写文件

本文件是**自包含的单文件生成器**：不导入、不修改 `generate_docx_category_report_en.py`，
也不需要 python-docx。正文文案（章节结构、叙述段落、表格布局）与两张图的渲染代码
都是从原脚本逐字复制过来的，因此本报告与英文 docx 报告的形态天然一致；代价是此后
各自演进，docx 版或图脚本改了，这里不会自动跟随。

运行时依赖只有 openpyxl（读底稿）与 matplotlib / numpy / seaborn / pandas（画图，
「十、图表渲染」内延迟导入）。缺画图依赖时只丢图、报告照常生成，`--no-charts`
可完全绕开。

设计原则
--------
1. **报告形态对齐英文 docx 报告**：章节编号、叙述段落、表格布局与 docx 版一致。
   正文逻辑只经一组渲染助手（add_heading / add_para / add_table / …）接触文档对象，
   本文件把这组助手实现为 Markdown 输出，正文函数原样保留。
2. **统计数字一律来自 00_核心对比 / 01_差异诊断地图 / 02_业务聚类对比**，
   不依赖 docx 版的 `Analysis`（其统计口径建立在被截断的 03 明细之上）。
   板块级的「板块内分类不一致笔数」由 01 的 37×37 类别计数矩阵推导：
   矩阵对角 = 交集数量，非对角同板块格 = 板块内分类不一致笔数。
   该推导已逐类别校验（矩阵行列合计 = illion/finv 数量，对角 = 交集数量）。
3. **03_排查明细 仅用于「典型交易样本」**，绝不用作分母或计数。
   底稿中 03 只含 5 个流向、1,048,572 行，是 2,869,569 条差异的截断样本，
   把它当统计基础会系统性低估差异规模。个别叙述（如 Information 的「0 金额占比」）
   本身就是对「明细样本」的描述，样本来源即 03。
4. 金额只有 Top 20 流向可得（01 表的「差异金额」列）。其余流向的金额在底稿中
   不存在，一律显示为「—」而非 $0.00。详见 `fmt_money`。

中文版对应 `scripts/generate_md_category_report.py`。
"""

from __future__ import annotations

import argparse
import heapq
import re
import sys
import warnings
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import xml.etree.ElementTree as ET

from openpyxl import load_workbook

import paths

# 本脚本所在项目的根目录
BASE_DIR = Path(__file__).resolve().parents[1]


# ===========================================================================
# 一、常量与业务定义
# ===========================================================================

EMPTY = "(空)"  # 底稿中缺失分类的占位符

BLUE = "1F4E79"
BLUE_MID = "2E74B5"
BLUE_PALE = "DEEBF7"
GRAY = "595959"
GRAY_PALE = "F2F2F2"
YELLOW_PALE = "FFF2CC"
DARK = "222222"
RED = "C00000"    # 红灯指标：加粗红字
GREEN = "217A57"  # 绿灯指标：加粗绿字

# 指标红绿灯阈值（红灯 = 需关注，绿灯 = 表现良好）
RED_INTER_SHARE_LT = 0.50   # 一致率（类别并集）< 50% → 红灯
RED_ONLY_SHARE_GT = 0.30    # 独有占比（illion / finv）> 30% → 红灯
GREEN_INTER_SHARE_GT = 0.80 # 一致率（类别并集）> 80% → 绿灯

# ---------------------------------------------------------------------------
# 业务板块定义（与报告口径一致：收入 3 / 支出 23 / 转账 2 / 负债 8 = 36）
# ---------------------------------------------------------------------------
GROUPS: Dict[str, List[str]] = {
    "收入类": ["Wages", "Centrelink", "All Other Credits"],
    "支出类": [
        "Information", "Donations", "Education", "Home Improvement", "Insurance",
        "Subscription TV", "Pet Care", "Entertainment", "Retail", "Utilities",
        "Gyms and other memberships", "Personal Care", "Rent", "Health", "Travel",
        "Groceries", "Automotive", "Gambling", "Department Stores", "Telecommunications",
        "Dining Out", "Fees", "Transport",
    ],
    "转账类": ["External Transfers", "Internal Transfer"],
    "负债类": [
        "SACC Loans", "Non SACC Loans", "Credit Card Repayments", "Debt Consolidation",
        "Debt Collection", "Dishonours", "Overdrawn", "Unknown Loans",
    ],
}
GROUP_OF: Dict[str, str] = {
    category: group for group, categories in GROUPS.items() for category in categories
}
GROUP_ORDER: List[str] = ["收入类", "支出类", "转账类", "负债类"]

FOCUS_EXPENSE: set = {"Rent", "Groceries", "Dining Out", "Retail", "Automotive", "Gambling", "Utilities"}
FOCUS_DETAILED: set = (
    {"Wages", "Centrelink", "All Other Credits", "External Transfers", "Internal Transfer"}
    | FOCUS_EXPENSE
)
# v3 口径：第 3 章展开分析的重要类别；其余类别统一纳入第 4 章附录（七项指标汇总）
IMPORTANT_EXPENSE: set = {"Rent", "Gambling"}
IMPORTANT_LIABILITY: set = {"SACC Loans", "Non SACC Loans", "Dishonours", "Credit Card Repayments"}

# 类别业务定义（描述性说明，不推断数据）
CATEGORY_DEFINITION: Dict[str, str] = {
    "Wages": "工资薪金类收入，包括薪资入账、公司发放的工资、奖金、遣散费等。",
    "Centrelink": "政府福利类收入，包括 Centrelink 福利金、政府补贴与救济金。",
    "All Other Credits": "其他贷方入账，非工资、非福利的杂项收款与退款。",
    "Information": "信息资讯类支出，如报刊、资讯订阅、在线内容付费。",
    "Donations": "捐赠类支出，如慈善捐款、捐赠订阅。",
    "Education": "教育类支出，如学费、课程费、教材费。",
    "Home Improvement": "家居装修与改善类支出，如建材、装修服务。",
    "Insurance": "保险类支出，如各类保费缴纳。",
    "Subscription TV": "电视订阅类支出，如流媒体、付费电视、家庭娱乐订阅。",
    "Pet Care": "宠物照护类支出，如宠物食品、兽医、美容。",
    "Entertainment": "娱乐类支出，如电影、演出门票、游戏。",
    "Retail": "零售类消费，涵盖百货以外的零售购物。",
    "Utilities": "公用事业类支出，如水、电、燃气、电话账单等。",
    "Gyms and other memberships": "健身房及其他会员类支出，如健身、俱乐部会费。",
    "Personal Care": "个人护理类支出，如美发、美容、按摩、理疗。",
    "Rent": "房租类支出，周期性租金支付。",
    "Health": "医疗健康类支出，如诊所、药房、牙医、医疗费用。",
    "Travel": "旅行类支出，如机票、酒店、旅行预订、租车。",
    "Groceries": "食品杂货类支出，如超市买菜、日用杂货。",
    "Automotive": "汽车相关支出，如加油、维修、停车、车险以外的车辆费用。",
    "Gambling": "博彩类支出，如投注、彩票、赌场交易。",
    "Department Stores": "百货商场类消费。",
    "Telecommunications": "电信类支出，如手机话费、宽带、通讯设备。",
    "Dining Out": "餐饮外食类支出，如餐厅、快餐、外卖。",
    "Fees": "手续费与杂费类支出，如银行费用、罚款、迟纳金。",
    "Transport": "交通出行类支出，如公交、地铁、出租车、过路费、停车费。",
    "External Transfers": "外部转账，与自身账户体系之外的账户之间的资金转移。",
    "Internal Transfer": "内部转账，账户体系内部的资金转移（同行账户间划转）。",
    "SACC Loans": "SACC 贷款相关交易（银行内贷款产品，含还款与放款）。",
    "Non SACC Loans": "非 SACC 贷款相关交易（其他贷款产品）。",
    "Credit Card Repayments": "信用卡还款类交易。",
    "Debt Consolidation": "债务合并类交易，将多笔债务合并为单笔还款。",
    "Debt Collection": "债务催收类交易，涉及催收机构的还款。",
    "Dishonours": "拒付/退票类交易，如支票退票、直接扣款失败。",
    "Overdrawn": "透支相关交易，账户透支及透支费用。",
    "Unknown Loans": "未识别来源的贷款相关交易，需要进一步核验。",
}

# 差异类型归一化：底稿 03 排查类型 / 01 差异类型 -> 报告口径
DIFF_TYPE_MAP = {
    "分类边界冲突": "双方分类不一致",
    "分类不一致": "双方分类不一致",
    "仅illion有分类": "仅 Illion 有值",
    "仅finv有分类": "仅 finv 有值",
    "finv漏识别": "仅 Illion 有值",
    "finv新增识别": "仅 finv 有值",
}

SHEET_00 = "00_核心对比"
SHEET_01 = "01_差异诊断地图"
SHEET_02 = "02_业务聚类对比"
SHEET_03 = "03_排查明细"

DASH = "—"  # 底稿中不存在的数据，区别于真实的 0

MATRIX_HEADER_ROW = 28  # 01 表：计数矩阵表头所在行
MATRIX_SPAN = 37        # 36 个类别 + 「(空)」

DIFF_TYPE_BOTH = "双方分类不一致"
DIFF_TYPE_ILLION_ONLY = "仅 Illion 有值"
DIFF_TYPE_FINV_ONLY = "仅 finv 有值"

# 02 表的大类口径与本报告不同：Dishonours 归支出类，负债类改称「贷款类」
GROUP5_OVERRIDE = {"Dishonours": "支出类"}
GROUP5_ALIAS = {"负债类": "贷款类"}
G5_ORDER = ["收入类", "支出类", "贷款类", "转账类", "未分类"]

DEFAULT_OUTPUT_NAME = "category_difference_report_v2_en.md"

BLANK = "(blank)"

CATEGORY_DEFINITION_EN: Dict[str, str] = {
    "Wages": "Salary and wage inflows, including payroll credits, bonuses and severance payments.",
    "Centrelink": "Government benefit inflows, including Centrelink payments and other subsidies.",
    "All Other Credits": "Other credit inflows — non-payroll, non-benefit receipts and refunds.",
    "Information": "Information and media spend — newspapers, news/info subscriptions and paid online content.",
    "Donations": "Charitable giving — donations and giving subscriptions.",
    "Education": "Education spend — tuition, course fees and study materials.",
    "Home Improvement": "Home improvement — building materials and renovation services.",
    "Insurance": "Insurance premiums of all types.",
    "Subscription TV": "TV/media subscriptions — streaming, pay TV and home-entertainment subscriptions.",
    "Pet Care": "Pet care — food, veterinary services and grooming.",
    "Entertainment": "Entertainment spend — cinema, tickets, events and games.",
    "Retail": "Retail purchases other than department stores.",
    "Utilities": "Utilities — electricity, water, gas and phone bills.",
    "Gyms and other memberships": "Gym and other memberships — fitness clubs and membership fees.",
    "Personal Care": "Personal care — hairdressing, beauty, massage and therapy.",
    "Rent": "Periodic rental payments.",
    "Health": "Health and medical — clinics, pharmacies, dentists and medical bills.",
    "Travel": "Travel spend — flights, hotels, bookings and car hire.",
    "Groceries": "Groceries — supermarkets and everyday food and household shopping.",
    "Automotive": "Vehicle-related spend — fuel, servicing, parking and non-insurance car costs.",
    "Gambling": "Gambling spend — betting, lottery and casino transactions.",
    "Department Stores": "Department-store shopping.",
    "Telecommunications": "Telecommunications — mobile plans, broadband and communication devices.",
    "Dining Out": "Dining out — restaurants, fast food and takeaway.",
    "Fees": "Fees and charges — bank fees, fines and late-payment charges.",
    "Transport": "Transport — buses, trains, taxis, tolls and parking.",
    "External Transfers": "External transfers — movements between the account population and accounts outside it.",
    "Internal Transfer": "Internal transfers — movements between accounts within the same population.",
    "SACC Loans": "SACC loan activity (in-house lending products, including repayment and drawdown).",
    "Non SACC Loans": "Non-SACC loan activity (other lending products).",
    "Credit Card Repayments": "Credit-card repayments.",
    "Debt Consolidation": "Debt consolidation — several debts repaid through a single payment.",
    "Debt Collection": "Debt collection — repayments involving collection agencies.",
    "Dishonours": "Dishonours — bounced cheques and failed direct debits.",
    "Overdrawn": "Overdraft activity, including overdraft charges.",
    "Unknown Loans": "Loan-related transactions of unrecognised source — requires investigation.",
}


GROUP_EN: Dict[str, str] = {"收入类": "Income", "支出类": "Expenses", "转账类": "Transfers", "负债类": "Liabilities"}

# diff_type strings arrive from the data layer in Chinese; map to English at presentation time.

DIFF_TYPE_EN: Dict[str, str] = {
    "双方分类不一致": "Both classified, labels differ",
    "仅 Illion 有值": "Illion only",
    "仅 finv 有值": "finv only",
}
# priority strings that may come straight from the workbook in Chinese

PRIORITY_EN = {"高": "High", "中": "Medium", "低": "Low"}

# ---------------------------------------------------------------------------
# Low-level rendering helpers (English document variants of the Chinese helpers)
# ---------------------------------------------------------------------------


# ===========================================================================
# 二、基础工具与数据读取（00 / 01 表）
# ===========================================================================

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def norm_cat(value: Any) -> str:
    """标准化类别字段：None / 空 / (空) 均视为缺失。"""
    text = str(value or "").strip()
    if text in {"", "-", "None", EMPTY, "（空）"}:
        return ""
    return text


def fmt_num(value: Any) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return ""


def fmt_pct(value: Any, digits: int = 2) -> str:
    """value 为比例小数，输出百分比字符串；为空输出 ''。"""
    if value is None or value == "":
        return ""
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return ""




def fmt_money(value: Any) -> str:
    """金额格式化；0 渲染为「—」，因为底稿里 0 表示「未给出金额」而非「金额为零」。

    01 表只给出 Top 20 流向的差异金额，其余流向的 `flow_stats[...]["amount"]` 取默认值
    0.0（见 MdAnalysis.__init__）。原样格式化成 $0.00 等于断言这些流向金额为零。
    已核验：本底稿 01 表 20 条 Top 流向金额全部为正且非零，03 表 1,200 条样本中
    0 金额与负金额均为 0 条，故「0 ⇒ 未知」成立。
    样本表不走这条路径（见 add_samples_table 用 _fmt_money_exact）。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v == 0:
        return DASH
    return f"${v:,.2f}"


def _fmt_money_exact(value: Any) -> str:
    """样本金额的逐字格式化：样本里的 0 是真实金额，照常显示。"""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return ""


def _amount_known(value: Any) -> bool:
    """该流向的金额在底稿中是否存在（0 表示未给出，不是金额为零）。"""
    return to_float(value) != 0



def inter_light(inter_share: Any) -> Optional[str]:
    """一致率（类别并集）红绿灯：<50% 红灯，>80% 绿灯，缺失/正常区间返回 None。"""
    if inter_share is None or inter_share == "":
        return None
    v = to_float(inter_share)
    if v < RED_INTER_SHARE_LT:
        return RED
    if v > GREEN_INTER_SHARE_GT:
        return GREEN
    return None


def only_light(share: Any) -> Optional[str]:
    """独有占比（illion / finv）红灯：>30% 红灯，其余返回 None。"""
    if share is None or share == "":
        return None
    return RED if to_float(share) > RED_ONLY_SHARE_GT else None


def indicator_highlights(items: List[Dict[str, Any]], col_inter: int = 5,
                         col_il: int = 6, col_fv: int = 7) -> Dict[Tuple[int, int], str]:
    """为七项指标表（一致率/两列独有占比）生成红绿灯着色映射 {(行, 列): 颜色}。"""
    highlights: Dict[Tuple[int, int], str] = {}
    for i, item in enumerate(items):
        color = inter_light(item.get("交集占比（并集）"))
        if color:
            highlights[(i, col_inter)] = color
        for j, key in ((col_il, "illion独有占比（并集）"), (col_fv, "finv独有占比（并集）")):
            color = only_light(item.get(key))
            if color:
                highlights[(i, j)] = color
    return highlights


def diff_type_label(value: Any) -> str:
    return DIFF_TYPE_MAP.get(str(value or "").strip(), str(value or "").strip())


# ---------------------------------------------------------------------------
# 数据读取
# ---------------------------------------------------------------------------
def read_generated_date(ws: Any) -> str:
    """00_核心对比 A2 单元格：'... | 生成时间: 2026-08-27'，提取底稿生成日期（无则返回空串）。"""
    import re
    for row in ws.iter_rows(min_row=2, max_row=2, min_col=1, max_col=1, values_only=True):
        if row[0]:
            m = re.search(r"生成时间[:：]\s*(\d{4}-\d{2}-\d{2})", str(row[0]))
            if m:
                return m.group(1)
    return ""


def read_metrics(ws: Any) -> Dict[str, Dict[str, Any]]:
    """00_核心对比 第 5~20 行：指标 / 结果 / 分子 / 分母 / 说明。"""
    metrics: Dict[str, Dict[str, Any]] = {}
    for row in ws.iter_rows(min_row=5, max_row=20, min_col=1, max_col=5, values_only=True):
        label = str(row[0] or "").strip()
        if label:
            metrics[label] = {"result": row[1], "numerator": row[2], "denominator": row[3], "note": row[4]}
    return metrics


def read_categories(ws: Any) -> List[Dict[str, Any]]:
    """00_核心对比 第 23~58 行：36 个类别的全量指标（33 列）。"""
    headers = [str(v or "") for v in next(ws.iter_rows(min_row=23, max_row=23, min_col=1, max_col=33, values_only=True))]
    items: List[Dict[str, Any]] = []
    for row in ws.iter_rows(min_row=24, max_row=59, min_col=1, max_col=33, values_only=True):
        category = norm_cat(row[0])
        if not category or not any(v not in (None, "") for v in row):
            continue
        item = {headers[i]: row[i] for i in range(len(headers))}
        item["category"] = category
        item["group"] = GROUP_OF.get(category, "其他")
        items.append(item)
    return items


def read_top_flows(ws: Any) -> List[Dict[str, Any]]:
    """01_差异诊断地图 第 6~25 行：Top 20 差异流向。"""
    flows: List[Dict[str, Any]] = []
    for row in ws.iter_rows(min_row=6, max_row=25, min_col=1, max_col=15, values_only=True):
        if not isinstance(row[0], (int, float)):
            continue
        flows.append({
            "rank": row[0],
            "priority": str(row[1] or "").strip(),
            "key_category": str(row[2] or "").strip(),
            "illion_category": norm_cat(row[3]),
            "finv_category": norm_cat(row[4]),
            "diff_type": diff_type_label(row[5]),
            "count": to_float(row[6]),
            "share_all": row[7],
            "amount": to_float(row[12]),
            "direction": str(row[13] or "").strip(),
            "suggestion": str(row[14] or "").strip(),
        })
    return flows


def group5(category: str) -> str:
    """把类别映射到 02 表口径的五个大类；空类别归「未分类」。"""
    if not category:
        return "未分类"
    g = GROUP5_OVERRIDE.get(category) or GROUP_OF.get(category, "")
    return GROUP5_ALIAS.get(g, g)


# ===========================================================================
# 三、读取 01 的类别计数矩阵 / 02 的大类矩阵
# ===========================================================================
def read_matrix(ws: Any) -> Dict[Tuple[str, str], float]:
    """读取 01 表的 37×37 类别计数矩阵：{(illion 类别, finv 类别): 数量}。

    空分类（底稿写作「(空)」）经 norm_cat 归一为空串；整行留白的分隔行跳过，
    避免与「(空)」行混淆。
    """
    rows = list(
        ws.iter_rows(
            min_row=MATRIX_HEADER_ROW,
            max_row=MATRIX_HEADER_ROW + MATRIX_SPAN,
            min_col=1,
            max_col=MATRIX_SPAN + 1,
            values_only=True,
        )
    )
    if not rows:
        raise ValueError(f"{SHEET_01} 第 {MATRIX_HEADER_ROW} 行未找到类别矩阵")
    col_labels = [norm_cat(v) for v in rows[0][1:]]

    matrix: Dict[Tuple[str, str], float] = {}
    for row in rows[1:]:
        raw_label = str(row[0] or "").strip()
        if not raw_label:  # 留白分隔行
            continue
        row_label = norm_cat(raw_label)
        for j, col_label in enumerate(col_labels):
            if j + 1 >= len(row):
                break
            matrix[(row_label, col_label)] = to_float(row[j + 1])
    return matrix


def read_group_matrix_02(ws: Any) -> Dict[Tuple[str, str], float]:
    """读取 02 表的 5×5 大类矩阵（行 = illion 大类，列 = finv 大类）。"""
    header: Optional[List[str]] = None
    out: Dict[Tuple[str, str], float] = {}
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=8, values_only=True):
        first = str(row[0] or "").strip()
        if not first:
            continue
        if "illion" in first and "finv" in first:  # 表头行
            header = [str(v or "").strip() for v in row[1:]]
            continue
        if header is None:
            continue
        if first not in G5_ORDER:
            continue
        for j, col_label in enumerate(header):
            if col_label not in G5_ORDER or j + 1 >= len(row):
                continue
            out[(first, col_label)] = to_float(row[j + 1])
    return out


# ===========================================================================
# 四、流式读取 03 的样本交易（仅用于案例展示）
# ===========================================================================
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# 03 表需要的列（0 基）：F=transaction_date, G=amount, J=dr_cr, K=text,
# M=counterparty, N=illion Category, O=finv Category
COL_DATE, COL_AMOUNT, COL_DRCR, COL_TEXT, COL_CP, COL_IL, COL_FV = 5, 6, 9, 10, 12, 13, 14
SAMPLE_COLS = {COL_DATE, COL_AMOUNT, COL_DRCR, COL_TEXT, COL_CP, COL_IL, COL_FV}
SAMPLE_FIRST_ROW = 4


def read_row_count(xlsx_path: Path, sheet_name: str) -> int:
    """工作表的数据行数（读 dimension 声明，不解析内容，开销可忽略）。

    用于「数据来源」中对 03 表规模的如实描述——底稿里 03 有百万行，本脚本只取
    每个类别的若干条典型样本，不能把样本条数说成底稿的明细笔数。
    """
    with zipfile.ZipFile(xlsx_path) as zf:
        with zf.open(_sheet_xml_path(zf, sheet_name)) as fh:
            head = fh.read(4096).decode("utf-8", "ignore")
    m = re.search(r'<dimension[^>]*ref="[A-Z]+\d+:[A-Z]+(\d+)"', head)
    if not m:
        return 0
    return max(0, int(m.group(1)) - (SAMPLE_FIRST_ROW - 1))


def _col_index(ref: str) -> int:
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return -1
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _sheet_xml_path(zf: zipfile.ZipFile, sheet_name: str) -> str:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rid = None
    for sh in wb.iter(NS + "sheet"):
        if sh.get("name") == sheet_name:
            rid = sh.get(REL_NS + "id")
            break
    if rid is None:
        raise KeyError(f"工作簿中未找到工作表「{sheet_name}」")
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    for rel in rels:
        if rel.get("Id") == rid:
            # Target 可能是 "worksheets/sheet4.xml"（相对）或 "/xl/worksheets/sheet4.xml"（绝对）
            target = (rel.get("Target") or "").lstrip("/")
            return target if target.startswith("xl/") else "xl/" + target
    raise KeyError(f"未找到工作表「{sheet_name}」的关系项")


def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    out: List[str] = []
    with zf.open("xl/sharedStrings.xml") as fh:
        for _, el in ET.iterparse(fh, events=("end",)):
            if el.tag == NS + "si":
                out.append("".join(t.text or "" for t in el.iter(NS + "t")))
                el.clear()
    return out


def _cell_value(cell: ET.Element, shared: Sequence[str]) -> str:
    ctype = cell.get("t")
    if ctype == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(NS + "t"))
    v = cell.find(NS + "v")
    if v is None or v.text is None:
        return ""
    if ctype == "s":
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError):
            return ""
    return v.text


def read_samples(xlsx_path: Path, per_category: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """流式读取 03，按类别保留金额绝对值最大的 per_category 条样本。

    用 iterparse 逐行解析（03 有 100 万行，openpyxl 全量载入过慢），
    每行只取需要的 7 列，处理完即释放。
    """
    buckets: Dict[str, List[Tuple[float, int, Dict[str, Any]]]] = defaultdict(list)
    counter = 0

    with zipfile.ZipFile(xlsx_path) as zf:
        shared = _shared_strings(zf)
        path = _sheet_xml_path(zf, SHEET_03)
        with zf.open(path) as fh:
            for _, el in ET.iterparse(fh, events=("end",)):
                if el.tag != NS + "row":
                    continue
                try:
                    row_no = int(el.get("r") or 0)
                except ValueError:
                    row_no = 0
                if row_no >= SAMPLE_FIRST_ROW:
                    cells: Dict[int, str] = {}
                    for cell in el.iter(NS + "c"):
                        idx = _col_index(cell.get("r") or "")
                        if idx in SAMPLE_COLS:
                            cells[idx] = _cell_value(cell, shared)
                    il = norm_cat(cells.get(COL_IL))
                    fv = norm_cat(cells.get(COL_FV))
                    if il or fv:
                        amount = to_float(cells.get(COL_AMOUNT))
                        rec = {
                            "transaction_date": str(cells.get(COL_DATE) or "").strip(),
                            "text": str(cells.get(COL_TEXT) or "").strip(),
                            "counterparty": str(cells.get(COL_CP) or "").strip(),
                            "dr_cr": str(cells.get(COL_DRCR) or "").strip(),
                            "amount": amount,
                            "illion_category": il,
                            "finv_category": fv,
                        }
                        for cat in {il, fv}:
                            if not cat:
                                continue
                            counter += 1
                            entry = (abs(amount), counter, rec)
                            bucket = buckets[cat]
                            if len(bucket) < per_category:
                                heapq.heappush(bucket, entry)
                            elif entry[0] > bucket[0][0]:
                                heapq.heapreplace(bucket, entry)
                el.clear()
    return buckets


# ===========================================================================
# 五、分析对象：以 00 / 01 为统计基础
# ===========================================================================
class MdAnalysis:
    def __init__(self, metrics, categories, top_flows, matrix, samples, generated_date: str = ""):
        self.metrics = metrics
        self.categories = categories
        self.top_flows = top_flows
        self.matrix = matrix
        self.samples = samples
        self.generated_date = generated_date

        self.total_transactions = to_float(metrics.get("总交易数", {}).get("result"))
        self.illion_coverage = metrics.get("illion Category 覆盖率", {}).get("result")
        self.finv_coverage = metrics.get("finv Category 覆盖率", {}).get("result")
        self.joint_nonempty = to_float(metrics.get("双方非空时一致率", {}).get("denominator"))
        self.joint_agreement = metrics.get("双方非空时一致率", {}).get("result")
        self.mismatch = to_float(metrics.get("双方非空时差异率", {}).get("numerator"))
        self.diff_total = to_float(metrics.get("Category 差异总数", {}).get("result"))
        self.illion_only_total = to_float(metrics.get("仅 illion 有 Category", {}).get("result"))
        self.finv_only_total = to_float(metrics.get("仅 finv 有 Category", {}).get("result"))
        self.both_empty = to_float(metrics.get("双方均为空", {}).get("result"))
        self.union_nonempty = self.joint_nonempty + self.illion_only_total + self.finv_only_total

        self.category_by_name = {item["category"]: item for item in categories}

        # Top 20 流向的金额（01 表「差异金额」列）；其余流向底稿未提供金额
        self.flow_amount: Dict[Tuple[str, str], float] = {
            (f["illion_category"], f["finv_category"]): f["amount"] for f in top_flows
        }

        # 流向统计由矩阵重建：(illion, finv, 差异类型) -> {count, amount, has_amount}
        self.flow_stats: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for (il, fv), count in matrix.items():
            if not count:
                continue
            if il and fv:
                if il == fv:  # 对角线 = 交集，不是差异
                    continue
                dt = DIFF_TYPE_BOTH
            elif il:
                dt = DIFF_TYPE_ILLION_ONLY
            else:
                dt = DIFF_TYPE_FINV_ONLY
            self.flow_stats[(il, fv, dt)] = {
                "count": count,
                "amount": self.flow_amount.get((il, fv), 0.0),
                "has_amount": (il, fv) in self.flow_amount,
            }

    # -- 板块口径（由矩阵推导，不依赖 03）--------------------------------
    def segment_summary(self, group: str) -> Dict[str, Any]:
        within = cross = illion_only = finv_only = 0
        for (il, fv), count in self.matrix.items():
            if not count or (il and fv and il == fv):
                continue
            il_g = GROUP_OF.get(il) if il else ""
            fv_g = GROUP_OF.get(fv) if fv else ""
            if not (il_g == group or fv_g == group):
                continue
            if il and fv:
                if il_g == fv_g:
                    within += count
                else:
                    cross += count
            elif il:
                illion_only += count
            else:
                finv_only += count
        total = within + cross + illion_only + finv_only
        return {
            "total": total,
            "share": total / self.diff_total if self.diff_total else 0,
            "within": within,
            "cross": cross,
            "illion_only": illion_only,
            "finv_only": finv_only,
        }

    def segment_coverage(self, group: str) -> Dict[str, Any]:
        cats = [c for c in self.categories if c["group"] == group]
        illion_n = sum(to_float(c.get("illion数量")) for c in cats)
        finv_n = sum(to_float(c.get("finv数量")) for c in cats)
        inter_n = sum(to_float(c.get("交集数量")) for c in cats)
        union_sum = sum(to_float(c.get("并集数量")) for c in cats)
        within = self.segment_summary(group)["within"]
        union_n = union_sum - within
        return {
            "category_count": len(cats),
            "illion_count": illion_n,
            "finv_count": finv_n,
            "union_count": union_n,
            "intersection_count": inter_n,
            "illion_coverage": illion_n / self.total_transactions if self.total_transactions else 0,
            "finv_coverage": finv_n / self.total_transactions if self.total_transactions else 0,
            "exact_rate": inter_n / union_n if union_n else 0,
            "broad_rate": (inter_n + within) / union_n if union_n else 0,
        }

    def category_difference_n(self, category: str) -> float:
        """该类别的差异笔数 = 并集 − 交集（底稿「双向变动数」）。"""
        item = self.category_by_name.get(category, {})
        return to_float(item.get("双向变动数"))

    # -- 类别 / 流向口径 -------------------------------------------------
    def category_flows(self, category: str, limit: int = 5) -> List[Tuple[str, str, str, int, float]]:
        flows = [
            (il, fv, dt, int(stat["count"]), stat["amount"])
            for (il, fv, dt), stat in self.flow_stats.items()
            if category in (il, fv)
        ]
        flows.sort(key=lambda x: -x[3])
        return flows[:limit]

    def segment_top_flows(self, group: str, limit: int = 5) -> List[Tuple[str, str, str, int, float]]:
        flows = [
            (il, fv, dt, int(stat["count"]), stat["amount"])
            for (il, fv, dt), stat in self.flow_stats.items()
            if GROUP_OF.get(il) == group or GROUP_OF.get(fv) == group
        ]
        flows.sort(key=lambda x: -x[3])
        return flows[:limit]

    def flow_between(self, cat_a: str, cat_b: str) -> List[Tuple[str, str, str, int, float]]:
        flows = [
            (il, fv, dt, int(stat["count"]), stat["amount"])
            for (il, fv, dt), stat in self.flow_stats.items()
            if {il, fv} == {cat_a, cat_b}
        ]
        flows.sort(key=lambda x: -x[3])
        return flows

    # -- 样本（仅来自 03）------------------------------------------------
    def top_samples(self, category: str, limit: int = 5) -> List[Dict[str, Any]]:
        rows = sorted(self.samples.get(category, []), key=lambda t: t[0], reverse=True)
        return [t[2] for t in rows[:limit]]

    def top_samples_in_flow(self, category: str, flow: Tuple[str, str], limit: int = 1):
        rows = [
            t for t in self.samples.get(category, [])
            if (t[2]["illion_category"], t[2]["finv_category"]) == flow
        ]
        rows.sort(key=lambda t: t[0], reverse=True)
        return [t[2] for t in rows[:limit]]

    def sample_categories(self) -> List[str]:
        return sorted(c for c, v in self.samples.items() if v)

    # -- 独立校验 --------------------------------------------------------
    def diff_type_breakdown(self) -> Dict[str, int]:
        out = {DIFF_TYPE_BOTH: 0, DIFF_TYPE_ILLION_ONLY: 0, DIFF_TYPE_FINV_ONLY: 0}
        for (il, fv), count in self.matrix.items():
            if not count or (il and fv and il == fv):
                continue
            if il and fv:
                out[DIFF_TYPE_BOTH] += count
            elif il:
                out[DIFF_TYPE_ILLION_ONLY] += count
            else:
                out[DIFF_TYPE_FINV_ONLY] += count
        return out

    def group5_matrix(self) -> Dict[Tuple[str, str], float]:
        """把 37×37 类别矩阵聚合成 02 表口径的 5×5 大类矩阵。

        01 的类别矩阵只覆盖「至少一侧有分类」的交易（矩阵描述的是分类流向，
        双方均为空不构成流向），因此需补回 00 表的「双方均为空」格。
        """
        out: Dict[Tuple[str, str], float] = defaultdict(float)
        for (il, fv), count in self.matrix.items():
            out[(group5(il), group5(fv))] += count
        out[("未分类", "未分类")] += self.both_empty
        return out

    def reconcile(self) -> List[str]:
        """与底稿 00 表逐项交叉校验，返回问题清单（空 = 全部通过）。"""
        problems: List[str] = []

        # 1) 矩阵行/列合计 == 00 表的 illion / finv 数量
        row_sum: Dict[str, float] = defaultdict(float)
        col_sum: Dict[str, float] = defaultdict(float)
        for (il, fv), count in self.matrix.items():
            row_sum[il] += count
            col_sum[fv] += count
        for item in self.categories:
            cat = item["category"]
            if abs(row_sum.get(cat, 0) - to_float(item.get("illion数量"))) > 0.5:
                problems.append(f"矩阵行合计 ≠ illion数量：{cat}")
            if abs(col_sum.get(cat, 0) - to_float(item.get("finv数量"))) > 0.5:
                problems.append(f"矩阵列合计 ≠ finv数量：{cat}")

        # 2) 矩阵对角 == 00 表的交集数量
        for item in self.categories:
            cat = item["category"]
            if abs(self.matrix.get((cat, cat), 0) - to_float(item.get("交集数量"))) > 0.5:
                problems.append(f"矩阵对角 ≠ 交集数量：{cat}")

        # 3) 差异类型构成合计与分项 == 00 总体的对应值
        breakdown = self.diff_type_breakdown()
        if abs(sum(breakdown.values()) - self.diff_total) > 0.5:
            problems.append(
                f"差异类型构成合计 {sum(breakdown.values()):,.0f} ≠ 差异总数 {self.diff_total:,.0f}"
            )
        for label, expect in (
            (DIFF_TYPE_BOTH, self.mismatch),
            (DIFF_TYPE_ILLION_ONLY, self.illion_only_total),
            (DIFF_TYPE_FINV_ONLY, self.finv_only_total),
        ):
            if abs(breakdown[label] - expect) > 0.5:
                problems.append(f"{label} {breakdown[label]:,.0f} ≠ 00 表 {expect:,.0f}")

        for item in self.categories:
            cat = item["category"]
            # 4) 并集 == 交集 + 两侧独有
            expect_union = (
                to_float(item.get("交集数量"))
                + to_float(item.get("illion独有数量"))
                + to_float(item.get("finv独有数量"))
            )
            if abs(to_float(item.get("并集数量")) - expect_union) > 0.5:
                problems.append(f"并集 ≠ 交集 + 两侧独有：{cat}")

            # 5) 双向变动数 == 并集 − 交集 == 矩阵中涉及该类别且非对角的总量
            if abs(to_float(item.get("双向变动数"))
                   - (to_float(item.get("并集数量")) - to_float(item.get("交集数量")))) > 0.5:
                problems.append(f"双向变动数 ≠ 并集 − 交集：{cat}")
            flow_sum = sum(
                stat["count"] for (il, fv, _), stat in self.flow_stats.items() if cat in (il, fv)
            )
            if abs(flow_sum - to_float(item.get("双向变动数"))) > 0.5:
                problems.append(f"矩阵流向合计 ≠ 双向变动数：{cat}")

            # 6) 单边缺失 == 流向其他类别 + 对侧缺失
            if abs(
                to_float(item.get("流向其他Category")) + to_float(item.get("finv缺失"))
                - to_float(item.get("illion独有数量"))
            ) > 0.5:
                problems.append(f"illion独有 ≠ 流向其他 + finv缺失：{cat}")

        # 7) 差异贡献率合计为 1.0，且按底稿行序的累计 == 底稿「累计差异贡献率」列
        contrib_sum = sum(to_float(c.get("差异贡献率")) for c in self.categories)
        if abs(contrib_sum - 1.0) > 1e-6:
            problems.append(f"差异贡献率合计 {contrib_sum:.6f} ≠ 1.0")
        cum = 0.0
        for item in self.categories:
            cum += to_float(item.get("差异贡献率"))
            if abs(cum - to_float(item.get("累计差异贡献率"))) > 1e-6 and item.get("累计差异贡献率") is not None:
                problems.append(f"累计差异贡献率 ≠ 逐行累加：{item['category']}")

        return problems

    def reconcile_02(self, xlsx_path: Path) -> List[str]:
        """与 02 表的大类矩阵逐格交叉校验（该表未参与本报告的任何计算）。"""
        problems: List[str] = []
        wb = load_workbook(xlsx_path, read_only=True, data_only=True)
        try:
            expect = read_group_matrix_02(wb[SHEET_02])
        finally:
            wb.close()
        if not expect:
            return ["未能从 02_业务聚类对比 读取到大类矩阵"]
        got = self.group5_matrix()
        for (r, c), want in sorted(expect.items()):
            have = got.get((r, c), 0.0)
            if abs(have - want) > 0.5:
                problems.append(f"02 表 {r}→{c}：底稿 {want:,.0f} vs 本报告 {have:,.0f}")
        return problems


# ===========================================================================
# 六、Markdown 渲染工具（转义 / 表格 / 锚点）
# ===========================================================================
def esc(text: Any) -> str:
    """转义 Markdown 表格中的竖线与换行。"""
    s = "" if text is None else str(text)
    return s.replace("|", "\\|").replace("\n", "<br>").strip()


def md_table(headers: Sequence[Any], rows: Iterable[Sequence[Any]], aligns: Optional[Sequence[str]] = None) -> str:
    heads = [esc(h) for h in headers]
    sep = [
        {"l": ":---", "r": "---:", "c": ":---:"}[(aligns[i] if aligns and i < len(aligns) else "l")]
        for i in range(len(heads))
    ]
    lines = ["| " + " | ".join(heads) + " |", "| " + " | ".join(sep) + " |"]
    for row in rows:
        cells = [esc(v) for v in row]
        cells += [""] * (len(heads) - len(cells))
        lines.append("| " + " | ".join(cells[: len(heads)]) + " |")
    return "\n".join(lines)


def anchor(text: str) -> str:
    """近似 GitHub 的标题锚点规则。"""
    s = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", text).strip().lower()
    return re.sub(r"\s+", "-", s)


TOC_TOKEN = "@@TOC@@"


class _CountedList(list):
    """元素是真实样本，len() 却是底稿口径的真实计数。

    docx 正文逻辑用 len(analysis.category_details[cat]) 当分母；在完整的 03 表上它
    等于「该类别涉及的差异行数」。03 被截断后我们只有样本，故用 00 表的「双向变动数」
    （= 并集 − 交集 = 涉及该类别的差异笔数，见 MdAnalysis.reconcile 第 5 项校验）作为
    长度，而元素仍是样本，供 dr_cr_distribution 等按行取值的用法使用。
    """

    def __init__(self, items: Iterable[Dict[str, Any]], count: float) -> None:
        super().__init__(items)
        self._count = int(count or 0)

    def __len__(self) -> int:  # type: ignore[override]
        return self._count


class MdAnalysisFull(MdAnalysis):
    """在矩阵数据层之上补齐 docx 正文逻辑所需的接口（details / category_details）。"""

    def __init__(self, *args: Any, detail_rows: int = 0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        # 03 样本拍平成明细列表：docx 正文逻辑按 details 迭代取典型样本
        # （Transfer 的方向分布、Gambling 的 Score55/ATM 补充样本、Information 的
        #  明细构成核验）。这些取的都是「样本」，03 只作样本来源，不当统计基础。
        # len(details) 则用于「数据来源」里描述 03 表规模，故取底稿真实行数而非样本条数。
        flat = [entry[2] for cat in sorted(self.samples) for entry in self.samples[cat]]
        self.details: _CountedList = _CountedList(flat, detail_rows or len(flat))

        # 类别 → 该类别涉及的样本；len() 为底稿口径的差异笔数（双向变动数）
        self.category_details: Dict[str, _CountedList] = {
            item["category"]: _CountedList(
                [entry[2] for entry in self.samples.get(item["category"], [])],
                self.category_difference_n(item["category"]),
            )
            for item in self.categories
        }

    def dr_cr_distribution(self, category: str) -> Tuple[int, int]:
        """类别样本的 credit / debit 分布（同名于 docx 的 Analysis 方法，此处基于 03 样本）。"""
        credit = debit = 0
        for detail in self.category_details.get(category, []):
            if detail.get("dr_cr") == "credit":
                credit += 1
            elif detail.get("dr_cr") == "debit":
                debit += 1
        return credit, debit


# ---------------------------------------------------------------------------
# 承接图表插入的 docx API 占位对象
# ---------------------------------------------------------------------------
class _NullFormat:
    """吞掉 paragraph_format.space_before / space_after 之类的排版赋值。"""

    def __getattr__(self, name: str) -> Any:
        return None

    def __setattr__(self, name: str, value: Any) -> None:
        pass


class _MdPictureRun:
    def __init__(self, doc: "MdDoc") -> None:
        self.doc = doc

    def add_picture(self, path: str, width: Any = None) -> None:
        p = Path(path)
        if p.exists():
            self.doc.parts.append(f"![{p.stem}]({self.doc.rel_path(p)})")

    @property
    def font(self) -> _NullFormat:
        return _NullFormat()


class _MdPictureParagraph:
    """接住 build_report 里 `doc.add_paragraph().add_run().add_picture(...)` 这条链。"""

    def __init__(self, doc: "MdDoc") -> None:
        self.doc = doc
        self.alignment = None
        self.paragraph_format = _NullFormat()

    def add_run(self) -> _MdPictureRun:
        return _MdPictureRun(self.doc)


# ---------------------------------------------------------------------------
# 七、Markdown 文档对象（承接 docx API 与正文逻辑）
# ---------------------------------------------------------------------------
class MdDoc:
    """docx 渲染助手的 Markdown 落点。"""

    # 目录标题：中文版是「目录」，英文版要 Contents
    toc_title = "Contents"

    def __init__(self, out_path: Path) -> None:
        self.out_path = out_path
        self.parts: List[str] = []
        self.headings: List[Tuple[int, str]] = []
        self.title: Optional[str] = None
        self._toc_placed = False
        # 「交易方向与金额」段落当前的金额覆盖范围提示，由 render_category_block 设置
        self.amount_note: str = ""
        # 「数据来源」段后追加的口径说明（03 只作样本来源，不作统计基础）
        self.source_note: str = ""

    # -- 路径 ------------------------------------------------------------
    def rel_path(self, target: Path) -> str:
        try:
            return target.resolve().relative_to(self.out_path.parent.resolve()).as_posix()
        except ValueError:
            return target.resolve().as_posix()

    # -- build_report 直接调用的 docx API（仅图表插入）--------------------
    def add_paragraph(self) -> _MdPictureParagraph:
        return _MdPictureParagraph(self)

    # -- Markdown 原语 ---------------------------------------------------
    def blank(self) -> None:
        if self.parts and self.parts[-1] != "":
            self.parts.append("")

    def heading(self, text: str, level: int) -> None:
        if not text:
            return
        self.blank()
        level = max(1, min(6, int(level)))
        self.parts.append("#" * level + " " + text)
        self.parts.append("")
        self.headings.append((level, text))

    def title_line(self, text: str) -> None:
        """封面主标题：docx 里是居中的大字号段落，这里取作 md 的一级标题。"""
        if self.title is None:
            self.title = text
            self.blank()
            self.parts.append("# " + text)
            self.parts.append("")
        else:
            self.para(text, bold=True)

    def para(self, text: str, bold: bool = False) -> None:
        if not text:
            return
        self.blank()
        self.parts.append(f"**{text}**" if bold else text)
        self.parts.append("")

    def table(self, headers: Sequence[Any], rows: Sequence[Sequence[Any]],
              aligns: Optional[Sequence[str]] = None) -> None:
        rows = list(rows)
        if not rows:
            return
        self.blank()
        self.parts.append(md_table(headers, rows, aligns))
        self.parts.append("")

    def callout(self, text: str) -> None:
        if not text:
            return
        self.blank()
        for line in str(text).splitlines() or [""]:
            self.parts.append(f"> {line}".rstrip())
        self.parts.append("")

    def page_break(self) -> None:
        # 首个分页符之后即正文，目录插在它前面（此时标题尚未收集完，先放占位符）
        if not self._toc_placed:
            self._toc_placed = True
            self.blank()
            self.parts.append(TOC_TOKEN)
            self.parts.append("")
        self.blank()
        self.parts.append("---")
        self.parts.append("")

    # -- 输出 ------------------------------------------------------------
    def _toc(self) -> str:
        if not self.headings:
            return ""
        lines = [f"## {self.toc_title}", ""]
        for level, text in self.headings:
            if level > 2:
                continue
            lines.append(f"{'  ' * (level - 1)}- [{text}](#{anchor(text)})")
        return "\n".join(lines) + "\n"

    def render(self) -> str:
        body = "\n".join(self.parts)
        if TOC_TOKEN in body:
            body = body.replace(TOC_TOKEN, self._toc().rstrip("\n"))
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if self.title and body.startswith("# " + self.title):
            return body + "\n"
        return f"# {self.title or '类别差异分析报告'}\n\n{body}\n"


# ---------------------------------------------------------------------------
# 渲染助手：与 docx 同名同签名，输出改成 Markdown
# ---------------------------------------------------------------------------


# ===========================================================================
# 八、渲染层：函数名与签名对齐英文 docx 版，输出改为 Markdown
#
# 「九、正文文案」里的裸名调用（add_heading / add_para / add_table / fmt_money /
# render_category_block_en …）直接解析到这里，不需要任何打补丁或运行时替换。
# ===========================================================================
def _light_mark(text: Any, color: Optional[str]) -> str:
    """docx 用红/绿字标红绿灯，Markdown 用圆点 + 加粗保留同样的强调。"""
    s = "" if text is None else str(text)
    if not color:
        return s
    if color == RED:
        return f"🔴 {s}"
    if color == GREEN:
        return f"🟢 {s}"
    return f"**{s}**"


def add_heading(doc: MdDoc, text: str, level: int = 1) -> None:
    doc.heading(text, level)


def add_page_break(doc: MdDoc) -> None:
    doc.page_break()


def add_para(doc: MdDoc, text: str, size: float = 10, bold: bool = False,
             color: str = "", align: str = "left", space_after: float = 6) -> None:
    if not text:
        return
    # 封面主标题（居中且字号 ≥11）→ 一级标题；「Data sources」等 12pt 加粗行 → 二级标题
    if align == "center" and size >= 11:
        doc.title_line(text)
        return
    if bold and size >= 12:
        doc.heading(text, 2)
        return
    doc.para(text, bold=bold)
    # 「Excel workbook:」之后立刻交代 03 的用途，避免把样本条数当成统计口径
    if doc.source_note and text.startswith("Excel workbook:"):
        doc.para(doc.source_note)


def add_para_labeled(doc: MdDoc, label: str, text: str, size: float = 9.5,
                     space_after: float = 3) -> None:
    # 「Direction & value」的合计只可能覆盖底稿给出金额的流向，补一句限定说明
    note = doc.amount_note
    if note and label == "Direction & value" and text:
        text = text[:-1] + note + "." if text.endswith(".") else text + note
    doc.para(f"**{label}:** {text}" if text else f"**{label}:**")


def add_table(doc: MdDoc, headers: Sequence[str], rows: Sequence[Sequence[Any]],
              col_widths: Any = None, font_size: float = 8.5, header_color: str = "",
              align_center_cols: Any = None, zebra: bool = True,
              highlights: Any = None, fills: Any = None) -> None:
    rows = list(rows)
    if not rows:
        return
    centers = set(align_center_cols or ())
    aligns = ["c" if i in centers else "l" for i in range(len(headers))]
    hl = highlights or {}
    body: List[List[str]] = []
    for i, row in enumerate(rows):
        cells: List[str] = []
        for j in range(len(headers)):
            value = row[j] if j < len(row) else ""
            cells.append(_light_mark(value, hl.get((i, j))))
        body.append(cells)
    doc.table(headers, body, aligns)


def add_callout(doc: MdDoc, text: str, fill: str = "", edge: str = "",
                size: float = 9.5) -> None:
    doc.callout(text)


def en_run_only(run: Any) -> None:
    """docx 里用于统一字体；Markdown 无字体概念，忽略。"""


# --- python-docx 的取值占位 -------------------------------------------------
# 正文的图表插入段里有 Pt(2) / Cm(15.5) / WD_ALIGN_PARAGRAPH.CENTER 三处
# python-docx 取值。Markdown 没有字号、列宽、对齐的概念，这些值最终都被 MdDoc
# 的图片占位对象丢掉，故这里只需名字存在。
def Pt(value: Any) -> float:  # 字号
    return float(value)


def Cm(value: Any) -> float:  # 列宽
    return float(value)


class _WdAlignEnum:
    LEFT = CENTER = RIGHT = JUSTIFY = None


WD_ALIGN_PARAGRAPH = _WdAlignEnum()


def _amount_note_en(analysis: Any, category: str) -> str:
    """「Direction & value」合计的覆盖范围提示：底稿只给出 Top 20 流向的金额。"""
    pairs = [(il, fv) for (il, fv, _dt) in analysis.flow_stats if category in (il, fv)]
    missing = sum(1 for p in pairs if not analysis.flow_amount.get(p))
    if not missing:
        return ""
    return (f"; the workbook gives an amount for only {len(pairs) - missing} of these "
            f"flows, so the other {missing} are not covered")


def add_flow_table_en(doc: MdDoc, flows: Sequence[Tuple[str, str, str, int, float]],
                      title: str = "", denom: float = 0,
                      share_label: str = "Share of cat. diffs") -> None:
    if title:
        doc.para(title, bold=True)
    # 底稿未给出金额的流向由 fmt_money 渲染成「—」：0 是「未知」，不是「金额为零」
    rows = [
        [index, il or BLANK, fv or BLANK, en_diff_type(diff_type),
         fmt_pct(count / denom if denom else 0), fmt_money(amount)]
        for index, (il, fv, diff_type, count, amount) in enumerate(flows, 1)
    ]
    doc.table(["Rank", "Illion category", "finv category", "Difference type", share_label,
               "Diff amount"], rows, ["c", "l", "l", "l", "c", "r"])


def add_samples_table_en(doc: MdDoc, samples: Sequence[Dict[str, Any]]) -> None:
    rows = []
    for s in samples:
        counterparty = (s.get("counterparty", "") or "").strip()
        rows.append([
            s.get("transaction_date", ""),
            s.get("text", "") or "",
            BLANK if counterparty in ("", "-") else counterparty,
            s.get("dr_cr", ""),
            _fmt_money_exact(s.get("amount")),
            en_flow_cell(s["illion_category"], s["finv_category"]),
        ])
    if rows:
        doc.table(["Date", "Transaction description", "Counterparty", "Dir.", "Amount",
                   "Illion → finv"], rows, ["c", "l", "l", "c", "r", "c"])


def add_boundary_bullets_en(doc: MdDoc, pairs: Sequence[Tuple[str, str]],
                            analysis: Any) -> None:
    for a, b in pairs:
        flows = analysis.flow_between(a, b)
        if not flows:
            continue
        if len(flows) == 1:
            il, fv, _dt, _cnt, amount = flows[0]
            doc.para(f"{a} vs {b}: the {en_flow_cell(il, fv)} flow carries "
                     f"{fmt_money(amount)} of differences.")
            continue
        # 双向混淆：分别交代每个方向的金额，缺金额的方向单独说明而不是填 0
        known = [f for f in flows if _amount_known(f[4])]
        if not known:
            doc.para(f"{a} vs {b}: confusion runs both ways; the workbook gives no "
                     f"amount for either direction.")
        elif len(known) == len(flows):
            amounts = " and ".join(fmt_money(f[4]) for f in sorted(flows, key=lambda x: -x[4]))
            doc.para(f"{a} vs {b}: confusion runs both ways, with {amounts} of differences.")
        else:
            amounts = " and ".join(fmt_money(f[4]) for f in sorted(known, key=lambda x: -x[4]))
            doc.para(f"{a} vs {b}: confusion runs both ways; {len(known)} of the "
                     f"{len(flows)} directions carry {amounts}, and the workbook gives no "
                     f"amount for the other {len(flows) - len(known)}.")


# ===========================================================================
# 九、正文文案（与英文 docx 版同源，逐字保留）
# ===========================================================================

def en_flow_cell(illion: str, finv: str) -> str:
    """'X → Y' with a blank side rendered as (blank); side names are English category names."""
    return f"{illion or BLANK} → {finv or BLANK}"


def en_priority(value: Any) -> str:
    text = str(value or "").strip() or "-"
    return PRIORITY_EN.get(text, text)


def en_diff_type(value: str) -> str:
    return DIFF_TYPE_EN.get(value, value)


# ---------------------------------------------------------------------------
# Narrative text (English, dynamically computed)
# ---------------------------------------------------------------------------


def _transfer_pair_stats(analysis: Analysis) -> Dict[str, Any]:
    """ET→IT 判定分歧流与 ET 单边统计（全部动态来自差异流向/明细，无硬编码）。

    返回: count/amount = External Transfers → Internal Transfer 流向（笔数/金额），
    credit/debit = 该流向明细的方向分布，et_only = ET 单边（仅 Illion 有值）笔数，
    et_n/it_n = 两类别相关差异明细数。
    """
    et, it = "External Transfers", "Internal Transfer"
    pair_count = pair_amount = 0.0
    credit = debit = 0
    for (il, fv, _), stat in analysis.flow_stats.items():
        if il == et and fv == it:
            pair_count += stat["count"]
            pair_amount += stat["amount"]
    for detail in analysis.details:
        if detail["illion_category"] == et and detail["finv_category"] == it:
            if detail["dr_cr"] == "credit":
                credit += 1
            elif detail["dr_cr"] == "debit":
                debit += 1
    et_only = sum(stat["count"] for (il, fv, _), stat in analysis.flow_stats.items()
                  if il == et and fv == "")
    return {"et": et, "it": it, "count": pair_count, "amount": pair_amount,
            "credit": credit, "debit": debit, "et_only": et_only,
            "et_n": len(analysis.category_details.get(et, [])),
            "it_n": len(analysis.category_details.get(it, []))}


def indicator_highlights_en(items: List[Dict[str, Any]], col_inter: int = 3,
                            col_il: int = 4, col_fv: int = 5) -> Dict[Tuple[int, int], str]:
    """Traffic-light map for agreement/only-share columns in a category metrics table."""
    highlights: Dict[Tuple[int, int], str] = {}
    for i, item in enumerate(items):
        color = inter_light(item.get("交集占比（并集）"))
        if color:
            highlights[(i, col_inter)] = color
        for j, key in ((col_il, "illion独有占比（并集）"), (col_fv, "finv独有占比（并集）")):
            color = only_light(item.get(key))
            if color:
                highlights[(i, j)] = color
    return highlights


def category_narrative_en(item: Dict[str, Any], total_transactions: float = 0) -> str:
    """Uniform read-out of the seven category metrics (Section 3 deep dives)."""
    category = item["category"]
    union = to_float(item.get("并集数量"))
    inter = to_float(item.get("交集数量"))
    union_share = union / total_transactions if total_transactions else 0
    inter_share_all = inter / total_transactions if total_transactions else 0
    return (
        f"Illion covers {fmt_pct(item.get('illion覆盖率'))} of all transactions and finv "
        f"{fmt_pct(item.get('finv覆盖率'))}; the union of the two sides spans {fmt_pct(union_share)} "
        f"of all transactions and the intersection {fmt_pct(inter_share_all)}, giving an agreement rate "
        f"of {fmt_pct(item.get('交集占比（并集）'))} on the union basis. Illion-only and finv-only shares "
        f"of the union are {fmt_pct(item.get('illion独有占比（并集）'))} and "
        f"{fmt_pct(item.get('finv独有占比（并集）'))} respectively."
    )


def category_headline_en(item: Dict[str, Any], analysis: Analysis) -> str:
    """Lead conclusion for one category: agreement rate, core tension, dominant flow, a supporting sample."""
    category = item["category"]
    # Transfer categories use the dedicated internal-vs-external wording; fall back to the generic
    # logic whenever the ET→IT disputed flow is absent.
    if category in ("External Transfers", "Internal Transfer") \
            and _transfer_pair_stats(analysis)["count"] > 0:
        return transfer_category_headline_en(analysis, item)
    inter_share = to_float(item.get("交集占比（并集）"))
    illion_only_share = to_float(item.get("illion独有占比（并集）"))
    finv_only_share = to_float(item.get("finv独有占比（并集）"))
    if inter_share < 0.5:
        rate_txt = f"agreement is only {fmt_pct(inter_share)}"
    elif inter_share >= 0.9:
        rate_txt = f"agreement is a strong {fmt_pct(inter_share)}"
    else:
        rate_txt = f"agreement is {fmt_pct(inter_share)}"
    flows = analysis.category_flows(category, limit=1)
    flow_txt = ""
    if flows:
        il, fv, dt, cnt, amount = flows[0]
        flow_txt = (f"Differences concentrate in the {en_flow_cell(il, fv)} flow "
                    f"({fmt_pct(cnt / analysis.diff_total if analysis.diff_total else 0)} of all differences, "
                    f"{fmt_money(amount)} in value)")
    samples = analysis.top_samples_in_flow(category, (flows[0][0], flows[0][1]), limit=1) if flows else []
    sample_txt = ""
    if samples:
        s = samples[0]
        sample_txt = (f"A typical case, \"{s.get('text') or ''}\" ({fmt_money(s.get('amount'))}), is classified "
                      f"{en_flow_cell(s['illion_category'], s['finv_category'])} and is consistent with that view")
    if inter_share >= 0.9:
        parts = [f"{category}: {rate_txt}."]
        if flow_txt:
            parts.append(f"{flow_txt}.")
        if sample_txt:
            parts.append(f"{sample_txt}.")
        return " ".join(parts)
    if finv_only_share > illion_only_share + 0.05:
        core = f"the core tension is finv classifying \"{category}\" more broadly"
    elif illion_only_share > finv_only_share + 0.05:
        core = f"the core tension is Illion classifying \"{category}\" more broadly"
    else:
        core = f"the core tension is a boundary misalignment between the two sides on \"{category}\""
    parts = [f"{category}: {rate_txt}, and {core}."]
    if flow_txt:
        parts.append(f"{flow_txt}.")
    if sample_txt:
        parts.append(f"{sample_txt}.")
    return " ".join(parts)


def segment_headline_en(analysis: Analysis, group: str, items: List[Dict[str, Any]]) -> str:
    """Segment-level lead conclusion: top difference categories, dominant side, external boundary."""
    # Transfers use the dedicated internal-vs-external wording (ET→IT flow present → branch)
    if group == "转账类" and _transfer_pair_stats(analysis)["count"] > 0:
        return transfer_segment_headline_en(analysis, items)
    cat_diff = sorted(
        ((c["category"], len(analysis.category_details.get(c["category"], []))) for c in items),
        key=lambda x: -x[1])
    main_cats = [name for name, n in cat_diff if n > 0]
    if not main_cats:
        return f"No difference rows reference the {GROUP_EN[group]} segment; the two sides agree, so risk is low."
    top_names = main_cats[0] if len(main_cats) == 1 else f"{main_cats[0]} and {main_cats[1]}"
    ws = analysis.segment_summary(group)
    if ws["finv_only"] > ws["illion_only"]:
        side_txt = f"the segment's differences are driven mainly by finv's broader {GROUP_EN[group]} recognition"
    elif ws["illion_only"] > ws["finv_only"]:
        side_txt = f"the segment's differences are driven mainly by Illion's broader {GROUP_EN[group]} recognition"
    else:
        side_txt = "the two sides contribute similar volumes of one-sided recognition"
    out_flows = []
    for (il, fv, dt), stat in analysis.flow_stats.items():
        il_g = GROUP_OF.get(il) if il else ""
        fv_g = GROUP_OF.get(fv) if fv else ""
        if (il_g == group) != (fv_g == group):
            other = fv if il_g == group else il
            if other and stat["count"] > 0:
                out_flows.append((other, stat["count"]))
    out_flows.sort(key=lambda x: -x[1])
    boundary_txt = ""
    if out_flows:
        other, n = out_flows[0]
        if group == "收入类" and GROUP_OF.get(other, "") == "转账类":
            boundary_txt = (f"; the {other} boundary is the main cross-segment leak, with the two sides "
                            "disagreeing on how some transfer inflows should be classified")
        else:
            boundary_txt = f"; the boundary with {other} is the main cross-segment leak"
    best = max(items, key=lambda c: to_float(c.get("交集占比（并集）")), default=None)
    strong_txt = ""
    if best is not None and to_float(best.get("交集占比（并集）")) >= 0.9:
        strong_txt = (f" By contrast, {best['category']} shows strong consensus "
                      f"({fmt_pct(best.get('交集占比（并集）'))} agreement) and low risk.")
    return (f"{GROUP_EN[group]} differences concentrate in {top_names}; {side_txt}{boundary_txt}."
            f"{strong_txt}")


def side_only_leader_en(analysis: Analysis, group: str, side: str) -> str:
    """Category with the most one-sided recognition in a segment (side classified, the other blank)."""
    best, best_n = "", 0
    for (il, fv, dt), stat in analysis.flow_stats.items():
        il_g = GROUP_OF.get(il) if il else ""
        fv_g = GROUP_OF.get(fv) if fv else ""
        if side == "finv" and il == "" and fv_g == group and stat["count"] > best_n:
            best, best_n = fv, stat["count"]
        elif side == "illion" and fv == "" and il_g == group and stat["count"] > best_n:
            best, best_n = il, stat["count"]
    return best



# -- Special deep-dive summaries (polished English; every figure computed from the workbook) ----
# ---------------------------------------------------------------------------
# Transfers narrative (External Transfers / Internal Transfer — internal-vs-external definition split)
# ---------------------------------------------------------------------------
def transfer_category_headline_en(analysis: Analysis, item: Dict[str, Any]) -> str:
    """Lead conclusion for External Transfers / Internal Transfer (replaces the generic "broader
    recognition" wording).

    The same batch of movements is judged in opposite ways: Illion reads them as external
    transfers, while finv links them across a customer's own cards/accounts and reads them as
    transfers internal to the same bank — a definitional split, not a plain coverage gap.
    All figures come from the working paper; the supporting sample is the top-value row of the
    ET→IT flow.
    """
    category = item["category"]
    st = _transfer_pair_stats(analysis)
    et, it = st["et"], st["it"]
    diff_total = analysis.diff_total or 0
    rate = to_float(item.get("交集占比（并集）"))
    il_cov, fv_cov = item.get("illion覆盖率"), item.get("finv覆盖率")
    share_cat = (st["count"] / st["it_n"] if category == it and st["it_n"]
                 else (st["count"] / st["et_n"] if st["et_n"] else 0))
    share_all = st["count"] / diff_total if diff_total else 0
    if rate < 0.7:
        rate_txt = f"agreement is only {fmt_pct(rate)}"
    elif rate >= 0.8:
        rate_txt = f"agreement is {fmt_pct(rate)}, a strong consensus"
    else:
        rate_txt = f"agreement is {fmt_pct(rate)}"
    if category == et:
        head = (f"{et}: {rate_txt}, and the core tension is a definitional split over "
                f"internal-vs-external transfers rather than a plain coverage gap: "
                f"{fmt_num(st['count'])} movements ({fmt_money(st['amount'])}, "
                f"{fmt_pct(share_cat)} of {et}-related differences and {fmt_pct(share_all)} of all "
                f"differences) are classified as external transfers on the Illion side, while finv "
                f"links the same batch through transaction associations across a customer's own "
                f"cards/accounts and reads them as transfers internal to the same bank")
        if st["et_only"]:
            head += f"; Illion-only rows (no finv category) add {fmt_num(st['et_only'])}"
        head += (f", so Illion's coverage looks broader ({fmt_pct(il_cov)} vs {fmt_pct(fv_cov)}), "
                 f"but that breadth is mostly finv's reassignment to internal transfers plus "
                 f"Illion-only recognition rather than genuine over-coverage")
    else:
        il_only = to_float(item.get("illion独有占比（并集）"))
        fv_only = to_float(item.get("finv独有占比（并集）"))
        head = (f"{it}: {rate_txt}, but the core tension is how each side recognises internal "
                f"transfers: finv links a customer's own cards/accounts and recognises in-bank "
                f"movements as internal transfers ({et} → {it}: {fmt_num(st['count'])} movements "
                f"worth {fmt_money(st['amount'])}, {fmt_pct(share_cat)} of {it}-related differences "
                f"and {fmt_pct(share_all)} of all differences), whereas Illion reads the same batch "
                f"as external transfers. finv's one-sided share ({fmt_pct(fv_only)}) exceeds "
                f"Illion's ({fmt_pct(il_only)}), so finv's coverage is somewhat broader "
                f"({fmt_pct(fv_cov)} vs {fmt_pct(il_cov)}) — an extension of finv's card-linkage "
                f"recognition, not Illion under-recognition")
    head += "."
    samples = analysis.top_samples_in_flow(et, (et, it), limit=1)
    if samples:
        s = samples[0]
        head += (f" A typical case, \"{s.get('text') or ''}\" ({fmt_money(s.get('amount'))}), is "
                 f"classified {et} → {it} and is consistent with that view.")
    return head


def transfer_segment_headline_en(analysis: Analysis, items: List[Dict[str, Any]]) -> str:
    """Transfers-segment lead conclusion (replaces the generic "driven mainly by X's broader
    recognition" wording).

    Segment driver = the internal-vs-external definition split (ET→IT: Illion says external,
    finv's card/account linkage says internal); secondary = Illion-only ET rows and the income
    boundaries. Figures fully dynamic.
    """
    by_name = {item["category"]: item for item in items}
    et_item, it_item = by_name.get("External Transfers"), by_name.get("Internal Transfer")
    st = _transfer_pair_stats(analysis)
    et, it = st["et"], st["it"]
    diff_total = analysis.diff_total or 0
    parts = [
        f"Transfers differences concentrate in {et} ({fmt_num(st['et_n'])} related rows, "
        f"{fmt_pct(st['et_n'] / diff_total if diff_total else 0)} of all differences) and "
        f"{it} ({fmt_num(st['it_n'])} rows, "
        f"{fmt_pct(st['it_n'] / diff_total if diff_total else 0)})",
        f"the largest split inside the segment is the internal-vs-external definition: "
        f"{et} → {it} carries {fmt_num(st['count'])} movements ({fmt_money(st['amount'])}, "
        f"{fmt_pct(st['count'] / diff_total if diff_total else 0)} of all differences) that Illion "
        f"reads as external transfers while finv, using transaction associations across a "
        f"customer's own cards/accounts, reads as transfers internal to the same bank",
        f"the remainder is mostly Illion-only {et} ({fmt_num(st['et_only'])} rows) plus the income "
        f"boundaries with All Other Credits and Wages",
    ]
    if it_item is not None and to_float(it_item.get("交集占比（并集）")) >= 0.5:
        parts.append(f"{it} itself agrees strongly ({fmt_pct(it_item.get('交集占比（并集）'))}); "
                     f"its differences are largely this reassignment")
    return "; ".join(parts) + "."


def transfer_summary_paragraphs_en(analysis: Analysis) -> List[str]:
    """Transfers-segment summary (two paragraphs, mirroring the Rent / Gambling deep dives).

    P1: agreement/volume contrast between the two categories plus the two-way ET→IT disputed flow;
    P2: the recognition mechanism with sample evidence + staged remediation.
    """
    et, it = "External Transfers", "Internal Transfer"
    et_item = analysis.category_by_name.get(et)
    it_item = analysis.category_by_name.get(it)
    if et_item is None or it_item is None:
        return ["The working paper lacks metrics for one of the transfer categories; no summary generated."]
    st = _transfer_pair_stats(analysis)
    diff_total = analysis.diff_total or 0
    total = analysis.total_transactions or 0
    paras: List[str] = []

    # P1 — agreement/volume contrast and the two-way ET→IT flow
    et_union = to_float(et_item.get("并集数量"))
    it_union = to_float(it_item.get("并集数量"))
    cr_total = st["credit"] + st["debit"]
    max_flow_amt = max((s["amount"] for s in analysis.flow_stats.values()), default=0.0)
    largest_txt = (" — the largest single disputed flow in value across the whole report"
                   if st["count"] and st["amount"] >= max_flow_amt
                   else " — the segment's largest disputed flow in value")
    p1 = (f"The two transfer categories diverge sharply: {et} agrees at "
          f"{fmt_pct(et_item.get('交集占比（并集）'))} on the union basis "
          f"({fmt_num(et_union)}-row union, {fmt_pct(et_union / total if total else 0)} of all "
          f"transactions), whereas {it} reaches {fmt_pct(it_item.get('交集占比（并集）'))} "
          f"({fmt_num(it_union)} rows, {fmt_pct(it_union / total if total else 0)}). Their related "
          f"differences are {fmt_pct(st['et_n'] / diff_total if diff_total else 0)} and "
          f"{fmt_pct(st['it_n'] / diff_total if diff_total else 0)} of all differences respectively, "
          f"and the {et} → {it} flow alone carries {fmt_num(st['count'])} movements worth "
          f"{fmt_money(st['amount'])}{largest_txt}; its rows split {fmt_pct(st['credit'] / cr_total if cr_total else 0)} "
          f"credit / {fmt_pct(st['debit'] / cr_total if cr_total else 0)} debit — genuine two-way "
          f"movement rather than one-directional outward payment.")
    paras.append(p1)

    # P2 — mechanism, sample evidence and staged remediation
    samples = analysis.top_samples_in_flow(et, (et, it), limit=1)
    sample_txt = ""
    if samples:
        s = samples[0]
        sample_txt = (f" (e.g. {s.get('transaction_date')} \"{s.get('text') or ''}\", "
                      f"{fmt_money(s.get('amount'))})")
    p2 = (f"Mechanism and how to fix it. The disputed rows carry clear signatures — same-amount "
          f"paired Internet Deposit/Withdrawal entries to/from the same account{sample_txt} and "
          f"card-to-card CommBank app transfers (\"Transfer to/from xx…\"): finv links such movements "
          f"across a customer's own cards/accounts and recognises them as internal to the bank, while "
          f"Illion, without that linkage, reads the counterparty/description (Funds Transfer etc.) as "
          f"external — so the same batch ends up classified differently on the two sides. Recommended "
          f"actions: 1) sample the {et} → {it} rows to confirm the two linked cards/accounts genuinely "
          f"belong to the same customer; if they do, take finv's linkage-based result as the reference "
          f"for internal transfers, or feed the card-group linkage back to Illion so both sides align")
    extras = []
    if st["et_only"]:
        extras.append(f"handle the Illion-only {et} rows ({fmt_num(st['et_only'])}) separately from the "
                      f"internal-vs-external definition dispute, and check whether they are Illion "
                      f"over-recognition or finv misses")
    extras.append("treat this flow as P1 — it is the segment's largest by both volume and value")
    if extras:
        p2 += "; " + "; ".join(f"{i + 2}) {e}" for i, e in enumerate(extras))
    p2 += "."
    paras.append(p2)
    return paras


def _bias_comment(fv_only: float, il_only: float) -> str:
    """One-sided-recognition sentence shared by the deep dives (2x rule for 'clear' bias)."""
    if fv_only > il_only * 2:
        return (f"Among mismatches the one-sided bias is clearly toward finv: finv-only recognition is "
                f"{fmt_pct(fv_only)} versus {fmt_pct(il_only)} for Illion-only")
    if il_only > fv_only * 2:
        return (f"Among mismatches the one-sided bias is clearly toward Illion: Illion-only recognition is "
                f"{fmt_pct(il_only)} versus {fmt_pct(fv_only)} for finv-only")
    return (f"Among mismatches one-sided recognition is balanced (Illion-only {fmt_pct(il_only)} vs "
            f"finv-only {fmt_pct(fv_only)})")


def _direction_comment(cat: str, credit: int, debit: int) -> str:
    """Direction clause, no leading/trailing whitespace, no trailing period (caller joins)."""
    if not (credit + debit):
        return ""
    dr_share = debit / (credit + debit)
    spend_cats = ('Rent', 'Gambling', 'Information', 'Retail', 'Donations', 'Automotive')
    if dr_share >= 0.5:
        tail = ", consistent with spend" if cat in spend_cats else ""
        return (f"Direction is debit-led, at {fmt_pct(dr_share)} of the related difference rows{tail}")
    return f"Direction is credit-led, with credit at {fmt_pct(1 - dr_share)} of the related difference rows"


def _flow_aggregates(analysis: Analysis, cat: str) -> Dict[str, Any]:
    all_flows = sorted(
        [(il, fv, dt, s["count"], s["amount"])
         for (il, fv, dt), s in analysis.flow_stats.items() if cat in (il, fv)],
        key=lambda x: x[3], reverse=True,
    )
    return {
        "flows": all_flows,
        "finv_only_n": sum(f[3] for f in all_flows if f[1] == cat and f[0] == ""),
        "illion_only_n": sum(f[3] for f in all_flows if f[0] == cat and f[1] == ""),
        "trans_in": sum(f[3] for f in all_flows if f[1] == cat
                        and f[0] in ("External Transfers", "Internal Transfer")),
        "trans_out": sum(f[3] for f in all_flows if f[0] == cat
                         and f[1] in ("External Transfers", "Internal Transfer")),
    }


def rent_summary_paragraphs_en(analysis: Analysis, item: Dict[str, Any]) -> List[str]:
    """Rent deep dive, two paragraphs: alignment/volume/direction/bias, then flow mix, cause and action."""
    inter_share = to_float(item.get("交集占比（并集）"))
    il_only = to_float(item.get("illion独有占比（并集）"))
    fv_only = to_float(item.get("finv独有占比（并集）"))
    union = to_float(item.get("并集数量"))
    total = analysis.total_transactions or 0
    diff_total = analysis.diff_total or 0
    detail_n = len(analysis.category_details.get("Rent", []))
    agg = _flow_aggregates(analysis, "Rent")
    all_flows, finv_only_n, illion_only_n, trans_in = (agg["flows"], agg["finv_only_n"],
                                                       agg["illion_only_n"], agg["trans_in"])
    paras: List[str] = []

    p1 = f"Rent aligns well: agreement on the union basis is {fmt_pct(inter_share)}"
    if union and total:
        p1 += f", and the union covers only {fmt_pct(union / total)} of all transactions"
    credit, debit = analysis.dr_cr_distribution("Rent")
    clauses = [p1, _direction_comment("Rent", credit, debit), _bias_comment(fv_only, il_only)]
    paras.append(". ".join(c for c in clauses if c) + ".")

    if not all_flows or not detail_n:
        paras.append("No difference rows reference Rent beyond the above; the two sides agree and no "
                     "targeted rule change is indicated.")
        return paras
    p2 = f"Rent-related differences account for {fmt_pct(detail_n / diff_total) if diff_total else ''} of all differences."
    flow_parts = []
    if trans_in and finv_only_n:
        flow_parts.append(
            f"The two dominant flows are transfer inflows into Rent (Internal Transfer → Rent and "
            f"External Transfers → Rent) at {fmt_pct(trans_in / detail_n)} of Rent-related differences "
            f"and finv-only recognition (no Illion label) at {fmt_pct(finv_only_n / detail_n)}"
        )
        it_rent = [f for f in all_flows if f[0] == "Internal Transfer" and f[1] == "Rent"]
        if it_rent:
            flow_parts.append(f"Internal Transfer → Rent alone accounts for {fmt_pct(it_rent[0][3] / detail_n)} "
                              f"of Rent-related differences (worth {fmt_money(it_rent[0][4])})")
    else:
        top = all_flows[0]
        flow_parts.append(f"Differences concentrate in the {en_flow_cell(top[0], top[1])} flow "
                          f"({fmt_pct(top[3] / detail_n)} of Rent-related differences)")
    if illion_only_n:
        flow_parts.append(f"Illion-only recognition adds {fmt_pct(illion_only_n / detail_n)} of "
                          f"Rent-related differences")
    p2 += " " + "; ".join(flow_parts) + "."
    if trans_in >= detail_n * 0.55:
        p2 += (f" Transfer inflows together are {fmt_pct(trans_in / detail_n)} of Rent-related differences — "
               f"close to three in five — so the root cause is the boundary between rent payments and "
               f"transfers: a meaningful share of rent is being paid by transfer and lands outside Rent in "
               f"Illion. We recommend reviewing rules for transfer counterparties that look like rent payees "
               f"(property managers, strata and real-estate agents) and treating likely-rent payees as Rent first.")
    elif trans_in >= detail_n * 0.4 and trans_in >= finv_only_n:
        p2 += (f" Transfer inflows make up {fmt_pct(trans_in / detail_n)} of Rent-related differences, so the "
               f"rent-vs-transfer boundary is the primary issue: part of the rent paid by transfer is not "
               f"recognised as Rent. We recommend checking transfer counterparties with rent-like payee "
               f"characteristics first, then sampling the finv-only rows.")
    elif finv_only_n >= detail_n * 0.2:
        p2 += (f" Taken together, Rent's main gap is Illion under-recognising rent transactions "
               f"(finv-only rows are {fmt_pct(finv_only_n / detail_n)} of Rent-related differences); we "
               f"recommend widening Illion's rent keyword list and merchant knowledge base.")
    elif illion_only_n >= detail_n * 0.2:
        p2 += (f" Taken together, Rent's main gap is finv under-recognising rent transactions "
               f"(Illion-only rows are {fmt_pct(illion_only_n / detail_n)} of Rent-related differences); we "
               f"recommend widening finv's rent keyword list and merchant knowledge base.")
    else:
        p2 += (" Overall, Rent differences are dispersed; sample the main flows above before changing rules.")
    paras.append(p2)
    return paras


def gambling_summary_paragraphs_en(analysis: Analysis, item: Dict[str, Any]) -> List[str]:
    """Gambling deep dive, two paragraphs (same structure as the Chinese special section)."""
    inter_share = to_float(item.get("交集占比（并集）"))
    il_only = to_float(item.get("illion独有占比（并集）"))
    fv_only = to_float(item.get("finv独有占比（并集）"))
    union = to_float(item.get("并集数量"))
    total = analysis.total_transactions or 0
    diff_total = analysis.diff_total or 0
    detail_n = len(analysis.category_details.get("Gambling", []))
    agg = _flow_aggregates(analysis, "Gambling")
    all_flows, finv_only_n, illion_only_n, trans_in = (agg["flows"], agg["finv_only_n"],
                                                       agg["illion_only_n"], agg["trans_in"])
    paras: List[str] = []

    p1 = f"Gambling agreement on the union basis is {fmt_pct(inter_share)}"
    if union and total:
        p1 += f", and the union covers {fmt_pct(union / total)} of all transactions"
    credit, debit = analysis.dr_cr_distribution("Gambling")
    clauses = [p1, _direction_comment("Gambling", credit, debit), _bias_comment(fv_only, il_only)]
    paras.append(". ".join(c for c in clauses if c) + ".")

    if not all_flows or not detail_n:
        paras.append("No difference rows reference Gambling beyond the above; the two sides agree and no "
                     "targeted rule change is indicated.")
        return paras
    p2 = f"Gambling-related differences are {fmt_pct(detail_n / diff_total) if diff_total else ''} of all differences. "
    if trans_in and finv_only_n and trans_in + finv_only_n >= detail_n * 0.5:
        p2 += (f"The dominant sources are finv-only recognition (no Illion label) and transfer inflows "
               f"from the transfer categories (External Transfers → Gambling, Internal Transfer → Gambling), "
               f"at {fmt_pct(finv_only_n / detail_n)} and {fmt_pct(trans_in / detail_n)} of Gambling-related "
               f"differences respectively")
        if illion_only_n:
            p2 += f"; Illion-only recognition (no finv label) is {fmt_pct(illion_only_n / detail_n)}"
        p2 += ". "
        p2 += (f"finv-only recognition plus transfer inflows together run to about "
               f"{fmt_pct((finv_only_n + trans_in) / detail_n)}, so Gambling differences sit mainly at the "
               f"boundary between gambling recognition and transfers/other spend. That points either to Illion "
               f"missing gambling done through transfer channels or to finv over-recognising it. We recommend "
               f"reviewing rules for gambling-looking payees among transfers and sampling the finv-only rows "
               f"to tell genuine coverage expansion from over-recognition.")
    else:
        top = all_flows[0]
        p2 += (f"Differences concentrate in the {en_flow_cell(top[0], top[1])} flow "
               f"({fmt_pct(top[3] / detail_n)} of Gambling-related differences)")
        extras = []
        if finv_only_n:
            extras.append(f"finv-only recognition (no Illion label) is {fmt_pct(finv_only_n / detail_n)}")
        if illion_only_n:
            extras.append(f"Illion-only recognition (no finv label) is {fmt_pct(illion_only_n / detail_n)}")
        if trans_in:
            extras.append(f"transfer inflows from the transfer categories (External Transfers → Gambling, "
                          f"Internal Transfer → Gambling) are {fmt_pct(trans_in / detail_n)}")
        if extras:
            p2 += "; " + "; ".join(extras)
        p2 += ". "
        p2 += ("Overall, Gambling differences are dispersed; sample the main flows above before changing rules.")
    paras.append(p2)
    return paras


def information_summary_paragraphs_en(analysis: Analysis, item: Dict[str, Any]) -> List[str]:
    """Information deep dive: definitional mismatch (zero-amount notices vs paid content), guarded conclusion."""
    inter_share = to_float(item.get("交集占比（并集）"))
    il_only = to_float(item.get("illion独有占比（并集）"))
    fv_only = to_float(item.get("finv独有占比（并集）"))
    union = to_float(item.get("并集数量"))
    total = analysis.total_transactions or 0
    diff_total = analysis.diff_total or 0
    il_cov = to_float(item.get("illion覆盖率"))
    fv_cov = to_float(item.get("finv覆盖率"))
    il_cat = analysis.category_details.get("Information", [])

    # rows each side put into Information (a detail row can be on both sides when they agree)
    il_rows = [d for d in il_cat if d["illion_category"] == "Information"]
    fv_rows = [d for d in il_cat if d["finv_category"] == "Information"]
    both = [d for d in il_rows if d["finv_category"] == "Information"]
    both_n = len(both)
    il_zero_n = len([d for d in il_rows if to_float(d.get("amount")) == 0])
    il_zero_rows = [d for d in il_cat if d["illion_category"] == "Information"
                    and not d["finv_category"] and to_float(d.get("amount")) == 0]
    fv_zero_rows = [d for d in il_cat if d["finv_category"] == "Information"
                    and not d["illion_category"] and to_float(d.get("amount")) == 0]
    il_zero_share = il_zero_n / len(il_rows) if il_rows else 0
    paras: List[str] = []

    p1 = (f"Information is the widest mismatch in the expense segment: agreement on the union basis "
          f"is only {fmt_pct(inter_share)}")
    if union and total:
        p1 += f", and the union covers {fmt_pct(union / total)} of all transactions"
    credit, debit = analysis.dr_cr_distribution("Information")
    clauses = [p1, _direction_comment("Information", credit, debit), _bias_comment(fv_only, il_only)]
    paras.append(". ".join(c for c in clauses if c) + ".")

    il_cat_n = len(il_cat)
    if not il_cat_n:
        paras.append("No difference rows reference Information beyond the above; the two sides agree.")
        return paras
    p2 = f"Information-related differences are {fmt_pct(il_cat_n / diff_total) if diff_total else ''} of all differences"
    fv_flow_n = sum(s["count"] for (il, fv, _), s in analysis.flow_stats.items()
                    if fv == "Information" and il == "")
    il_flow_n = sum(s["count"] for (il, fv, _), s in analysis.flow_stats.items()
                    if il == "Information" and fv == "")
    extras = []
    if fv_flow_n:
        extras.append(f"finv-only recognition (no Illion label) is {fmt_pct(fv_flow_n / il_cat_n)}")
    if il_flow_n:
        extras.append(f"Illion-only recognition (no finv label) is {fmt_pct(il_flow_n / il_cat_n)}")
    if extras:
        p2 += ", where " + " and ".join(extras)
    p2 += ". "
    if il_zero_share >= 0.5:
        sample_rows = il_zero_rows or fv_zero_rows
        sample_txt = ""
        if sample_rows:
            sample_txt = f"such as \"{sample_rows[0].get('text') or ''}\""
        p2 += (f"Digging into the two definitions: {fmt_pct(il_zero_share)} of the rows Illion classifies as "
               f"Information are zero-amount fee-waiver or notification items {sample_txt}, while finv's "
               f"Information consists of paid information services with actual amounts")
        if both_n == 0:
            p2 += (f", and the two sides share no common rows. The gap is therefore not a dispute over the "
                   f"same transaction but a definitional mismatch: Illion books zero-amount notices into "
                   f"Information, while finv reserves the category for genuine paid information spend")
        else:
            union_n = len(il_rows) + len(fv_rows) - both_n
            p2 += (f". Few rows are shared (intersection {fmt_pct(both_n / union_n if union_n else 0)} of the "
                   f"union), so the gap is largely a definitional mismatch rather than a dispute over the same "
                   f"transactions")
        p2 += "."
    # guarded conclusion: claim finv is broader only when coverage actually says so
    if fv_cov >= il_cov:
        p2 += (f" Overall, finv's Information recognition is materially broader than Illion's "
               f"(coverage {fmt_pct(fv_cov)} vs {fmt_pct(il_cov)}). We recommend adopting finv's basis, "
               f"deciding explicitly whether Information may include zero-amount items, reviewing finv's "
               f"information-service rules and Illion's boundary, and either confirming the added recognition "
               f"or re-classifying fee-waiver/notification rows separately.")
    else:
        p2 += (f" Overall, the two sides differ mainly in scope (coverage Illion {fmt_pct(il_cov)} vs finv "
               f"{fmt_pct(fv_cov)}) with little overlap in the rows each side classifies. We recommend first "
               f"deciding whether Information may include zero-amount items, then aligning the information-service "
               f"rules and boundaries on both sides.")
    paras.append(p2)
    return paras


def expense_flag_summary_en(analysis: Analysis, item: Dict[str, Any]) -> List[str]:
    """Two-paragraph special summaries for the flagged expense categories.

    Gambling and Information delegate to their own functions; Retail / Donations / Automotive
    use category-specific cause analysis below, with a generic fallback.
    """
    cat = item["category"]
    if cat == "Gambling":
        return gambling_summary_paragraphs_en(analysis, item)
    if cat == "Information":
        return information_summary_paragraphs_en(analysis, item)

    inter_share = to_float(item.get("交集占比（并集）"))
    il_only = to_float(item.get("illion独有占比（并集）"))
    fv_only = to_float(item.get("finv独有占比（并集）"))
    union = to_float(item.get("并集数量"))
    total = analysis.total_transactions or 0
    diff_total = analysis.diff_total or 0
    detail_n = len(analysis.category_details.get(cat, []))
    agg = _flow_aggregates(analysis, cat)
    all_flows, finv_only_n, illion_only_n, trans_in = (agg["flows"], agg["finv_only_n"],
                                                       agg["illion_only_n"], agg["trans_in"])
    paras: List[str] = []

    p1 = f"{cat} agreement on the union basis is {fmt_pct(inter_share)}"
    if union and total:
        p1 += f", and the union covers {fmt_pct(union / total)} of all transactions"
    credit, debit = analysis.dr_cr_distribution(cat)
    clauses = [p1, _direction_comment(cat, credit, debit), _bias_comment(fv_only, il_only)]
    paras.append(". ".join(c for c in clauses if c) + ".")

    if not all_flows or not detail_n:
        paras.append(f"No difference rows reference {cat} beyond the above; the two sides agree.")
        return paras
    top = all_flows[0]
    top_is_fv_only = top[0] == "" and top[1] == cat
    top_is_il_only = top[0] == cat and top[1] == ""
    p2 = (f"{cat}-related differences are "
          f"{fmt_pct(detail_n / diff_total) if diff_total else ''} of all differences.")
    if top_is_fv_only:
        p2 += (f" Differences are dominated by finv-only recognition (no Illion label), at "
               f"{fmt_pct(top[3] / detail_n)} of {cat}-related differences")
    elif top_is_il_only:
        p2 += (f" Differences are dominated by Illion-only recognition (no finv label), at "
               f"{fmt_pct(top[3] / detail_n)} of {cat}-related differences")
    else:
        p2 += (f" Differences concentrate in the {en_flow_cell(top[0], top[1])} flow "
               f"({fmt_pct(top[3] / detail_n)} of {cat}-related differences)")
    extras = []
    if finv_only_n and not top_is_fv_only:
        extras.append(f"finv-only recognition (no Illion label) is {fmt_pct(finv_only_n / detail_n)}")
    if illion_only_n and not top_is_il_only:
        extras.append(f"Illion-only recognition (no finv label) is {fmt_pct(illion_only_n / detail_n)}")
    if trans_in and not (top[0] in ("External Transfers", "Internal Transfer") and top[1] == cat):
        extras.append(f"transfer inflows from the transfer categories (External Transfers → {cat}, "
                      f"Internal Transfer → {cat}) are {fmt_pct(trans_in / detail_n)}")
    if extras:
        p2 += "; " + "; ".join(extras)
    p2 += ". "

    if cat == "Retail":
        p2 += (f"The root cause of Retail's gap is the boundary between retail payments and transfers: "
               f"retail purchases paid by transfer are booked as Retail by finv but as transfers by Illion, "
               f"and finv's one-sided rows extend that broader treatment to retail merchants Illion leaves "
               f"unclassified; the net effect is that finv recognises retail spend more broadly. We recommend "
               f"checking rules for transfer counterparties with retail characteristics (supermarkets, "
               f"convenience stores) and sampling finv-only Retail rows to confirm they are reasonable.")
    elif cat == "Donations":
        p2 += (f"The root cause of Donations' gap runs the other way from most red categories: finv "
               f"under-recognises charitable giving — Illion flags giving-style transactions that finv leaves "
               f"unlabelled, so the gap is a finv coverage shortfall rather than a boundary dispute. Given the "
               f"category is small (union {fmt_pct(union / total) if total else ''} of all transactions), we "
               f"recommend adding giving-style merchants/keywords to finv and re-running the comparison.")
    elif cat == "Automotive":
        g2a = sum(f[3] for f in all_flows if f[0] == "Groceries" and f[1] == cat)
        a2g = sum(f[3] for f in all_flows if f[0] == cat and f[1] == "Groceries")
        t2a = sum(f[3] for f in all_flows if f[0] in ("External Transfers", "Internal Transfer") and f[1] == cat)
        a2t = sum(f[3] for f in all_flows if f[0] == cat and f[1] in ("External Transfers", "Internal Transfer"))
        a2e = sum(f[3] for f in all_flows if f[0] == cat and f[1] == "Entertainment")
        p2 += (f"Automotive differences have no single root cause; they reflect systematic boundary "
               f"disagreements in how vehicle-related spend (fuel, servicing, insurance) is assigned by the "
               f"two rule books: the main flows are bidirectional between Groceries and Automotive "
               f"({fmt_pct((g2a + a2g) / detail_n)} combined) and between the transfer categories and "
               f"Automotive ({fmt_pct((t2a + a2t) / detail_n)}), plus Automotive → Entertainment "
               f"({fmt_pct(a2e / detail_n)}), while one-sided rows on both sides add further volume, so "
               f"sampling before rule changes is the right next step: start with Groceries (car accessories "
               f"bought at supermarkets) and transfers (cars paid by transfer), then align the rules.")
    else:
        p2 += (f"Overall, {cat} differences are dispersed; sample the main flows above before changing rules.")
    paras.append(p2)
    return paras


# ---------------------------------------------------------------------------
# Block and table builders
# ---------------------------------------------------------------------------


def render_category_block_en(doc: Document, item: Dict[str, Any], analysis: Analysis,
                             detailed: bool = False, number: Optional[str] = None,
                             extra_samples: Optional[List[Dict[str, Any]]] = None) -> None:
    """Conclusion-first block for one category: headline → definition / key metrics / flows / samples."""
    category = item["category"]
    group = item.get("group", "")
    # 「Direction & value」只覆盖底稿给出金额的流向，先算好覆盖范围交给 add_para_labeled
    doc.amount_note = _amount_note_en(analysis, category) if detailed else ""
    if number is None:
        add_heading(doc, f"Category: {category} ({GROUP_EN.get(group, group)})", level=3)
    elif number:
        add_heading(doc, f"{number} {category}", level=3)

    add_para(doc, category_headline_en(item, analysis), size=9.5, space_after=4)

    add_para(doc, "Category detail", size=9.5, bold=True, space_after=2)
    add_para_labeled(doc, "Definition",
                     CATEGORY_DEFINITION_EN.get(category, ""), size=9.5, space_after=3)

    add_para(doc, "Key indicators", size=9.5, bold=True, space_after=2)
    total = analysis.total_transactions
    union = to_float(item.get("并集数量"))
    inter = to_float(item.get("交集数量"))
    add_table(doc,
              ["Category", "Illion cov.", "finv cov.", "Union %", "Intersec. %",
               "Agreement (union)", "Illion-only", "finv-only"],
              [[category, fmt_pct(item.get("illion覆盖率")), fmt_pct(item.get("finv覆盖率")),
                fmt_pct(union / total if total else 0), fmt_pct(inter / total if total else 0),
                fmt_pct(item.get("交集占比（并集）")),
                fmt_pct(item.get("illion独有占比（并集）")), fmt_pct(item.get("finv独有占比（并集）"))]],
              [2.7, 1.7, 1.7, 1.7, 1.8, 2.4, 2.1, 2.1], font_size=8.5,
              align_center_cols={1, 2, 3, 4, 5, 6, 7},
              highlights=indicator_highlights_en([item]))
    add_para_labeled(doc, "Interpretation", category_narrative_en(item, total), size=9.5, space_after=3)

    if detailed:
        flows = analysis.category_flows(category, limit=5)
        if flows:
            add_para(doc, "Top difference flows", size=9.5, bold=True, space_after=2)
            add_flow_table_en(doc, flows, denom=len(analysis.category_details.get(category, [])),
                              share_label="Share of cat. diffs")
        credit, debit = analysis.dr_cr_distribution(category)
        dir_total = credit + debit
        if dir_total:
            credit_share = fmt_pct(credit / dir_total)
            debit_share = fmt_pct(debit / dir_total)
            dir_txt = f"Of the difference rows, {credit_share} are credit and {debit_share} debit; "
        else:
            dir_txt = "The difference rows carry no recorded direction; "
        total_amount = sum(stat['amount'] for (il, fv, _), stat in analysis.flow_stats.items()
                           if category in (il, fv))
        add_para_labeled(doc, "Direction & value",
                         f"{dir_txt}combined difference value is {fmt_money(total_amount)}.",
                         size=9.5, space_after=2)
        add_para(doc, "Sample transactions", size=9.5, bold=True, space_after=2)
        samples = analysis.top_samples(category, limit=5)
        if extra_samples:
            seen = {(s.get("text") or "", s.get("transaction_date") or "") for s in samples}
            for s in extra_samples:
                key = (s.get("text") or "", s.get("transaction_date") or "")
                if key not in seen:
                    seen.add(key)
                    samples.append(s)
            samples.sort(key=lambda d: -abs(to_float(d.get("amount")) or 0))
        add_samples_table_en(doc, samples)
    doc.amount_note = ""


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------
# difference-source English labels (keys are the Chinese labels from the workbook layer)
SRC_EN = {"双方分类不一致": "classification disagreement", "仅 finv 有值": "finv-only recognition",
          "仅 Illion 有值": "Illion-only recognition"}


def build_report_en(doc: Document, analysis: Analysis, chart_png: Optional[Path] = None,
                    chart_2_2_png: Optional[Path] = None,
                    input_name: str = paths.REVIEW_REPORT_NAME,
                    generated_date: str = "") -> None:
    m = analysis

    # ===== Cover =====
    add_para(doc, "", size=10, space_after=40)
    add_para(doc, "BS-CAT Category Performance Report", size=24, bold=True, color=BLUE,
             align="center", space_after=6)
    add_para(doc, "Income, Expense, Transfer and Liability Category Performance — Illion vs finv",
             size=11, color=GRAY, align="center", space_after=24)
    add_table(doc, ["Sample scope", "Categories", "Comparison", "Report date"],
              [[f"{fmt_num(m.total_transactions)} transactions", f"{len(m.categories)} categories",
                "Illion vs finv | BS-CAT", generated_date]],
              [4.25, 4.25, 4.25, 4.25], font_size=10, align_center_cols=set())
    add_para(doc, "", size=10, space_after=12)
    add_callout(doc,
                f"This report is generated automatically from workbook {input_name} and covers "
                f"{len(m.categories)} categories across income, expenses, transfers and liabilities. "
                "Every figure is computed dynamically from the workbook; liability detail is kept at "
                "metric level because a dedicated Liability deep-dive report already exists.")
    add_para(doc, "", size=10, space_after=12)
    add_para(doc, "Data source", size=12, bold=True, color=BLUE_MID)
    add_para(doc, f"Excel workbook: sheet 00_核心对比 (headline and per-category metrics), "
                  f"01_差异诊断地图 (top {len(m.top_flows)} difference flows), "
                  f"03_排查明细 ({fmt_num(len(m.details))} difference rows with amounts and direction).",
             size=9.5, color=GRAY)
    add_page_break(doc)

    # ===== 1. Executive summary =====
    add_heading(doc, "1. Executive Summary", level=1)
    finv_only_share = m.finv_only_total / m.diff_total if m.diff_total else 0
    illion_only_share = m.illion_only_total / m.diff_total if m.diff_total else 0
    mismatch_share = m.mismatch / m.diff_total if m.diff_total else 0
    diff_sources = sorted(
        [("双方分类不一致", mismatch_share), ("仅 finv 有值", finv_only_share),
         ("仅 Illion 有值", illion_only_share)],
        key=lambda x: -x[1])
    cov_leader = "finv" if to_float(m.finv_coverage) >= to_float(m.illion_coverage) else "Illion"
    cov_follower = "Illion" if cov_leader == "finv" else "finv"
    leader_cov = m.finv_coverage if cov_leader == "finv" else m.illion_coverage
    follower_cov = m.illion_coverage if cov_leader == "finv" else m.finv_coverage
    cmp_txt = "slightly above" if abs(to_float(m.finv_coverage) - to_float(m.illion_coverage)) < 0.05 else "above"
    mismatch_is_top1 = diff_sources[0][0] == "双方分类不一致"
    gap_pp = abs(to_float(m.finv_coverage) - to_float(m.illion_coverage)) * 100
    diff_descs = [SRC_EN[name] for name, share in diff_sources]
    d1_name = diff_descs[0]
    summary = (
        f"This assessment compares the category labels that Illion and finv assign to the same "
        f"transactions. {cov_leader} assigns a category to {fmt_pct(leader_cov)} of all transactions — "
        f"{cmp_txt} {cov_follower}'s {fmt_pct(follower_cov)} (a {gap_pp:.2f} pp gap), so coverage is broadly "
        f"on a par, with {cov_leader} holding a modest edge. Where both sides classify the same "
        f"transaction, the two sides' labels agree in {fmt_pct(m.joint_agreement)} of cases, showing that "
        f"the two engines classify the rows they share consistently. Among the {fmt_num(m.diff_total)} "
        f"difference rows, "
        f"{d1_name} is the largest single source at {fmt_pct(diff_sources[0][1])}, followed by "
        f"{SRC_EN[diff_sources[1][0]]} ({fmt_pct(diff_sources[1][1])}) and "
        f"{SRC_EN[diff_sources[2][0]]} ({fmt_pct(diff_sources[2][1])}) — so the gap is driven first by "
        f"{'how the two sides label the rows they both classify' if mismatch_is_top1 else 'one side recognising rows the other misses'}, "
        f"{'with one-sided coverage secondary' if mismatch_is_top1 else 'with label disagreement secondary'}. "
        f"Difference rows concentrate in transfers and consumption. The headline issue is therefore not "
        f"coverage quantity but inconsistent rules and boundaries in specific categories; follow-up work "
        f"should target transfers and high-frequency consumption, and align the classification standard."
    )
    add_para(doc, summary, size=10.5)
    add_callout(doc, "Bottom line: the coverage comparison, the agreement on jointly classified rows, "
                     "the composition of differences, and the primary source of the gap are summarised above; "
                     "full detail follows in Sections 2–3.")
    add_page_break(doc)

    # ===== 2. Coverage and agreement overview =====
    add_heading(doc, "2. Coverage and Agreement Overview", level=1)
    add_para(doc, "This section works top-down: the overall agreement rate and the nature of the "
                  "differences, then the four business segments, then the category level. Coverage means the "
                  "share of all transactions that Illion/finv assigns to a category; the agreement rate is "
                  "the share of transactions with at least one side classified on which both sides assign "
                  "the same category. Full per-category metrics are rolled up in the Appendix (Section 4).",
             size=9.5)

    # 2.1 Overall coverage, agreement and the nature of differences
    add_heading(doc, "2.1 Overall Coverage, Agreement and Differences", level=2)
    adjusted_agreement = m.metrics.get("覆盖调整后一致率", {}).get("result")
    add_table(doc, ["Metric", "Value", "Definition"],
              [
                  ["Illion coverage", fmt_pct(m.illion_coverage), "transactions classified by Illion / all transactions"],
                  ["finv coverage", fmt_pct(m.finv_coverage), "transactions classified by finv / all transactions"],
                  ["Coverage gap (finv − Illion)", fmt_pct(to_float(m.finv_coverage) - to_float(m.illion_coverage)),
                   "how much wider finv is than Illion"],
                  ["Both classified", fmt_pct(m.joint_nonempty / m.total_transactions if m.total_transactions else 0),
                   "transactions classified by both / all transactions"],
                  ["Agreement (both classified)", fmt_pct(m.joint_agreement),
                   "same category on both sides / classified by both"],
                  ["Coverage-adjusted agreement", fmt_pct(adjusted_agreement),
                   "same category / at least one side classified"],
                  ["Difference rate (≥ one side)", fmt_pct(m.diff_total / m.union_nonempty if m.union_nonempty else 0),
                   "difference rows / at least one side classified"],
              ],
              [5.6, 3.6, 7.4], font_size=9, align_center_cols={1})
    union_rate = m.diff_total / m.union_nonempty if m.union_nonempty else 0
    d1, d2, d3 = diff_sources
    cov_gap_pp = abs(to_float(m.finv_coverage) - to_float(m.illion_coverage)) * 100
    coverage_degree = "slightly wider" if cov_gap_pp < 1.0 else "wider"
    p1 = (
        f"On coverage, {cov_leader} ({fmt_pct(leader_cov)}) runs {cov_gap_pp:.2f} pp ahead of "
        f"{cov_follower} ({fmt_pct(follower_cov)}), so its coverage is {coverage_degree}. Across all "
        f"transactions, {fmt_pct(m.joint_nonempty / m.total_transactions if m.total_transactions else 0)} "
        f"are classified by both sides, and within that set the two sides' labels agree "
        f"{fmt_pct(m.joint_agreement)} of the time — the two engines judge the rows they share quite "
        f"consistently. Adding the rows that only one side classifies (the “at least one side” "
        f"basis) drops the agreement rate to {fmt_pct(adjusted_agreement)}, with a difference rate of "
        f"{fmt_pct(union_rate)}. The {abs(float(fmt_pct(m.joint_agreement).rstrip('%')) - float(fmt_pct(adjusted_agreement).rstrip('%'))):.2f} pp "
        f"gap between the two bases comes from one-sided rows, which make up "
        f"{fmt_pct((m.union_nonempty - m.joint_nonempty) / m.union_nonempty if m.union_nonempty else 0)} of "
        f"the union — counting them as differences pulls the coverage-adjusted agreement rate down."
    )
    add_para(doc, p1, size=9.5)
    add_para(doc, "Splitting the difference rows by nature gives two buckets: classification disagreement "
                  "(both sides classified the row, but with different labels — a rules or boundary issue) and "
                  "one-sided recognition (only one side classified the row — a coverage gap). The table shows "
                  "the distribution:", size=9.5)
    diff_notes = {"双方分类不一致": "both sides classified, labels differ",
                  "仅 finv 有值": "finv recognised a category Illion did not",
                  "仅 Illion 有值": "Illion recognised a category finv did not"}
    diff_rows = [[SRC_EN[name].capitalize(), fmt_pct(share), diff_notes[name]] for name, share in diff_sources] + \
                [["Total difference rows", "100.00%", "disagreements plus one-sided rows"]]
    add_table(doc, ["Nature of difference", "Share of all diffs", "What it means"], diff_rows,
              [5.2, 2.8, 8.0], font_size=9, align_center_cols={1})
    d2_side = "finv" if d2[0] == "仅 finv 有值" else "Illion"
    d3_side = "finv" if d3[0] == "仅 finv 有值" else "Illion"
    # keep the closing sentence consistent with whatever source actually ranks first
    # (mirror of the conditional used in the executive summary)
    if mismatch_is_top1:
        driver_txt = (f"the overall gap is driven first by label disagreement, then by extra recognition "
                      f"on the {d2_side} side, with {d3_side}-only rows contributing least")
    else:
        driver_txt = (f"the overall gap is driven first by {SRC_EN[d1[0]]} (rows one side recognises and "
                      f"the other misses), then by {SRC_EN[d2[0]]}, with {SRC_EN[d3[0]]} contributing least")
    p2 = (
        f"{SRC_EN[d1[0]].capitalize()} is the largest source at {fmt_pct(d1[1])}, then "
        f"{SRC_EN[d2[0]]} ({fmt_pct(d2[1])}) and {SRC_EN[d3[0]]} ({fmt_pct(d3[1])}). The first two together "
        f"explain about {fmt_pct(d1[1] + d2[1])} of all differences. Rows with neither side classified "
        f"({fmt_pct(m.both_empty / m.total_transactions if m.total_transactions else 0)} of all "
        f"transactions) are excluded from difference analysis because no label comparison is possible. In "
        f"short, {cov_leader}'s coverage is slightly better, agreement within the shared set is high, and "
        f"{driver_txt}."
    )
    add_para(doc, p2, size=9.5)

    # 2.2 Business segments
    add_heading(doc, "2.2 Business Segments: Coverage and Agreement", level=2)
    add_para(doc, "The 36 categories form four segments: Income (3), Expenses (23), Transfers (2) and "
                  "Liabilities (8). Transfers are tracked separately as a neutral flow rather than being "
                  "folded into income or expenses. The segment agreement denominator is the segment union "
                  "(at least one side classified into the segment), matching the overall coverage-adjusted "
                  "agreement basis.", size=9.5)
    seg_cov_rows = []
    for group in GROUP_ORDER:
        sc = m.segment_coverage(group)
        union_share = sc["union_count"] / m.total_transactions if m.total_transactions else 0
        seg_cov_rows.append([
            GROUP_EN[group], sc["category_count"], fmt_pct(sc["illion_coverage"]),
            fmt_pct(sc["finv_coverage"]), fmt_pct(sc["exact_rate"]), fmt_pct(sc["broad_rate"]),
            fmt_pct(union_share),
        ])
    add_table(doc, ["Segment", "Cats", "Illion cov.", "finv cov.", "Agreement (union)",
                    "Agreement (in segment)", "Seg. union %"],
              seg_cov_rows, [2.6, 1.3, 2.0, 2.0, 2.9, 3.1, 2.1], font_size=8.5,
              align_center_cols={1, 2, 3, 4, 5, 6})
    add_para(doc, "“Agreement (union)” = same category on both sides / at least one side classified "
                  "into the segment; “Agreement (in segment)” = both sides in the segment (labels may "
                  "differ) / at least one side in it — the looser basis. “Seg. union %” = segment union / "
                  "all transactions; segment unions overlap on cross-segment rows, so the per-segment rates do "
                  "not sum to the overall rate — use the table for horizontal comparison.", size=8.5, color=GRAY)
    seg_cov_map = {g: m.segment_coverage(g) for g in GROUP_ORDER}
    best_g = max(GROUP_ORDER, key=lambda g: seg_cov_map[g]["exact_rate"])
    worst_g = min(GROUP_ORDER, key=lambda g: seg_cov_map[g]["exact_rate"])
    largest_g = max(GROUP_ORDER, key=lambda g: seg_cov_map[g]["union_count"])
    gap_g = max(GROUP_ORDER, key=lambda g: abs(seg_cov_map[g]["finv_coverage"] - seg_cov_map[g]["illion_coverage"]))
    gap_gap = abs(seg_cov_map[gap_g]["finv_coverage"] - seg_cov_map[gap_g]["illion_coverage"])
    worst_union_share = seg_cov_map[worst_g]["union_count"] / m.total_transactions if m.total_transactions else 0
    ws_worst = m.segment_summary(worst_g)
    best_broad_max = seg_cov_map[best_g]["broad_rate"] >= max(
        seg_cov_map[g]["broad_rate"] for g in GROUP_ORDER)
    worst_side = ("finv" if seg_cov_map[worst_g]["finv_coverage"] >= seg_cov_map[worst_g]["illion_coverage"]
                  else "Illion")
    worst_other = "Illion" if worst_side == "finv" else "finv"
    worst_side_cov = (seg_cov_map[worst_g]["finv_coverage"] if worst_side == "finv"
                      else seg_cov_map[worst_g]["illion_coverage"])
    worst_other_cov = (seg_cov_map[worst_g]["illion_coverage"] if worst_side == "finv"
                       else seg_cov_map[worst_g]["finv_coverage"])
    worst_only_share = ws_worst["finv_only"] if worst_side == "finv" else ws_worst["illion_only"]
    worst_vol_txt = (
        f"{GROUP_EN[worst_g]} is a small segment (union {fmt_pct(worst_union_share)} of all transactions), "
        f"so its low agreement moves the overall picture little"
        if worst_union_share < 0.1 else
        f"{GROUP_EN[worst_g]} is a large segment (union {fmt_pct(worst_union_share)} of all transactions) "
        f"and deserves priority attention")
    largest_share = m.segment_summary(largest_g)["share"]
    share_max_g = max(GROUP_ORDER, key=lambda g: m.segment_summary(g)["share"])
    share_txt = "the most concentrated of any segment" if share_max_g == largest_g else "relatively concentrated"
    gap_side = ("Illion" if seg_cov_map[gap_g]["illion_coverage"] > seg_cov_map[gap_g]["finv_coverage"]
                else "finv")
    gap_other = "finv" if gap_side == "Illion" else "Illion"
    add_callout(doc,
        f"Segment-level performance splits sharply. {GROUP_EN[best_g]} leads on both agreement bases "
        f"({fmt_pct(seg_cov_map[best_g]['exact_rate'])} union-based and "
        f"{fmt_pct(seg_cov_map[best_g]['broad_rate'])} in-segment), "
        f"{'the strongest of the four segments either way' if best_broad_max else 'the highest union-based rate of the four'}; "
        f"it is the segment where the two sides converge most. {GROUP_EN[worst_g]} has the lowest agreement, "
        f"at {fmt_pct(seg_cov_map[worst_g]['exact_rate'])} — but this is mostly a scope difference: within "
        f"the segment {worst_side} covers {fmt_pct(worst_side_cov)} versus {worst_other}'s "
        f"{fmt_pct(worst_other_cov)}, and {worst_side}-only rows make up "
        f"{fmt_pct(worst_only_share / ws_worst['total'] if ws_worst['total'] else 0)} of the segment's "
        f"differences. A low rate here reflects {worst_side}'s wider net more than weak recognition. "
        f"{worst_vol_txt}. {GROUP_EN[largest_g]} is the largest segment by volume, with a union of "
        f"{fmt_pct(seg_cov_map[largest_g]['union_count'] / m.total_transactions if m.total_transactions else 0)} "
        f"of all transactions (Illion {fmt_pct(seg_cov_map[largest_g]['illion_coverage'])}, finv "
        f"{fmt_pct(seg_cov_map[largest_g]['finv_coverage'])}), and its internal differences are "
        f"{share_txt}, contributing {fmt_pct(largest_share)} of all difference rows. "
        f"{GROUP_EN[gap_g]} is where the two sides' coverage diverges most — {gap_side} sits "
        f"{gap_gap * 100:.2f} pp above {gap_other}, the largest gap of any segment, signalling a real "
        f"disagreement about what belongs in {GROUP_EN[gap_g]}.")

    # 2.3 Category level
    add_heading(doc, "2.3 Category-Level Coverage and Agreement", level=2)
    add_para(doc, "The full per-category table (agreement on the union basis = intersection / category "
                  "union) is in Appendix 4.1; important categories are analysed in Section 3 and the "
                  "remaining ones are rolled up in Appendices 4.2–4.3. Traffic-light marks: red (bold) = "
                  "flag (agreement < 50% or an only-share > 30%); green (bold) = strong (agreement > 80%).",
             size=9.5)
    add_para(doc, "The two charts below give the category-level view — Chart 2.1 compares per-category "
                  "coverage and Chart 2.2 shows the distribution of agreement rates — followed by the "
                  "category-level conclusions.", size=9.5)
    if chart_2_2_png is not None and Path(chart_2_2_png).exists():
        pic_p = doc.add_paragraph()
        pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic_p.paragraph_format.space_before = Pt(2)
        pic_p.paragraph_format.space_after = Pt(2)
        pic_run = pic_p.add_run()
        pic_run.add_picture(str(chart_2_2_png), width=Cm(15.5))
        en_run_only(pic_run)
        add_para(doc, "Chart 2.1 Category coverage comparison (first two columns: bar length = that side's "
                      "coverage, i.e. rows classified by the side / all transactions; blue = Illion, "
                      "orange = finv. Right column: finv − Illion in pp; orange = finv wider, blue = Illion wider)",
                 size=9, bold=True, color=GRAY, align="center", space_after=8)
    if chart_png is not None and Path(chart_png).exists():
        pic_p = doc.add_paragraph()
        pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic_p.paragraph_format.space_before = Pt(2)
        pic_p.paragraph_format.space_after = Pt(2)
        pic_run = pic_p.add_run()
        pic_run.add_picture(str(chart_png), width=Cm(15.5))
        en_run_only(pic_run)
        add_para(doc, "Chart 2.2 Category agreement distribution by segment (agreement = intersection / "
                      "union; red < 50%, amber 50–80%, green ≥ 80%)",
                 size=9, bold=True, color=GRAY, align="center", space_after=8)
    low_cats = sorted([c for c in m.categories
                       if to_float(c.get("并集数量")) >= 500 and to_float(c.get("交集占比（并集）")) < 0.5],
                      key=lambda c: to_float(c.get("交集占比（并集）")))
    low_top = low_cats[:5]
    def _vol_txt(c: Dict[str, Any], i: int) -> str:
        share = fmt_pct(to_float(c.get("并集数量")) / m.total_transactions) if m.total_transactions else ""
        return f", with a union of {share} of all transactions" if i == 0 else f" ({share} of all transactions)"

    low_txt = "; ".join(
        f"{c['category']} agrees {fmt_pct(c.get('交集占比（并集）'))}{_vol_txt(c, i)}"
        for i, c in enumerate(low_top))
    low_names = ", ".join(c["category"] for c in low_top[:-1]) + \
                (f" and {low_top[-1]['category']}" if len(low_top) > 1 else "")
    high_cats = sorted([c for c in m.categories if to_float(c.get("并集数量")) >= 500],
                       key=lambda c: -to_float(c.get("交集占比（并集）")))
    high_top = high_cats[:3]
    high_names = ", ".join(c["category"] for c in high_top[:-1]) + \
                 (f" and {high_top[-1]['category']}" if len(high_top) > 1 else "")
    high_rates = ", ".join(fmt_pct(c.get("交集占比（并集）")) for c in high_top[:-1]) + \
                 (f" and {fmt_pct(high_top[-1].get('交集占比（并集）'))}" if len(high_top) > 1 else "")
    zero_cov = [c["category"] for c in m.categories if to_float(c.get("illion覆盖率")) == 0]
    zero_txt = ""
    if len(zero_cov) == 1:
        zero_txt = f"Separately, {zero_cov[0]} has no Illion coverage at all — an Illion blind spot."
    elif zero_cov:
        names = ", ".join(zero_cov[:-1]) + f" and {zero_cov[-1]}"
        zero_txt = f"Separately, {names} have no Illion coverage at all — Illion blind spots."
    add_callout(doc,
        f"Among categories with meaningful volume (union ≥ 500), the lowest-agreement "
        f"{'category is' if len(low_top) == 1 else f'{len(low_top)} categories are'} {low_names}: {low_txt}. "
        f"At the other end, the three strongest are {high_names}, agreeing {high_rates} respectively — "
        f"mostly clearly-ruled liability and fixed-spend categories. {zero_txt}")
    add_page_break(doc)

    # ===== 3. Segment deep dive =====
    add_heading(doc, "3. Category Review by Business Segment", level=1)
    add_para(doc, "This section answers, for each segment: which categories are stable, which show "
                  "one-sided coverage, and which need investigation. Each category is described with the "
                  "same seven ratios: 1 Illion coverage; 2 finv coverage; 3 union share of all transactions; "
                  "4 intersection share of all transactions; 5 agreement (category union); 6 Illion-only "
                  "share of the union; 7 finv-only share of the union. Union and intersection shares use all "
                  "transactions as the denominator, agreement uses the category union; because a row the two "
                  "sides classify differently is counted in both category unions, category agreement rates "
                  "do not add up to the overall coverage-adjusted rate "
                  f"({fmt_pct(m.metrics.get('覆盖调整后一致率', {}).get('result'))}, transaction-deduplicated).",
             size=9.5)

    def categories_in(group: str) -> List[Dict[str, Any]]:
        return [item for item in m.categories if item["group"] == group]

    # 3.1 Income
    add_heading(doc, "3.1 Income Segment", level=2)
    add_para(doc, segment_headline_en(m, "收入类", categories_in("收入类")), size=9.5, space_after=4)
    for i, item in enumerate(categories_in("收入类"), 1):
        render_category_block_en(doc, item, m, detailed=True, number=f"3.1.{i}")
    add_para(doc, "Income boundary notes", size=9.5, bold=True, space_after=2)
    add_boundary_bullets_en(doc, [("Wages", "All Other Credits"), ("Wages", "External Transfers"),
                                  ("All Other Credits", "External Transfers")], m)
    finv_only_leader = side_only_leader_en(m, "收入类", "finv")
    add_para(doc, f"Income differences are credit-led, as expected for an income-recognition exercise. "
                  f"finv-only income recognition lands mostly on {finv_only_leader or 'a few categories'}, "
                  f"so sample those rows to confirm the descriptions meet the income definitions.",
             size=9)
    add_page_break(doc)

    # 3.2 Expenses
    add_heading(doc, "3.2 Expenses Segment", level=2)
    add_para(doc, segment_headline_en(m, "支出类", categories_in("支出类")), size=9.5, space_after=4)
    add_para(doc, "The expenses segment holds 23 categories. Two are given full deep dives with top "
                  "difference flows, direction, amounts and samples — Gambling (3.2.1) and Rent (3.2.2). "
                  "Four flagged categories with meaningful difference volume — Retail, Information, "
                  "Donations and Automotive (3.2.3–3.2.6) — receive the same treatment. The remaining "
                  "categories are rolled up in Appendix 4.2.", size=9.5)

    # 3.2.1 Gambling deep dive
    add_heading(doc, "3.2.1 Gambling Deep Dive", level=3)
    expense_items = categories_in("支出类")
    for item in expense_items:
        if item["category"] == "Gambling":
            # extra samples: finv-only Gambling rows (SCORE55 card + casino ATM withdrawals) are merged into
            # the sample table because their flow is small and top-5-by-amount would miss it
            extra = None
            finv_only_g = [d for d in m.details
                           if not d["illion_category"] and d["finv_category"] == "Gambling"]
            if finv_only_g:
                def _pick_g(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
                    picked, seen = [], set()
                    for d in sorted(rows, key=lambda x: -abs(to_float(x.get("amount")) or 0)):
                        key = (d.get("text") or "", d.get("transaction_date") or "")
                        if key in seen:
                            continue
                        seen.add(key)
                        picked.append(d)
                        if len(picked) >= limit:
                            break
                    return picked
                score55_rows = [d for d in finv_only_g
                                if "SCORE55" in (d.get("counterparty") or "").upper()]
                atm_rows = [d for d in finv_only_g if "ATM" in (d.get("text") or "").upper()]
                extra = _pick_g(score55_rows, 1) + _pick_g(atm_rows, 1) or _pick_g(finv_only_g, 2)
            render_category_block_en(doc, item, m, detailed=True, number="", extra_samples=extra)
    gambling_item = m.category_by_name.get("Gambling")
    if gambling_item:
        add_para(doc, "Gambling difference analysis summary:", size=9.5, bold=True, space_after=2)
        for text in expense_flag_summary_en(m, gambling_item):
            add_para(doc, text, size=9.5, space_after=3)
    add_page_break(doc)

    # 3.2.2 Rent deep dive
    add_heading(doc, "3.2.2 Rent Deep Dive", level=3)
    rent = m.category_by_name.get("Rent")
    if rent:
        render_category_block_en(doc, rent, m, detailed=True, number="")
        add_para(doc, "Flows between Rent and adjacent categories:", size=9.5, bold=True, space_after=2)
        rent_flows = []
        for other in ["External Transfers", "Internal Transfer", "Utilities", "Home Improvement"]:
            rent_flows.extend(m.flow_between("Rent", other))
        rent_flows = sorted(rent_flows, key=lambda x: x[3], reverse=True)
        if rent_flows:
            add_flow_table_en(doc, rent_flows, denom=len(m.category_details.get("Rent", [])),
                              share_label="Share of cat. diffs")
        else:
            add_para(doc, "No difference flows were found between Rent and the categories above.",
                     size=9)
        add_para(doc, "Rent difference analysis summary:", size=9.5, bold=True, space_after=2)
        for text in rent_summary_paragraphs_en(m, rent):
            add_para(doc, text, size=9.5, space_after=3)
    add_page_break(doc)

    # 3.2.3–3.2.6 flagged expense categories
    for flag_i, flag_cat in enumerate(["Retail", "Information", "Donations", "Automotive"], 3):
        flag_item = m.category_by_name.get(flag_cat)
        if not flag_item:
            continue
        add_heading(doc, f"3.2.{flag_i} {flag_cat} Deep Dive", level=3)
        render_category_block_en(doc, flag_item, m, detailed=True, number="")
        add_para(doc, f"{flag_cat} difference analysis summary:", size=9.5, bold=True, space_after=2)
        for text in expense_flag_summary_en(m, flag_item):
            add_para(doc, text, size=9.5, space_after=3)
    add_page_break(doc)

    # 3.3 Transfers
    add_heading(doc, "3.3 Transfers Segment", level=2)
    add_para(doc, segment_headline_en(m, "转账类", categories_in("转账类")), size=9.5, space_after=4)
    add_para(doc, "The transfers segment holds External Transfers and Internal Transfer, tracked as a "
                  "neutral flow: they are excluded from income/expense totals but included in overall "
                  "coverage, difference-rate and classification-migration analysis. Beyond the seven "
                  "metrics we add the credit/debit split and the in/out direction, plus boundary checks "
                  "against Wages, All Other Credits, Rent and the other transfer category.", size=9.5)
    for i, item in enumerate(categories_in("转账类"), 1):
        render_category_block_en(doc, item, m, detailed=True, number=f"3.3.{i}")
    add_para(doc, "Transfer boundary notes", size=9.5, bold=True, space_after=2)
    add_boundary_bullets_en(doc, [("External Transfers", "Wages"), ("External Transfers", "All Other Credits"),
                                  ("External Transfers", "Rent"), ("External Transfers", "Internal Transfer")], m)
    add_para(doc, "Transfer differences summary:", size=9.5, bold=True, space_after=2)
    for text in transfer_summary_paragraphs_en(m):
        add_para(doc, text, size=9.5, space_after=3)
    add_para(doc, "Differences between transfers and income (Wages / All Other Credits) concentrate on "
                  "the credit side, so the receipts scenario is where the transfer-vs-income boundary "
                  "blurs; against Rent they concentrate on the debit side, suggesting rent paid by "
                  "transfer is being labelled as a transfer.", size=9)
    add_callout(doc, "Handling principle for transfers: treated as a neutral flow, excluded from income and "
                     "expense totals and from net-income calculations, but kept in overall coverage, "
                     "difference-rate and classification-migration analysis.")
    add_page_break(doc)

    # 3.4 Liabilities
    add_heading(doc, "3.4 Liabilities Segment", level=2)
    add_para(doc, segment_headline_en(m, "负债类", categories_in("负债类")), size=9.5, space_after=4)
    add_para(doc, "The liabilities segment holds 8 categories; SACC Loans, Non SACC Loans, Dishonours and "
                  "Credit Card Repayments are analysed below (top flows, direction, amounts and samples). "
                  "The remaining four (Debt Collection, Overdrawn, Debt Consolidation, Unknown Loans) are "
                  "rolled up in Appendix 4.3. Loan lifecycle, counterparty matching, Dishonours cases and "
                  "the SACC / Non-SACC cross-matrix are covered by the dedicated Liability report.",
             size=9.5)
    liab_i = 1
    for item in categories_in("负债类"):
        if item["category"] in ("SACC Loans", "Non SACC Loans", "Dishonours", "Credit Card Repayments"):
            render_category_block_en(doc, item, m, detailed=True, number=f"3.4.{liab_i}")
            liab_i += 1
    add_para(doc, "Liability segment difference ranking and top flows:", size=9.5, bold=True, space_after=2)
    liab_flows = m.segment_top_flows("负债类", limit=8)
    add_flow_table_en(doc, liab_flows, denom=m.segment_summary("负债类")["total"],
                      share_label="Share of seg. diffs")
    add_callout(doc, "Root-cause analysis of the liability segment (loan lifecycle, counterparty matching, "
                     "Dishonours cases, Unknown Loans drivers) is in the dedicated Liability report; this "
                     "report keeps the deep dives on important liability categories, the segment difference "
                     "ranking and the category-level positioning.")
    add_page_break(doc)

    # 3.5 Cross-segment recommendations
    add_heading(doc, "3.5 Cross-Segment Recommendations", level=2)
    add_para(doc, "Combining the overall difference structure with the category deep dives, we recommend "
                  "the following, in priority order:", size=9.5)

    def flow_ref_en(illion: str, finv: str) -> str:
        count = sum(stat["count"] for (il, fv, _), stat in m.flow_stats.items() if il == illion and fv == finv)
        if not count:
            return f"{illion} → {finv or BLANK}"
        share = fmt_pct(count / m.diff_total if m.diff_total else 0)
        return f"{illion} → {finv or BLANK} ({share})"

    add_table(doc, ["Priority", "Focus", "Recommended action", "Supporting data"],
              [
                  ["P1", "Validate finv's added recognition",
                   f"Sample the finv-only rows ({fmt_pct(m.finv_only_total / m.diff_total if m.diff_total else 0)} of "
                   "differences) to separate genuine coverage expansion from over-recognition",
                   "All Other Credits, Retail, Dining Out, Unknown Loans"],
                  ["P1", "Fix the transfer boundary",
                   "Split credit/debit handling and set recognition priority between transfers and income "
                   "(Wages / All Other Credits)",
                   flow_ref_en("External Transfers", "Internal Transfer") + "; " +
                   flow_ref_en("External Transfers", "All Other Credits")],
                  ["P1", "Fix loan-category boundaries",
                   "Re-check the knowledge base and aliases around Non SACC / SACC Loans and Unknown Loans",
                   flow_ref_en("Non SACC Loans", "Unknown Loans") + "; " +
                   flow_ref_en("SACC Loans", "Unknown Loans")],
                  ["P2", "Fix the Rent boundary",
                   "Review Rent vs transfers / Utilities / Home Improvement rules and build a Rent "
                   "regression set",
                   flow_ref_en("External Transfers", "Rent") + " and similar flows"],
                  ["P2", "Fix high-frequency spend boundaries",
                   "Build regression sets for Groceries, Dining Out, Retail, Automotive and Gambling",
                   flow_ref_en("Groceries", "Dining Out") + "; " + flow_ref_en("Groceries", "Retail") +
                   "; " + flow_ref_en("Groceries", "Automotive")],
                  ["P3", "Re-check remaining categories",
                   "Read low-volume category ratios with sample size in mind; check the flagged ratios in "
                   "the appendix",
                   "Appendix per-category ratios"],
              ],
              [1.6, 3.4, 6.6, 5.4], font_size=8.5, align_center_cols={0})
    add_para(doc, "Going forward: after every model or knowledge-base change, recompute the seven ratios "
                  "for all 36 categories and watch agreement (union), union share, finv-only and Illion-only "
                  "shares, and the Rent and transfer difference flows in particular.", size=9.5)
    add_page_break(doc)


    # ===== 4. Appendix =====
    add_heading(doc, "4. Appendix: Category Metrics and Roll-ups", level=1)
    add_para(doc, "4.1 lists all 36 categories with their seven ratios; 4.2 covers the 21 expense "
                  "categories outside the Rent and Gambling deep dives (Retail, Information, Donations and "
                  "Automotive were already analysed in 3.2.3–3.2.6 and are repeated here for reference, "
                  "without commentary); 4.3 covers the remaining 4 liability categories. The three sections "
                  "use exactly the same definitions as Sections 2 and 3 and are intended for quick scanning "
                  "and rule-checking.", size=9.5)

    # 4.1 Full metrics, all 36 categories
    add_para(doc, "4.1 Full Category Metrics (36 categories)", size=10, bold=True, space_after=2)
    for group in GROUP_ORDER:
        add_para(doc, f"{GROUP_EN[group]}:", size=9.5, bold=True, space_after=2)
        group_cats = [item for item in m.categories if item["group"] == group]
        cat_rows = []
        for item in group_cats:
            cat_rows.append([
                item["category"],
                fmt_pct(item.get("illion覆盖率")),
                fmt_pct(item.get("finv覆盖率")),
                fmt_pct(item.get("交集占比（并集）")),
                fmt_pct(item.get("illion独有占比（并集）")),
                fmt_pct(item.get("finv独有占比（并集）")),
                en_priority(item.get("建议优先级")),
            ])
        add_table(doc, ["Category", "Illion cov.", "finv cov.", "Agreement (union)",
                        "Illion-only", "finv-only", "Priority"],
                  cat_rows, [4.1, 1.9, 1.9, 2.5, 2.2, 2.2, 1.6], font_size=8,
                  align_center_cols={1, 2, 3, 4, 5, 6},
                  highlights=indicator_highlights_en(group_cats, col_inter=3, col_il=4, col_fv=5))

    def appendix_table(group: str, excluded: set, title: str) -> None:
        add_para(doc, title, size=10, bold=True, space_after=2)
        rows = []
        items = []
        for item in categories_in(group):
            if item["category"] not in excluded:
                items.append(item)
                rows.append([
                    item["category"],
                    fmt_pct(item.get("illion覆盖率")),
                    fmt_pct(item.get("finv覆盖率")),
                    fmt_pct(item.get("交集占比（并集）")),
                    fmt_pct(item.get("illion独有占比（并集）")),
                    fmt_pct(item.get("finv独有占比（并集）")),
                    en_priority(item.get("建议优先级")),
                ])
        add_table(doc, ["Category", "Illion cov.", "finv cov.", "Agreement (union)",
                        "Illion-only", "finv-only", "Priority"],
                  rows, [4.1, 1.9, 1.9, 2.5, 2.2, 2.2, 1.6], font_size=8,
                  align_center_cols={1, 2, 3, 4, 5},
                  highlights=indicator_highlights_en(items, col_inter=3, col_il=4, col_fv=5))

    appendix_table("支出类", {"Rent", "Gambling"}, "4.2 Expenses — remaining categories (21)")
    appendix_table("负债类", {"SACC Loans", "Non SACC Loans", "Dishonours", "Credit Card Repayments"},
                   "4.3 Liabilities — remaining categories (4)")
    add_para(doc, "Notes on definitions: union share (of all transactions) = category union / all "
                  "transactions; agreement (union) = intersection / category union; Illion-only and "
                  "finv-only shares are one-sided rows as a share of the category union. In every "
                  "“difference flows” table the share column uses that table's own context as the "
                  "denominator (category detail = the category's related differences; the liability "
                  "segment table = the segment's differences), i.e. it shows how the category/segment "
                  "differences are made up, not the share of all differences. Rows the two sides classify "
                  "differently are counted in both category unions, so per-category agreement rates do not "
                  f"sum to the overall coverage-adjusted rate ({fmt_pct(m.metrics.get('覆盖调整后一致率', {}).get('result'))}, "
                  "transaction-deduplicated). Priority comes from the workbook's 建议优先级 column, shown "
                  "as “-” when missing (the workbook column is '建议优先级'). Traffic lights: red "
                  "(bold) = flag (agreement < 50% or an only-share > 30%); green (bold) = strong "
                  "(agreement > 80%). Flow arrows “X → Y” mean Illion classified the row as X and finv as "
                  "Y; “(blank)” means that side did not classify the row.",
             size=8.5, color=GRAY)


# ===========================================================================
# 十、图表渲染（与图脚本同源，逐字保留）
# ===========================================================================

# 覆盖率列：格子内数据条（条长 = 覆盖率，线性比例 0–30%，用户选定「直接呈现大小」）
# 全图蓝橙体系：illion = 蓝、finv = 橙（与 diff 列「illion 更广=蓝 / finv 更广=橙」呼应，
# CVD ΔE 24.7 已验证 PASS）
BAR_COLOR_ILLION = "#2a78d6"
BAR_COLOR_FINV = "#eb6834"
SCALE_BAR_COLOR = "#8C8C8C"  # 底部比例尺：中性灰（仅长度参照，不表达身份）
PANEL_BG = "#FBF7EE"        # 格子浅金底（仅用于体现格子结构）
BAR_MAX = 30.0              # 条长比例上限（当前最大覆盖率 22.88%）
# diff 列发散色：蓝 = Illion 更广 / 白 = 相等 / 橙 = finv 更广（已通过 CVD 验证）
# 重点类别（行标签加粗）
HIGHLIGHT_CATS = ["External Transfers", "Wages", "All Other Credits", "Unknown Loans"]
TOP_N_STAR = 3  # 板块内 |diff| 前 N 加星号

# 板块名英文化（与图 2.1 一致）
GROUP_EN: Dict[str, str] = {"收入类": "Income", "支出类": "Expenses", "转账类": "Transfers", "负债类": "Liabilities"}

INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#898781"
TEXT_DARK = "#3c3c3c"


def render_coverage(path: Path, items: List[Dict[str, Any]],
                    input_name: str = paths.REVIEW_REPORT_NAME) -> None:
    """图 2.1：各类别覆盖率热力矩阵（illion / finv / diff），按业务板块分面。"""

    # matplotlib / numpy 是本图的重量级依赖，延迟到渲染时才导入：报告主体
    # （含 --check）不依赖它们，缺失时由 render_charts 兜底降级为「无此图」。
    import matplotlib
    matplotlib.use("Agg")  # 无 GUI 后端
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager
    from matplotlib.colors import LinearSegmentedColormap, ListedColormap
    from matplotlib.patches import Patch

    # diff 列发散色：蓝 = Illion 更广 / 白 = 相等 / 橙 = finv 更广（已通过 CVD 验证）
    cmap_diff = LinearSegmentedColormap.from_list("diff", ["#2a78d6", "#FFFFFF", "#eb6834"])
    for fname in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"):
        if Path(fname).exists():
            try:
                font_manager.fontManager.addfont(fname)
            except Exception:
                pass
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False

    groups: List[Dict[str, Any]] = []
    for group in GROUP_ORDER:
        rows = [it for it in items if it["group"] == group]
        for it in rows:
            it["_il"] = to_float(it.get("illion覆盖率")) * 100
            it["_fv"] = to_float(it.get("finv覆盖率")) * 100
            it["_diff"] = it["_fv"] - it["_il"]
        rows.sort(key=lambda r: -abs(r["_diff"]))  # 差异绝对值降序
        groups.append({"name": group, "rows": rows})

    # diff 列对称色标上限固定 3.0：绝大多数差异在 ±3pp 内，色深 = |diff| 且 ≥3pp 即饱和，
    # 让「更广」一侧颜色更明显（动态上限 7.5 会让 +2.23 之类只有 30% 色深）
    diff_vmax = 3.0

    fig = plt.figure(figsize=(12, 13.5))
    fig.suptitle("Category Coverage Comparison · Illion vs finv", fontsize=15, fontweight="bold",
                 color=INK, x=0.5, y=0.992)
    fig.text(0.5, 0.960,
             "Left two columns: horizontal bar = coverage share of all transactions "
             "(blue = Illion, orange = finv), bar length linear 0–30%, value at bar end. "
             "Right column: finv − Illion (pp); orange = finv wider, blue = Illion wider.",
             fontsize=9, color=INK_3, ha="center")
    fig.text(0.5, 0.936,
             "* = top 3 |diff| in segment · bold rows = key categories · "
             f"diff color saturates at ±3 pp · Source: {input_name} · 36 categories",
             fontsize=8.5, color=INK_3, ha="center")

    heights = [len(g["rows"]) for g in groups]
    # 五行网格：4 面板 + 底部色标/图例行；hspace 0.35 拉开板块间距（小面板也清晰分隔）
    gs = fig.add_gridspec(len(groups) + 1, 1, height_ratios=heights + [0.9], hspace=0.35)

    for gi, g in enumerate(groups):
        rows = g["rows"]
        n = len(rows)
        # 每面板拆两个子区：左 = 覆盖率 2 列（log 色标），右 = diff 1 列（发散色标）
        sub = gs[gi].subgridspec(1, 2, width_ratios=[2, 1], wspace=0.04)
        ax_m = fig.add_subplot(sub[0])
        ax_d = fig.add_subplot(sub[1])

        main_data = np.array([[r["_il"], r["_fv"]] for r in rows])
        diff_data = np.array([[r["_diff"]] for r in rows])

        # 覆盖率列：格子浅金底 + 数据条（条长线性 ∝ 覆盖率，illion = 蓝 / finv = 橙）+ 条端数值
        ax_m.imshow(np.full((n, 2), 0.5), cmap=ListedColormap([PANEL_BG]), aspect="auto", vmin=0, vmax=1)
        ax_m.axvline(1.0, color="#E8E0CE", lw=0.8, zorder=1)  # 两列间分隔线
        for i in range(n):
            for j in range(2):
                v = main_data[i, j]
                if v > 0:
                    bw = min(v / BAR_MAX, 1.0) * 0.94  # 条长占格宽比例（左右留边）
                    ax_m.barh(i, bw, left=j + 0.03, height=0.62,
                              color=BAR_COLOR_ILLION if j == 0 else BAR_COLOR_FINV, alpha=0.9, zorder=2)
                    if bw >= 0.45:
                        # 条内白字（条够长时）；否则条右端外侧深字
                        ax_m.text(j + 0.03 + bw / 2, i, f"{v:.2f}", ha="center", va="center",
                                  fontsize=9.5, color="#FFFFFF", zorder=3)
                    else:
                        ax_m.text(j + 0.03 + bw + 0.02, i, f"{v:.2f}", ha="left", va="center",
                                  fontsize=9.5, color=TEXT_DARK, zorder=3)
                else:
                    ax_m.text(j + 0.03, i, "0.00", ha="left", va="center", fontsize=9.5,
                              color=TEXT_DARK, zorder=3)
        ax_m.set_xlim(-0.03, 2.03)
        ax_m.set_ylim(n - 0.5, -0.5)

        # diff 列：发散色块（蓝 = Illion 更广 / 橙 = finv 更广）+ 数值（|diff| ≥ 1 加粗）
        ax_d.imshow(diff_data, cmap=cmap_diff, vmin=-diff_vmax, vmax=diff_vmax, aspect="auto")
        for i in range(n):
            d = diff_data[i, 0]
            txt_color = "#FFFFFF" if abs(d) >= diff_vmax * 0.55 else TEXT_DARK
            ax_d.text(0, i, f"{d:+.2f}", ha="center", va="center", fontsize=10, color=txt_color,
                      fontweight="bold" if abs(d) >= 1.0 else "normal")

        # 列标题与行标签
        top3 = {r["category"] for r in
                sorted((r for r in rows if abs(r["_diff"]) >= 0.5), key=lambda r: -abs(r["_diff"]))[:TOP_N_STAR]}
        ax_m.set_xticks([0, 1])
        ax_m.set_xticklabels(["illion %", "finv %"], fontsize=10, color=INK_2)
        ax_d.set_xticks([0])
        ax_d.set_xticklabels(["diff (pp)"], fontsize=10, color=INK_2)
        ax_m.set_yticks(range(n))
        ax_m.set_yticklabels([f"* {r['category']}" if r["category"] in top3 else r["category"]
                              for r in rows], fontsize=9, color=INK)
        for label, r in zip(ax_m.get_yticklabels(), rows):
            if r["category"] in HIGHLIGHT_CATS:
                label.set_fontweight("bold")
        ax_d.set_yticks([])

        # 面板浅灰边框：每个板块成为独立卡片，板块间清晰区分
        for spine in ax_m.spines.values():
            spine.set_visible(True)
            spine.set_color("#E2DCCF")
            spine.set_linewidth(0.8)
        for spine in ax_d.spines.values():
            spine.set_visible(True)
            spine.set_color("#E2DCCF")
            spine.set_linewidth(0.8)
        if gi < len(groups) - 1:
            ax_m.set_xticklabels([])
            ax_d.set_xticklabels([])

        gname = GROUP_EN.get(g["name"], g["name"])
        ax_m.set_title(f"{gname} · {n} categories", fontsize=12, color=INK, loc="left", pad=6)

    # 底部行：左 = 覆盖率线性比例尺（数据条长度对照 0–30%），右 = diff 方向图例
    sub_b = gs[len(groups)].subgridspec(1, 2, width_ratios=[0.72, 0.28], wspace=0.05)
    ax_cbar = fig.add_subplot(sub_b[0])
    ax_cbar.barh([0], [BAR_MAX], height=0.55, color=SCALE_BAR_COLOR, alpha=0.85)
    ax_cbar.set_xlim(0, BAR_MAX)
    ax_cbar.set_ylim(-0.5, 0.5)
    ax_cbar.set_yticks([])
    ax_cbar.set_xticks([0, 5, 10, 15, 20, 25, 30])
    ax_cbar.set_xticklabels(["0%", "5%", "10%", "15%", "20%", "25%", "30%"], fontsize=8.5, color=INK_3)
    ax_cbar.tick_params(length=2, colors=INK_3)
    for spine in ax_cbar.spines.values():
        spine.set_visible(False)
    ax_cbar.set_title("coverage bar length", fontsize=8.5, color=INK_3, loc="left", pad=2)
    ax_leg = fig.add_subplot(sub_b[1])
    ax_leg.axis("off")
    ax_leg.legend(handles=[Patch(facecolor="#2a78d6", alpha=0.85, label="Illion wider"),
                           Patch(facecolor="#eb6834", alpha=0.85, label="finv wider")],
                  loc="center left", frameon=False, fontsize=9)

    # 嵌套 subgridspec 会触发 tight_layout 的保守警告（布局实际正确应用），忽略
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        plt.tight_layout(rect=[0, 0.008, 1, 0.92])
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300, facecolor="#FFFFFF")
    plt.close(fig)
    print(f"Generated: {path}")


# 风格 A：精致商务 —— 低饱和深色系 + 细边框
# 风格 B：清爽浅色 —— 无边框亮色 + 数字标签统一深灰
STYLES: Dict[str, Dict[str, Any]] = {
    "a": dict(
        palette={"red": ("#C00000", "#8B0000"), "amber": ("#C98200", "#A45D00"),
                 "green": ("#2e7d32", "#1B5E20")},
        bar_alpha=0.88, bar_width=0.66, bar_edge_lw=0.4,
        label_size=9, label_bold=True, label_pad=3.0, label_ink=None,
        title="Category Agreement Rate · Faceted by Business Segment",
        subtitle="Bar length = agreement rate (intersection / union); panel Avg = transaction-weighted "
                 "segment agreement rate, same as the report table; dashed lines at 50% / 80%; "
                 "dark red <50%, amber 50%–80%, deep green ≥80%",
        hspace=0.42,
        grid_color="#EDEDED", ref_color="#BDBDBD", ref_lw=0.8,
        sep_color="#F0F0F0", sep_on=True,
    ),
    "b": dict(
        # 哑光三色（dataviz validator 全 PASS）：砖红 #C9403A / 暖金 #D98A24 / 灰绿 #4C9460
        palette={"red": ("#C9403A", "#C9403A"), "amber": ("#D98A24", "#D98A24"),
                 "green": ("#4C9460", "#4C9460")},
        bar_alpha=0.90, bar_width=0.62, bar_edge_lw=0.0,
        label_size=9, label_bold=True, label_pad=3.0, label_ink="#3c3c3c",
        title="Agreement Rate by Business Segment",
        subtitle="Bar length = agreement rate (intersection / union); panel Avg = transaction-weighted "
                 "segment agreement rate, same as the report table; dashed lines at 50% / 80%",
        hspace=0.38,
        grid_color="#F2F2F2", ref_color="#C9C9C9", ref_lw=0.7,
        sep_color="#F0F0F0", sep_on=False,
    ),
}

# 板块名英文化
GROUP_EN: Dict[str, str] = {"收入类": "Income", "支出类": "Expenses", "转账类": "Transfers", "负债类": "Liabilities"}

REF_LINE = "#BDBDBD"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#898781"


def _zone_fill_edge(share: float, palette: Dict[str, Tuple[str, str]]) -> Tuple[str, str]:
    if share < 0.50:
        return palette["red"]
    if share < 0.80:
        return palette["amber"]
    return palette["green"]


def render_dotplot(path: Path, items: List[Dict[str, Any]], style_key: str,
                   segment_avg: Dict[str, float] | None = None,
                   overall_avg: float | None = None,
                   input_name: str = paths.REVIEW_REPORT_NAME) -> None:
    """图 2.2：分类别一致率分面水平条形图（按业务板块分面，区块内一致率降序）。"""

    # matplotlib / seaborn / pandas 延迟导入，理由同上。
    import matplotlib
    matplotlib.use("Agg")  # 无 GUI 后端
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns
    from matplotlib import font_manager
    for fname in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"):
        if Path(fname).exists():
            try:
                font_manager.fontManager.addfont(fname)
            except Exception:
                pass
    # 顺序关键：seaborn set_theme 会重置 font.sans-serif，必须先调用再覆写
    sns.set_theme(style="white", rc={"axes.facecolor": "#FFFFFF", "figure.facecolor": "#FFFFFF"})
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False

    st = STYLES[style_key]
    palette = st["palette"]

    groups: List[Dict[str, Any]] = []
    for group in GROUP_ORDER:
        rows = [it for it in items if it["group"] == group]
        for it in rows:
            it["_share"] = to_float(it.get("交集占比（并集）"))
        rows.sort(key=lambda r: -r["_share"])
        # 面板 Avg 默认与 2.2 表同口径（交易加权板块一致率）；未传入时回退类别等权
        avg = segment_avg.get(group) if segment_avg else None
        if avg is None:
            avg = sum(r["_share"] for r in rows) / len(rows) if rows else 0
        groups.append({"name": group, "count": len(rows), "avg": avg, "rows": rows})

    red_n = sum(1 for g in groups for r in g["rows"] if r["_share"] < 0.50)
    amber_n = sum(1 for g in groups for r in g["rows"] if 0.50 <= r["_share"] < 0.80)
    green_n = sum(1 for g in groups for r in g["rows"] if r["_share"] >= 0.80)
    # 整体 Avg 同口径：交易加权（覆盖调整后一致率），未传入时回退类别等权
    if overall_avg is None:
        total_avg = sum(g["avg"] * g["count"] for g in groups) / len(items)
    else:
        total_avg = overall_avg

    # ---- 画布 figsize=(11, 13)，纵向 4 分面 + 底部说明 ----
    fig = plt.figure(figsize=(11, 13))
    fig.suptitle(st["title"], fontsize=15, fontweight="bold", color=INK, x=0.5, y=0.978)
    fig.text(0.5, 0.953, st["subtitle"], fontsize=9, color=INK_3, ha="center")

    heights = [g["count"] for g in groups]
    # hspace 不能放 GridSpec 上（matplotlib 3.11 会令 tight_layout 失效），tight_layout 后单独设置
    gs = fig.add_gridspec(len(groups), 1, height_ratios=heights)

    xmax = max(max(r["_share"] * 100 for r in g["rows"]) for g in groups) + 7  # 右缘预留标签空间

    for gi, g in enumerate(groups):
        ax = fig.add_subplot(gs[gi])
        rows = g["rows"]
        n = len(rows)
        names = [r["category"] for r in rows]
        pcts = [r["_share"] * 100 for r in rows]
        fills = [_zone_fill_edge(r["_share"], palette)[0] for r in rows]
        edges = [_zone_fill_edge(r["_share"], palette)[1] for r in rows]

        df = pd.DataFrame({"category": names, "pct": pcts})
        sns.barplot(data=df, x="pct", y="category", orient="h", ax=ax,
                    color="#DDDDDD", width=st["bar_width"], legend=False)
        # 逐条重着色：填充 + 边框（透明度/线宽按风格）
        for patch, fill, edge in zip(ax.patches, fills, edges):
            patch.set_facecolor(fill)
            patch.set_edgecolor(edge)
            patch.set_alpha(st["bar_alpha"])
            patch.set_linewidth(st["bar_edge_lw"])
        # 条形右侧紧贴标注：仅一致率百分比
        for patch, edge, r in zip(ax.patches, edges, rows):
            ax.text(patch.get_width() + st["label_pad"], patch.get_y() + patch.get_height() / 2,
                    f"{r['_share'] * 100:.1f}%", fontsize=st["label_size"],
                    fontweight="bold" if st["label_bold"] else "normal",
                    color=st["label_ink"] or edge, va="center")

        # 参考线（50% / 80% 浅灰虚线，zorder 低于条形）
        ax.axvline(50, color=st["ref_color"], ls="--", lw=st["ref_lw"], zorder=1)
        ax.axvline(80, color=st["ref_color"], ls="--", lw=st["ref_lw"], zorder=1)
        # 仅 X 轴主网格浅灰细线；面板纯白
        ax.grid(axis="x", color=st["grid_color"], lw=0.5, zorder=0)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(st["grid_color"])
        ax.set_xlim(0, xmax)  # 0–100% 刻度；右缘容纳贴条标签
        ax.set_ylim(n - 0.62, -0.38)
        ax.set_xticks([0, 20, 40, 60, 80, 100])
        ax.tick_params(axis="x", labelsize=9, colors=INK_3)
        ax.tick_params(axis="y", length=0, labelsize=8, colors=INK)
        if gi < len(groups) - 1:
            ax.set_xticklabels([])
        # 分面标题：板块名称 + 板块一致率（交易加权，与 2.2 表同口径）
        gname = GROUP_EN.get(g["name"], g["name"])
        ax.set_title(f"{gname} · Avg {g['avg'] * 100:.1f}%", fontsize=12, color=INK,
                     loc="left", pad=6)
        if gi == len(groups) - 1:
            ax.set_xlabel("Agreement rate (intersection / union)", fontsize=10, color=INK_2, labelpad=3)

    # 分面间浅灰分隔线（画在下方分面的上缘内侧，ylim 上界为 n-0.62）
    if st["sep_on"]:
        for gi, g in enumerate(groups):
            if gi == 0:
                continue
            ax = fig.add_subplot(gs[gi])
            ax.axhline(y=len(g["rows"]) - 0.60, xmin=0, xmax=1, color=st["sep_color"], lw=0.5, zorder=3)

    fig.text(0.5, 0.012, f"Source: {input_name} · 36 categories · "
                         f"red {red_n} / amber {amber_n} / green {green_n} · avg agreement {total_avg * 100:.1f}%",
             fontsize=8.5, color=INK_3, ha="center")

    plt.tight_layout(rect=[0, 0.02, 1, 0.94])
    fig.subplots_adjust(hspace=st["hspace"])  # 分面间距（tight_layout 之后再设，避免被覆盖）
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300, facecolor="#FFFFFF")
    plt.close(fig)
    print(f"Generated: {path}  [{style_key}]")
    print(f"  Zones: red {red_n} / amber {amber_n} / green {green_n}; avg {total_avg * 100:.1f}%")


# ===========================================================================
# 十一、主流程
# ===========================================================================

def render_charts(an: MdAnalysisFull, xlsx: Path, out_dir: Path,
                  enabled: bool) -> Tuple[Optional[Path], Optional[Path]]:
    """渲染图2.1（覆盖率）与图2.2（一致率）；失败只告警，不阻断报告生成。

    两个图函数内联在本文件「十、图表渲染」；它们各自延迟导入 matplotlib /
    seaborn，缺依赖时这里捕获异常、报告降级为不含该图。

    图表与 md 放在同一目录，正文里用相对路径引用，整个目录可直接搬走。
    """
    if not enabled:
        return None, None
    out_dir.mkdir(parents=True, exist_ok=True)

    coverage_png: Optional[Path] = out_dir / "md_chart_2_1_coverage.png"
    try:
        render_coverage(coverage_png, an.categories, input_name=xlsx.name)
    except Exception as exc:
        print(f"[warn] Chart 2.1 (coverage) failed, report will omit it: {exc}")
        coverage_png = None

    dotplot_png: Optional[Path] = out_dir / "md_chart_2_2_dotplot.png"
    try:
        segment_avg = {g: an.segment_coverage(g)["exact_rate"] for g in GROUP_ORDER}
        overall_avg = to_float(an.metrics.get("覆盖调整后一致率", {}).get("result"))
        render_dotplot(dotplot_png, an.categories, "b", segment_avg, overall_avg,
                       input_name=xlsx.name)
    except Exception as exc:
        print(f"[warn] Chart 2.2 (agreement) failed, report will omit it: {exc}")
        dotplot_png = None

    return dotplot_png, coverage_png


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate the Markdown BS-CAT category difference report "
                    "(English, self-contained)"
    )
    ap.add_argument("--input", default=str(paths.default_review_report()),
                    help="path to the workbook (.xlsx)")
    ap.add_argument("--output", default=str(paths.report_output_dir() / DEFAULT_OUTPUT_NAME),
                    help="path to the output .md")
    ap.add_argument("--samples-per-category", type=int, default=5,
                    help="samples kept per category")
    ap.add_argument("--check", action="store_true",
                    help="run the reconciliations only, write nothing")
    ap.add_argument("--no-charts", action="store_true", help="skip chart rendering")
    args = ap.parse_args()

    xlsx = Path(args.input)
    if not xlsx.exists():
        sys.exit(f"Workbook not found: {xlsx}")
    print(f"Reading workbook: {xlsx} ({xlsx.stat().st_size / 1024 / 1024:.0f} MB) ...",
          flush=True)

    wb = load_workbook(xlsx, read_only=True, data_only=True)
    try:
        metrics = read_metrics(wb[SHEET_00])
        categories = read_categories(wb[SHEET_00])
        top_flows = read_top_flows(wb[SHEET_01])
        matrix = read_matrix(wb[SHEET_01])
        generated_date = read_generated_date(wb[SHEET_00])
    finally:
        wb.close()

    print(f"  {SHEET_00}: {len(categories)} categories, {len(metrics)} headline metrics")
    print(f"  {SHEET_01}: {len(top_flows)} top flows, {len(matrix)} matrix cells")

    detail_rows = read_row_count(xlsx, SHEET_03)
    print(f"Streaming {SHEET_03} samples ({detail_rows:,} rows in the sheet) ...", flush=True)
    samples = read_samples(xlsx, per_category=args.samples_per_category)
    with_samples = [c for c, v in samples.items() if v]
    print(f"  samples found for {len(with_samples)} categories: "
          f"{', '.join(sorted(with_samples))}")

    an = MdAnalysisFull(metrics, categories, top_flows, matrix, samples, generated_date,
                        detail_rows=detail_rows)

    print()
    print("=== Reconciliation ===")
    problems = an.reconcile()
    print(f"[1] matrix <-> {SHEET_00}: "
          f"{'all passed' if not problems else f'{len(problems)} failed'}")
    for p in problems:
        print("    [FAIL] " + p)

    problems_02 = an.reconcile_02(xlsx)
    print(f"[2] category matrix aggregated <-> {SHEET_02}: "
          f"{'all passed' if not problems_02 else f'{len(problems_02)} failed'}")
    for p in problems_02:
        print("    [FAIL] " + p)

    if problems or problems_02:
        sys.exit("\nReconciliation failed, no report written.")

    if args.check:
        print("\n--check: nothing was written.")
        return

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    print()
    print(f"Rendering charts ... ({'skipped' if args.no_charts else 'use --no-charts to skip'})")
    dotplot_png, coverage_png = render_charts(an, xlsx, out.parent, not args.no_charts)

    doc = MdDoc(out)
    doc.source_note = (
        f"Basis of preparation: every figure in this report comes from the {SHEET_00}, "
        f"{SHEET_01} and {SHEET_02} sheets. {SHEET_03} is used only to pick the "
        f"\u201ctypical transaction samples\u201d (the {args.samples_per_category} largest "
        f"rows per category); it is never used as a denominator or a count. Any share "
        f"described below as being of \u201cdifference samples\u201d therefore describes the "
        f"sample make-up, not the full distribution of differences."
    )

    # 正文逻辑与英文 docx 版同源：这里的 add_* / fmt_money / render_category_block_en
    # 就是上面定义的 Markdown 实现，build_report_en 里的裸名调用直接解析到它们。
    build_report_en(doc, an, dotplot_png, coverage_png,
                    input_name=xlsx.name, generated_date=generated_date)

    if out.exists():
        try:
            out.unlink()
        except PermissionError:
            sys.exit(f"Output file is locked and cannot be overwritten: {out} (close it first)")
    out.write_text(doc.render(), encoding="utf-8", newline="\n")
    print(f"\nWritten: {out} ({out.stat().st_size / 1024:.0f} KB, "
          f"{len(doc.headings)} headings)")


if __name__ == "__main__":
    main()
