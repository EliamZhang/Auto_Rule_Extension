# -*- coding: utf-8 -*-
"""从数据底稿生成 Markdown 版 BS-CAT 类别差异报告。

用法
----
    python scripts/generate_md_category_report.py
    python scripts/generate_md_category_report.py --input input/xxx.xlsx --output output/xxx.md
    python scripts/generate_md_category_report.py --check      # 只做口径校验，不写文件

本文件是**自包含的单文件生成器**：不导入、不修改 `generate_docx_category_report.py`，
也不需要 python-docx。正文文案（章节结构、叙述段落、表格布局）与两张图的渲染代码
都是从原脚本逐字复制过来的，因此两版报告的形态天然一致；代价是此后各自演进，
docx 版或图脚本改了，这里不会自动跟随。

运行时依赖只有 openpyxl（读底稿）与 matplotlib / numpy / seaborn / pandas（画图，
「十、图表渲染」内延迟导入）。缺画图依赖时只丢图、报告照常生成，`--no-charts`
可完全绕开。

设计原则
--------
1. **报告形态对齐 docx 报告**：章节编号、叙述段落、表格布局与 docx 版一致。
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
   不存在，一律显示为「—」而非 0。详见 `fmt_money`。
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

DEFAULT_OUTPUT_NAME = "category_difference_report_v2.md"

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
        lines = ["## 目录", ""]
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
# 八、渲染层：函数名与签名对齐 docx 版，输出改为 Markdown
#
# 「九、正文文案」里的裸名调用（add_heading / add_para / add_table / fmt_money /
# render_category_block …）直接解析到这里，不需要任何打补丁或运行时替换。
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
    # 封面主标题（居中且字号 ≥11）→ 一级标题；「数据来源」等 12pt 加粗行 → 二级标题
    if align == "center" and size >= 11:
        doc.title_line(text)
        return
    if bold and size >= 12:
        doc.heading(text, 2)
        return
    doc.para(text, bold=bold)
    # 「数据来源」之后立刻交代 03 的用途，避免把样本条数当成统计口径
    if doc.source_note and text.startswith("Excel 底稿："):
        doc.para(doc.source_note)


def add_para_labeled(doc: MdDoc, label: str, text: str, size: float = 9.5,
                     space_after: float = 3) -> None:
    # 「交易方向与金额」的合计只可能覆盖底稿给出金额的流向，补一句限定说明
    note = doc.amount_note
    if note and label == "交易方向与金额" and text:
        text = text[:-1] + note + "。" if text.endswith("。") else text + note
    doc.para(f"**{label}：**{text}" if text else f"**{label}：**")


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


def add_flow_table(doc: MdDoc, flows: Sequence[Tuple[str, str, str, int, float]],
                   title: str = "", denom: float = 0,
                   share_label: str = "占该类差异") -> None:
    if title:
        doc.para(title, bold=True)
    # 底稿未给出金额的流向由 fmt_money 渲染成「—」：0 是「未知」，不是「金额为零」
    rows = [
        [index, il or "（空）", fv or "（空）", diff_type_label(diff_type),
         fmt_pct(count / denom if denom else 0), fmt_money(amount)]
        for index, (il, fv, diff_type, count, amount) in enumerate(flows, 1)
    ]
    doc.table(["排名", "Illion 类别", "finv 类别", "差异类型", share_label, "差异金额"],
              rows, ["c", "l", "l", "l", "c", "r"])


def add_samples_table(doc: MdDoc, samples: Sequence[Dict[str, Any]]) -> None:
    rows = []
    for s in samples:
        counterparty = (s.get("counterparty", "") or "").strip()
        rows.append([
            s.get("transaction_date", ""),
            s.get("text", "") or "",
            "" if counterparty == "-" else (counterparty or "（空）"),
            s.get("dr_cr", ""),
            _fmt_money_exact(s.get("amount")),
            f"{s.get('illion_category') or '（空）'} → {s.get('finv_category') or '（空）'}",
        ])
    doc.table(["日期", "交易描述", "对手方", "方向", "金额", "Illion → finv"],
              rows, ["c", "l", "l", "c", "r", "c"])


def add_boundary_bullets(doc: MdDoc, pairs: Sequence[Tuple[str, str]], analysis: Any) -> None:
    for a, b in pairs:
        flows = analysis.flow_between(a, b)
        if not flows:
            continue
        if len(flows) == 1:
            il, fv, _dt, _cnt, amount = flows[0]
            doc.para(f"{a} 与 {b}：{il or '（空）'} → {fv or '（空）'} 差异金额 {fmt_money(amount)}。")
            continue
        # 双向混淆：分别交代每个方向的金额，缺金额的方向单独说明而不是填 0
        known = [f for f in flows if _amount_known(f[4])]
        if not known:
            doc.para(f"{a} 与 {b}：双向混淆（两个方向的金额在底稿中均未给出）。")
        elif len(known) == len(flows):
            amounts = " 和 ".join(fmt_money(f[4]) for f in sorted(flows, key=lambda x: -x[4]))
            doc.para(f"{a} 与 {b}：双向混淆，金额分别为 {amounts}。")
        else:
            amounts = " 和 ".join(fmt_money(f[4]) for f in sorted(known, key=lambda x: -x[4]))
            doc.para(f"{a} 与 {b}：双向混淆，其中底稿给出金额的 {len(known)} 个方向分别为 {amounts}，"
                     f"其余 {len(flows) - len(known)} 个方向金额缺失。")


def set_run_font_only(run: Any) -> None:
    """docx 里用于统一字体；Markdown 无字体概念，忽略。"""


def _amount_note(analysis: Any, category: str) -> str:
    """「合计差异金额」的覆盖范围提示：底稿只给出 Top 20 流向的金额。"""
    pairs = [(il, fv) for (il, fv, _dt) in analysis.flow_stats if category in (il, fv)]
    missing = sum(1 for p in pairs if not analysis.flow_amount.get(p))
    if not missing:
        return ""
    return (f"，其中底稿仅给出 {len(pairs) - missing} 条流向的金额、"
            f"其余 {missing} 条流向的金额在底稿中缺失")


# --- python-docx 的取值占位 -------------------------------------------------
# 「九、正文文案」的图表插入段里有 Pt(2) / Cm(15.5) / WD_ALIGN_PARAGRAPH.CENTER
# 三处 python-docx 取值。Markdown 没有字号、列宽、对齐的概念，这些值最终都被
# MdDoc 的图片占位对象丢掉（paragraph_format 是 _NullFormat，add_picture 忽略
# width，alignment 只是被赋值到 _MdPictureParagraph 上），故这里只需名字存在。
def Pt(value: Any) -> float:  # 字号
    return float(value)


def Cm(value: Any) -> float:  # 列宽
    return float(value)


class _WdAlignEnum:
    LEFT = CENTER = RIGHT = JUSTIFY = None


WD_ALIGN_PARAGRAPH = _WdAlignEnum()


# ===========================================================================
# 九、正文文案（与 docx 版同源，逐字保留）
# ===========================================================================

def category_narrative(item: Dict[str, Any], total_transactions: float = 0) -> str:
    """固定格式的自动指标解读（第三部分统一口径）。"""
    category = item["category"]
    illion_cov = item.get("illion覆盖率")
    finv_cov = item.get("finv覆盖率")
    union = to_float(item.get("并集数量"))
    inter = to_float(item.get("交集数量"))
    inter_share = item.get("交集占比（并集）")
    illion_only_share = item.get("illion独有占比（并集）")
    finv_only_share = item.get("finv独有占比（并集）")
    union_share = union / total_transactions if total_transactions else 0
    inter_share_txt = inter / total_transactions if total_transactions else 0
    return (
        f"Illion 覆盖率为 {fmt_pct(illion_cov)}，finv 覆盖率为 {fmt_pct(finv_cov)}；"
        f"并集占全部交易 {fmt_pct(union_share)}，交集占全部交易 {fmt_pct(inter_share_txt)}，"
        f"一致率（类别并集）为 {fmt_pct(inter_share)}。"
        f"Illion 独有占比为 {fmt_pct(illion_only_share)}，finv 独有占比为 {fmt_pct(finv_only_share)}。"
    )


def category_judgment(item: Dict[str, Any]) -> str:
    """根据两侧独有占比判断差异主导来源。"""
    illion_only_share = to_float(item.get("illion独有占比（并集）"))
    finv_only_share = to_float(item.get("finv独有占比（并集）"))
    if finv_only_share > illion_only_share + 0.05:
        return (
            f"差异判断：该类别以 finv 扩展识别为主（finv 独有占比 {fmt_pct(finv_only_share)} 高于 Illion 独有占比 "
            f"{fmt_pct(illion_only_share)}），finv 覆盖更广，需要进一步核验新增分类的合理性，区分覆盖扩展与归类分歧。"
        )
    if illion_only_share > finv_only_share + 0.05:
        return (
            f"差异判断：该类别以 Illion 独有为主（Illion 独有占比 {fmt_pct(illion_only_share)} 高于 finv 独有占比 "
            f"{fmt_pct(finv_only_share)}），提示差异可能来自 finv 侧未识别、Illion 侧扩展识别或归类迁移，"
            f"建议检查文本清洗、商户覆盖和兜底规则。"
        )
    return (
        f"差异判断：两侧独有占比较为接近（Illion 独有 {fmt_pct(illion_only_share)}，finv 独有 {fmt_pct(finv_only_share)}），"
        f"差异更可能来自双方分类边界或规则口径，建议按差异流向逐条核对规则优先级。"
    )


def category_conclusion(item: Dict[str, Any], total_transactions: float = 0) -> str:
    union = to_float(item.get("并集数量"))
    inter_share = to_float(item.get("交集占比（并集）"))
    if inter_share >= 0.9:
        stability = "交集占比较高，双方对该类别的共同识别稳定"
    elif inter_share >= 0.7:
        stability = "双方存在较好的共同覆盖，但仍有一定单边差异"
    else:
        stability = "共同覆盖偏低，需要重点检查分类边界、知识库或单侧覆盖差异"
    union_share = union / total_transactions if total_transactions else 0
    if union_share >= 0.005:
        volume = f"并集占全部交易 {fmt_pct(union_share)}，规模较大，对总体差异有较高影响"
    elif union_share < 0.001:
        volume = f"并集占全部交易 {fmt_pct(union_share)}，规模较小，比例指标需结合样本量谨慎解读"
    else:
        volume = f"并集占全部交易 {fmt_pct(union_share)}，规模处于中等水平"
    priority = str(item.get("建议优先级") or "").strip() or "-"
    return f"类别结论：{stability}；{volume}。建议优先级为 {priority}，可结合 Top 差异流向核对规则与知识库。"


def category_headline(item: Dict[str, Any], analysis: Analysis) -> str:
    """类别结论（结论先行）：一致率 + 核心矛盾 + 主要流向 + 样本佐证，全部动态生成。"""
    category = item["category"]
    # 转账两类走内外判定口径专项文案；ET→IT 分歧流存在时才启用，否则回落通用逻辑
    if category in ("External Transfers", "Internal Transfer") \
            and _transfer_pair_stats(analysis)["count"] > 0:
        return transfer_category_headline(analysis, item)
    inter_share = to_float(item.get("交集占比（并集）"))
    illion_only_share = to_float(item.get("illion独有占比（并集）"))
    finv_only_share = to_float(item.get("finv独有占比（并集）"))
    if inter_share < 0.5:
        rate_txt = f"一致率仅 {fmt_pct(inter_share)}"
    elif inter_share >= 0.9:
        rate_txt = f"一致率 {fmt_pct(inter_share)}，共识较强"
    else:
        rate_txt = f"一致率 {fmt_pct(inter_share)}"
    flows = analysis.category_flows(category, limit=1)
    flow_txt = ""
    if flows:
        il, fv, dt, cnt, amount = flows[0]
        flow_txt = (f"差异主要集中在 {il or '（空）'} → {fv or '（空）'} 流向"
                    f"（占全部差异的 {fmt_pct(cnt / analysis.diff_total if analysis.diff_total else 0)}、"
                    f"金额 {fmt_money(amount)}）")
    # 样本佐证：取主流向内的明细（按金额绝对值降序），保证样本与结论方向一致
    samples = analysis.top_samples_in_flow(category, (flows[0][0], flows[0][1]), limit=1) if flows else []
    sample_txt = ""
    if samples:
        s = samples[0]
        sample_txt = (f"典型样本「{s.get('text') or ''}」两侧分类为 "
                      f"{s['illion_category'] or '（空）'} → {s['finv_category'] or '（空）'}（{fmt_money(s.get('amount'))}），"
                      f"与上述判断一致")
    if inter_share >= 0.9:
        text = f"{category}的{rate_txt}"
    else:
        if finv_only_share > illion_only_share + 0.05:
            core = f"核心矛盾是 finv 对「{category}」识别范围更广"
        elif illion_only_share > finv_only_share + 0.05:
            core = f"核心矛盾是 Illion 对「{category}」识别范围更广"
        else:
            core = f"核心矛盾是两侧对「{category}」的分类边界不一致"
        text = f"{category}的{rate_txt}，{core}"
    # 主句与流向/样本之间以「，/。」衔接；流向或样本缺失时不产生空段与多余标点
    text += f"，{flow_txt}" if flow_txt else ""
    text += f"。{sample_txt}" if sample_txt else ""
    return text + "。"


def segment_headline(analysis: Analysis, group: str, items: List[Dict[str, Any]]) -> str:
    """板块总体结论（结论先行）：主要差异类别 + 主导侧 + 板块外边界 + 共识强类别。"""
    # 转账类走内外判定口径专项文案（ET→IT 分歧流存在时才启用，否则回落通用逻辑）
    if group == "转账类" and _transfer_pair_stats(analysis)["count"] > 0:
        return transfer_segment_headline(analysis, items)
    cat_diff = sorted(
        ((c["category"], len(analysis.category_details.get(c["category"], []))) for c in items),
        key=lambda x: -x[1])
    main_cats = [name for name, n in cat_diff if n > 0]
    if not main_cats:
        return f"{group}未发现差异明细，两侧识别结果一致，风险较低。"
    top_names = " 和 ".join(main_cats[:2])
    ws = analysis.segment_summary(group)
    if ws["finv_only"] > ws["illion_only"]:
        side_txt = f"核心差异来自 finv 对{group}识别范围更广"
    elif ws["illion_only"] > ws["finv_only"]:
        side_txt = f"核心差异来自 Illion 对{group}识别范围更广"
    else:
        side_txt = "两侧单边识别量接近"
    # 板块外边界：板块内类别与板块外类别之间差异量最大的流向
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
            boundary_txt = f"，与 {other} 边界模糊，部分转账的归类在两侧存在分歧"
        else:
            boundary_txt = f"，与 {other} 边界模糊"
    best = max(items, key=lambda c: to_float(c.get("交集占比（并集）")), default=None)
    strong_txt = ""
    if best is not None and to_float(best.get("交集占比（并集）")) >= 0.9:
        strong_txt = f"；{best['category']} 共识较强（{fmt_pct(best.get('交集占比（并集）'))}），风险较低"
    return f"{group}差异主要集中在 {top_names}，{side_txt}{boundary_txt}{strong_txt}。"


def side_only_leader(analysis: Analysis, group: str, side: str) -> str:
    """板块内单边识别最多的类别（side 侧有分类、另一侧为空）。"""
    best, best_n = "", 0
    for (il, fv, dt), stat in analysis.flow_stats.items():
        il_g = GROUP_OF.get(il) if il else ""
        fv_g = GROUP_OF.get(fv) if fv else ""
        if side == "finv" and il == "" and fv_g == group and stat["count"] > best_n:
            best, best_n = fv, stat["count"]
        elif side == "illion" and fv == "" and il_g == group and stat["count"] > best_n:
            best, best_n = il, stat["count"]
    return best


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


def transfer_category_headline(analysis: Analysis, item: Dict[str, Any]) -> str:
    """External Transfers / Internal Transfer 类别结论（结论先行，替代通用"识别范围更广"表述）。

    同一批资金划转两侧判定相反：Illion 按交易方/描述判为外部转账，
    finv 通过同一客户多张卡/账户之间的交易关联判为同一银行内部的转账——
    差异主因是内外转账的判定口径分歧，而非单纯的覆盖扩展/缺口。
    数字全动态取自底稿；典型样本按主流向（ET→IT）金额 Top 选取。
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
    rate_txt = (f"一致率仅 {fmt_pct(rate)}" if rate < 0.7
                else f"一致率 {fmt_pct(rate)}{'，共识较强' if rate >= 0.8 else ''}")
    if category == et:
        head = (f"{et} 的{rate_txt}，核心矛盾是内外转账的判定口径分歧："
                f"{fmt_num(st['count'])} 笔资金划转（金额 {fmt_money(st['amount'])}，"
                f"占 {et} 相关差异的 {fmt_pct(share_cat)}、全部差异的 {fmt_pct(share_all)}）"
                f"在 Illion 侧判为外部转账，finv 则通过同一客户多张卡/账户之间的交易关联，"
                f"将同一批划转识别为同一银行内部的转账")
        if st["et_only"]:
            head += f"；叠加 Illion 单边识别（仅 Illion 有值）{fmt_num(st['et_only'])} 笔"
        head += (f"，整体表现为 Illion 对 {et} 的识别范围更广"
                 f"（覆盖率 {fmt_pct(il_cov)} vs {fmt_pct(fv_cov)}），"
                 f"其构成以 finv 的内部划转归并与 Illion 单边识别为主，而非单纯的覆盖扩展")
    else:
        il_only = to_float(item.get("illion独有占比（并集）"))
        fv_only = to_float(item.get("finv独有占比（并集）"))
        head = (f"{it} 的{rate_txt}，核心矛盾是两侧对内部转账的判定方式不同："
                f"finv 通过多卡/多账户交易关联识别同行体系内的划转"
                f"（{et} → {it} 共 {fmt_num(st['count'])} 笔、金额 {fmt_money(st['amount'])}，"
                f"占 {it} 相关差异的 {fmt_pct(share_cat)}、全部差异的 {fmt_pct(share_all)}），"
                f"而 Illion 将同一批交易判为外部转账；finv 独有占比（{fmt_pct(fv_only)}）"
                f"高于 Illion（{fmt_pct(il_only)}），表现为 finv 覆盖略广"
                f"（覆盖率 {fmt_pct(fv_cov)} vs {fmt_pct(il_cov)}），"
                f"反映 finv 在多卡关联维度的识别范围延伸，而非 Illion 侧漏识别")
    head += "。"
    samples = analysis.top_samples_in_flow(et, (et, it), limit=1)
    if samples:
        s = samples[0]
        head += (f"典型样本「{s.get('text') or ''}」两侧分类为 {et} → {it}"
                 f"（{fmt_money(s.get('amount'))}），与上述判断一致。")
    return head


def transfer_segment_headline(analysis: Analysis, items: List[Dict[str, Any]]) -> str:
    """转账类板块总述（结论先行，替代通用"核心差异来自某侧识别范围更广"表述）。

    板块主因 = 内外转账判定口径分歧（ET→IT 由 finv 多卡/账户关联判为对内、
    Illion 判为对外）；次因 = ET 单边识别与转账/收入类边界。数字全动态。
    """
    by_name = {item["category"]: item for item in items}
    et_item, it_item = by_name.get("External Transfers"), by_name.get("Internal Transfer")
    st = _transfer_pair_stats(analysis)
    et, it = st["et"], st["it"]
    diff_total = analysis.diff_total or 0
    share_all = st["count"] / diff_total if diff_total else 0
    parts = [
        f"转账类差异主要集中在 {et}（相关差异 {fmt_num(st['et_n'])} 笔，"
        f"占全部差异的 {fmt_pct(st['et_n'] / diff_total if diff_total else 0)}）"
        f"与 {it}（{fmt_num(st['it_n'])} 笔，"
        f"占 {fmt_pct(st['it_n'] / diff_total if diff_total else 0)}）",
        f"板块内最大的分歧是内外转账的判定口径：{et} → {it} 共 {fmt_num(st['count'])} 笔"
        f"（金额 {fmt_money(st['amount'])}，占全部差异的 {fmt_pct(share_all)}），"
        f"Illion 判为对外转账，finv 依据同一客户多张卡/账户之间的交易关联判为对内划转",
        f"其余差异以 {et} 单边识别（仅 Illion 有值 {fmt_num(st['et_only'])} 笔）"
        f"及与 All Other Credits、Wages 的收入类边界为主",
    ]
    if it_item is not None and to_float(it_item.get("交集占比（并集）")) >= 0.5:
        parts.append(f"{it} 自身一致率较高（{fmt_pct(it_item.get('交集占比（并集）'))}），"
                     f"差异主要来自上述归并")
    return "；".join(parts) + "。"


def transfer_summary_paragraphs(analysis: Analysis) -> List[str]:
    """转账板块差异分析总结（两段式，与 Rent / Gambling 专项同构，数字全动态）。

    段 1：两类别一致率/体量对比 + ET→IT 双向划转流向（全报告金额最大的单一分歧流向）；
    段 2：判定机制与样本依据（finv 多卡/账户关联识别内转 vs Illion 判外转）+ 分层治理建议。
    """
    et, it = "External Transfers", "Internal Transfer"
    et_item = analysis.category_by_name.get(et)
    it_item = analysis.category_by_name.get(it)
    if et_item is None or it_item is None:
        return ["底稿中缺少转账类别的指标数据，无法生成转账差异分析总结。"]
    st = _transfer_pair_stats(analysis)
    diff_total = analysis.diff_total or 0
    total = analysis.total_transactions or 0
    paras: List[str] = []

    # 段落 1：两类别一致率/体量对比与 ET→IT 双向流向
    et_union = to_float(et_item.get("并集数量"))
    it_union = to_float(it_item.get("并集数量"))
    cr_total = st["credit"] + st["debit"]
    max_flow_amt = max((s["amount"] for s in analysis.flow_stats.values()), default=0.0)
    largest_txt = ("，是转账板块乃至全报告金额最大的单一分歧流向"
                   if st["count"] and st["amount"] >= max_flow_amt
                   else "，是转账板块内金额最大的单一分歧流向")
    p1 = (f"转账板块内两个类别的一致率分化明显：{et} 按并集口径的一致率为 "
          f"{fmt_pct(et_item.get('交集占比（并集）'))}"
          f"（并集 {fmt_num(et_union)} 笔，占全部交易的 "
          f"{fmt_pct(et_union / total if total else 0)}），而 {it} 达到 "
          f"{fmt_pct(it_item.get('交集占比（并集）'))}"
          f"（并集 {fmt_num(it_union)} 笔，占 {fmt_pct(it_union / total if total else 0)}）。"
          f"两者相关差异分别占全部差异的 "
          f"{fmt_pct(st['et_n'] / diff_total if diff_total else 0)} 与 "
          f"{fmt_pct(st['it_n'] / diff_total if diff_total else 0)}，其中 "
          f"{et} → {it} 同一流向共 {fmt_num(st['count'])} 笔、金额 {fmt_money(st['amount'])}"
          f"{largest_txt}；该流向 credit 占 {fmt_pct(st['credit'] / cr_total if cr_total else 0)}、"
          f"debit 占 {fmt_pct(st['debit'] / cr_total if cr_total else 0)}，"
          f"呈双向划转形态，而非单向外付。")
    paras.append(p1)

    # 段落 2：判定机制、样本依据与分层治理建议
    samples = analysis.top_samples_in_flow(et, (et, it), limit=1)
    sample_txt = ""
    if samples:
        s = samples[0]
        sample_txt = (f"（如 {s.get('transaction_date')}「{s.get('text') or ''}」，"
                      f"{fmt_money(s.get('amount'))}）")
    p2 = (f"判定机制与建议：从差异样本看，{et} → {it} 流向中同账号等额配对的 "
          f"Internet Deposit/Withdrawal{sample_txt}、CommBank app 卡间 "
          f"Transfer to/from xx… 互转等特征明显——finv 通过同一客户多张卡/账户之间的交易关联，"
          f"将这类划转识别为同一银行内部的转账；Illion 缺少该关联信息时，"
          f"按交易对方/描述（Funds Transfer 等）判为外部转账，同一批交易因此两侧结论相反，"
          f"形成内外转账的判定口径分歧。建议："
          f"① 抽样核验 {et} → {it} 样本，确认关联的两张卡/账户确属同一客户，若成立可将 finv 的"
          f"关联识别结果作为内部转账的判定依据，或向 Illion 侧同步卡组关联线索以对齐内外转账口径")
    suggestions = []
    if st["et_only"]:
        suggestions.append(f"将 {et} 单边识别（仅 Illion 有值 {fmt_num(st['et_only'])} 笔）"
                           f"与内外判定分歧分开治理，单独核验其属于 Illion 扩展识别还是 finv 漏识别，"
                           f"避免两类问题相互干扰")
    suggestions.append("该流向体量与金额均为板块最大，建议按 P1 优先级优先治理")
    if suggestions:
        p2 += "；" + "；".join(f"② {s}" if i == 0 else f"③ {s}"
                               for i, s in enumerate(suggestions[:2]))
    p2 += "。"
    paras.append(p2)
    return paras


def rent_summary_paragraphs(analysis: Analysis, rent: Dict[str, Any]) -> List[str]:
    """Rent 专项差异分析总结（两段式文案按人工润色定稿，数字仍动态取自底稿，无硬编码）。

    段落 1：并集口径一致率与体量 → 交易方向 → 单边识别偏向；
    段落 2：相关差异占全部差异的比例 → 差异流向构成（转账类流入 / 单边识别）→ 主因判断与建议。
    流向语义与报告一致：'X → Rent' 表示 Illion 侧为 X、finv 侧为 Rent；
    '仅 finv 有值 → Rent' 表示 finv 单边识别为 Rent、Illion 无分类。
    """
    inter_share = to_float(rent.get("交集占比（并集）"))
    illion_only_share = to_float(rent.get("illion独有占比（并集）"))
    finv_only_share = to_float(rent.get("finv独有占比（并集）"))
    union = to_float(rent.get("并集数量"))
    total = analysis.total_transactions or 0
    diff_total = analysis.diff_total or 0

    # Rent 相关全部差异流向（含单边：一侧类别为空）
    all_flows = sorted(
        [(il, fv, diff_type, stat["count"], stat["amount"])
         for (il, fv, diff_type), stat in analysis.flow_stats.items() if "Rent" in (il, fv)],
        key=lambda x: x[3], reverse=True,
    )
    detail_n = len(analysis.category_details.get("Rent", []))
    # 转账类流入（Illion 识别为转账、finv 识别为 Rent）
    trans_in = sum(f[3] for f in all_flows if f[1] == "Rent" and f[0] in ("External Transfers", "Internal Transfer"))
    it_rent = next((f for f in all_flows if f[0] == "Internal Transfer" and f[1] == "Rent"), None)
    finv_only_n = sum(f[3] for f in all_flows if f[1] == "Rent" and f[0] == "")
    illion_only_n = sum(f[3] for f in all_flows if f[0] == "Rent" and f[1] == "")

    paras: List[str] = []

    # 段落 1：一致率、体量、交易方向、单边识别偏向
    p1 = f"Rent 类别按并集口径的一致率为 {fmt_pct(inter_share)}"
    if union and total:
        p1 += f"，并集交易量占全部交易的 {fmt_pct(union / total)}"
    if detail_n:
        credit, debit = analysis.dr_cr_distribution("Rent")
        if credit + debit:
            dr_share = debit / (credit + debit)
            if dr_share >= 0.5:
                p1 += f"；交易方向以支出（debit）为主，占 {fmt_pct(dr_share)}，与租金支出场景一致"
            else:
                p1 += f"；交易方向以收入（credit）为主，占 {fmt_pct(1 - dr_share)}"
        else:
            p1 += "；差异样本未记录交易方向"
    p1 += "。"
    if finv_only_share or illion_only_share:
        if finv_only_share > illion_only_share * 2:
            p1 += (f"在双方识别不一致的样本中，单边差异明显偏向 finv：finv 独有识别占 {fmt_pct(finv_only_share)}"
                   f"，显著高于 Illion 独有识别的 {fmt_pct(illion_only_share)}，说明存在 Illion 漏识别的租金交易。")
        elif illion_only_share > finv_only_share * 2:
            p1 += (f"在双方识别不一致的样本中，单边差异明显偏向 Illion：Illion 独有识别占 {fmt_pct(illion_only_share)}"
                   f"，显著高于 finv 独有识别的 {fmt_pct(finv_only_share)}，说明 finv 对租金类交易的识别存在遗漏。")
        else:
            p1 += (f"在双方识别不一致的样本中，单边差异两侧接近（Illion 独有 {fmt_pct(illion_only_share)}，"
                   f"finv 独有 {fmt_pct(finv_only_share)}）。")
    paras.append(p1)

    # 段落 2：差异流向构成与主因判断（占比+金额口径）
    if not all_flows or not detail_n:
        paras.append("底稿中未发现 Rent 相关差异明细，Rent 两侧识别结果一致，无需针对性调整。")
        return paras
    p2 = f"Rent 相关差异占全部差异的 {fmt_pct(detail_n / diff_total)}。"
    flow_parts: List[str] = []
    if trans_in and finv_only_n:
        flow_parts.append(f"从差异流向看，转账类流入（Internal Transfer / External Transfers → Rent）和 finv 单边识别"
                          f"（Illion 无值，finv 识别为 Rent）是主要来源，分别占 Rent 相关差异的 "
                          f"{fmt_pct(trans_in / detail_n)} 和 {fmt_pct(finv_only_n / detail_n)}")
        if it_rent:
            flow_parts.append(f"其中 Internal Transfer → Rent 单项占 {fmt_pct(it_rent[3] / detail_n)}"
                              f"（涉及金额 {fmt_money(it_rent[4])}）")
    else:
        top = all_flows[0]
        flow_parts.append(f"从差异流向看，{top[0] or '（空）'} → {top[1] or '（空）'} 为主"
                          f"（占 Rent 相关差异的 {fmt_pct(top[3] / detail_n)}）")
    if illion_only_n:
        flow_parts.append(f"Illion 单边识别占 {fmt_pct(illion_only_n / detail_n)}")
    p2 += "；".join(flow_parts) + "。"
    # 主因判断（按数据占比触发；"接近六成" 文案仅在实际占比达到约 55% 及以上时使用）
    if trans_in >= detail_n * 0.55:
        p2 += (f"转账类流入合计占比接近六成，说明 Rent 差异主因是租金支付与转账类交易的边界划分，"
               f"即部分租金支付被识别为转账。"
               f"建议优先核对转账类交易中收款方为房产中介、物业管理等具有租金特征的识别规则，"
               f"将疑似租金收款方优先识别为 Rent。")
    elif trans_in >= detail_n * 0.4 and trans_in >= finv_only_n:
        p2 += (f"综合来看，Rent 差异的主因是租金支付与转账类交易的边界划分"
               f"（转账类流入合计占 Rent 相关差异的 {fmt_pct(trans_in / detail_n)}），"
               f"即部分租金支付被识别为转账。"
               f"建议优先核对转账类交易中收款方为房产中介、物业管理等具有租金特征的识别规则，"
               f"将疑似租金收款方优先识别为 Rent。")
    elif finv_only_n >= detail_n * 0.2:
        p2 += (f"综合来看，Rent 差异的主因是 Illion 对租金交易的识别覆盖不足"
               f"（finv 单边识别占 Rent 相关差异的 {fmt_pct(finv_only_n / detail_n)}），"
               f"建议检查 Illion 侧租金关键词与商户知识库的覆盖边界。")
    elif illion_only_n >= detail_n * 0.2:
        p2 += (f"综合来看，Rent 差异的主因是 finv 对租金交易的识别覆盖不足"
               f"（Illion 单边识别占 Rent 相关差异的 {fmt_pct(illion_only_n / detail_n)}），"
               f"建议检查 finv 侧租金关键词与商户知识库的覆盖边界。")
    else:
        p2 += "综合来看，Rent 差异分布较为分散，建议对主要差异流向抽样核验后再确定规则调整方向。"
    paras.append(p2)
    return paras


def information_summary_paragraphs(analysis: Analysis, item: Dict[str, Any]) -> List[str]:
    """Information 专项差异分析总结（两段式文案按人工润色定稿，数字仍动态取自底稿）。

    该类别与 Rent / Gambling 不同：一致率接近 0，单边差异为绝对主导；
    段落 2 的「进一步核验两侧定义」体现 Illion 侧 0 金额豁免/通知明细
    与 finv 侧有金额信息类消费的类别定义口径差异。
    流向语义与报告一致：'X → Information' 表示 Illion 侧为 X、finv 侧为 Information。
    """
    inter_share = to_float(item.get("交集占比（并集）"))
    il_only = to_float(item.get("illion独有占比（并集）"))
    fv_only = to_float(item.get("finv独有占比（并集）"))
    union = to_float(item.get("并集数量"))
    total = analysis.total_transactions or 0
    diff_total = analysis.diff_total or 0
    detail_n = len(analysis.category_details.get("Information", []))
    all_flows = sorted(
        [(il, fv, dt, s["count"], s["amount"])
         for (il, fv, dt), s in analysis.flow_stats.items() if "Information" in (il, fv)],
        key=lambda x: x[3], reverse=True,
    )
    fv_only_n = sum(f[3] for f in all_flows if f[1] == "Information" and f[0] == "")
    il_only_n = sum(f[3] for f in all_flows if f[0] == "Information" and f[1] == "")
    il_cov = to_float(item.get("illion覆盖率"))
    fv_cov = to_float(item.get("finv覆盖率"))

    paras: List[str] = []

    # 段落 1：一致率、体量、交易方向、单边识别偏向
    p1 = f"Information 类别按并集口径的一致率仅 {fmt_pct(inter_share)}"
    if union and total:
        p1 += f"，并集交易量占全部交易的 {fmt_pct(union / total)}"
    credit, debit = analysis.dr_cr_distribution("Information")
    if credit + debit:
        dr_share = debit / (credit + debit)
        if dr_share >= 0.5:
            p1 += f"；交易方向以支出（debit）为主，占 {fmt_pct(dr_share)}"
        else:
            p1 += f"；交易方向以收入（credit）为主，占 {fmt_pct(1 - dr_share)}"
    p1 += "。"
    if fv_only or il_only:
        if fv_only > il_only * 2:
            p1 += (f"单边差异明显偏向 finv：finv 独有识别占 {fmt_pct(fv_only)}"
                   f"，显著高于 Illion 独有识别的 {fmt_pct(il_only)}。")
        elif il_only > fv_only * 2:
            p1 += (f"单边差异明显偏向 Illion：Illion 独有识别占 {fmt_pct(il_only)}"
                   f"，显著高于 finv 独有识别的 {fmt_pct(fv_only)}。")
        else:
            p1 += (f"单边差异两侧接近（Illion 独有 {fmt_pct(il_only)}，"
                   f"finv 独有 {fmt_pct(fv_only)}）。")
    paras.append(p1)

    # 段落 2：相关差异占比与单边构成 → 定义口径核验 → 主因判断与建议
    if not all_flows or not detail_n:
        paras.append("底稿中未发现 Information 相关差异明细，两侧识别结果一致，无需针对性调整。")
        return paras
    if fv_only_n and il_only_n:
        p2 = (f"Information 相关差异占全部差异的 "
              f"{fmt_pct(detail_n / diff_total) if diff_total else ''}，"
              f"其中 finv 单边识别（Illion 无值，finv 识别为 Information）占 {fmt_pct(fv_only_n / detail_n)}，"
              f"Illion 单边识别占 {fmt_pct(il_only_n / detail_n)}。")
    else:
        top = all_flows[0]
        p2 = (f"Information 相关差异占全部差异的 "
              f"{fmt_pct(detail_n / diff_total) if diff_total else ''}。"
              f"差异流向以 {top[0] or '（空）'} → {top[1] or '（空）'} 为主"
              f"（占 Information 相关差异的 {fmt_pct(top[3] / detail_n)}）。")
    # 定义口径核验：两侧明细的金额构成与重合情况
    il_cat = [d for d in analysis.details if d["illion_category"] == "Information"]
    fv_cat = [d for d in analysis.details if d["finv_category"] == "Information"]
    both_n = sum(1 for d in analysis.details
                 if d["illion_category"] == "Information" and d["finv_category"] == "Information")
    il_zero_share = sum(1 for d in il_cat if d["amount"] == 0) / len(il_cat) if il_cat else 0
    fv_zero_share = sum(1 for d in fv_cat if d["amount"] == 0) / len(fv_cat) if fv_cat else 0
    if il_zero_share == 1.0 and fv_zero_share == 0 and both_n == 0:
        # 定稿场景：Illion 全部为 0 金额豁免/通知明细、finv 全为有金额消费、两侧无重合
        sample = (next((d for d in il_cat if d["amount"] == 0 and d["text"]), None)
                  or next((d for d in il_cat if d["text"]), None))
        sample_txt = f"（如“{sample['text'] or ''}”）" if sample else ""
        p2 += (f"进一步核验两侧定义发现，Illion 侧识别出的明细全部为 0 金额的费用豁免/通知类交易{sample_txt}，"
               f"finv 侧则均为有金额的信息类消费，两侧明细无重合。"
               f"这说明差异并非同一笔交易的归类分歧，而是类别定义口径不同："
               f"Illion 将 0 金额的通知/豁免明细归入 Information，基本不构成实际消费；"
               f"finv 侧才是有金额的真实信息类消费。")
    else:
        # 数据形态偏离定稿场景时的动态表述（保留 0 金额构成与重合比例，保证表述不失真）
        sample = next((d for d in il_cat if d["text"]), None)
        sample_txt = f"（如「{sample['text'] or ''}」）" if sample else ""
        union_n = len(il_cat) + len(fv_cat) - both_n
        if both_n == 0:
            both_txt = "两侧明细完全不存在重合"
        else:
            both_txt = f"两侧明细重合较少（交集占比 {fmt_pct(both_n / union_n if union_n else 0)}）"
        p2 += (f"进一步核验两侧对 Information 的定义：Illion 侧识别出的明细中金额为 0 的占 "
               f"{fmt_pct(il_zero_share)}，典型为费用豁免/通知类交易{sample_txt}；"
               f"finv 侧识别出的明细中金额为 0 的占 {fmt_pct(fv_zero_share)}，以有金额的信息类消费为主。"
               f"{both_txt}，即两侧的 Information 是两批不同的交易，"
               f"差异并非同一笔交易的归类分歧，而是类别定义口径不同。")
    # 主因判断与建议（覆盖率对照动态；仅当 finv 覆盖确实大于 Illion 时按定稿口径表述）
    if fv_cov >= il_cov:
        p2 += (f"综合来看，finv 对该类别的识别范围明显大于 Illion"
               f"（覆盖率 {fmt_pct(fv_cov)} vs {fmt_pct(il_cov)}），"
               f"建议以 finv 口径为准，并明确 Information 是否包含 0 金额明细；"
               f"同时核对 finv 侧信息类交易（资讯/信息服务类商户）的识别规则与 Illion 的覆盖边界，"
               f"确认新增识别是否合理，或将费用豁免/通知类交易单独归类复核。")
    else:
        p2 += (f"综合来看，两侧对 Information 的识别范围差异不大，但明细重合度较低"
               f"（覆盖率 Illion {fmt_pct(il_cov)} vs finv {fmt_pct(fv_cov)}），"
               f"建议先明确 Information 是否包含 0 金额明细，再核对两侧对信息类交易的识别规则与覆盖边界，"
               f"确认以哪一侧口径为准或是否需要统一归类口径。")
    paras.append(p2)
    return paras


def gambling_summary_paragraphs(analysis: Analysis, item: Dict[str, Any]) -> List[str]:
    """Gambling 专项差异分析总结（两段式文案按人工润色定稿，数字仍动态取自底稿）。

    与 Rent 专项同构；段落 2 的主因判断为 finv 单边识别与转账类流入的边界重叠。
    流向语义与报告一致：'X → Gambling' 表示 Illion 侧为 X、finv 侧为 Gambling；
    '仅 finv 有值 → Gambling' 表示 finv 单边识别为 Gambling、Illion 无分类。
    """
    inter_share = to_float(item.get("交集占比（并集）"))
    il_only = to_float(item.get("illion独有占比（并集）"))
    fv_only = to_float(item.get("finv独有占比（并集）"))
    union = to_float(item.get("并集数量"))
    total = analysis.total_transactions or 0
    diff_total = analysis.diff_total or 0
    detail_n = len(analysis.category_details.get("Gambling", []))
    all_flows = sorted(
        [(il, fv, dt, s["count"], s["amount"])
         for (il, fv, dt), s in analysis.flow_stats.items() if "Gambling" in (il, fv)],
        key=lambda x: x[3], reverse=True,
    )
    fv_only_n = sum(f[3] for f in all_flows if f[1] == "Gambling" and f[0] == "")
    il_only_n = sum(f[3] for f in all_flows if f[0] == "Gambling" and f[1] == "")
    trans_in = sum(f[3] for f in all_flows if f[1] == "Gambling"
                   and f[0] in ("External Transfers", "Internal Transfer"))

    paras: List[str] = []

    # 段落 1：一致率、体量、交易方向、单边识别偏向
    p1 = f"Gambling 类别按并集口径的一致率为 {fmt_pct(inter_share)}"
    if union and total:
        p1 += f"，并集交易量占全部交易的 {fmt_pct(union / total)}"
    credit, debit = analysis.dr_cr_distribution("Gambling")
    if credit + debit:
        dr_share = debit / (credit + debit)
        if dr_share >= 0.5:
            p1 += f"；交易方向以支出（debit）为主，占比 {fmt_pct(dr_share)}"
        else:
            p1 += f"；交易方向以收入（credit）为主，占比 {fmt_pct(1 - dr_share)}"
    p1 += "。"
    if fv_only > il_only * 2:
        p1 += (f"在双方识别不一致的样本中，单边差异明显偏向 finv：finv 独有识别占 {fmt_pct(fv_only)}"
               f"，高于 Illion 独有识别的 {fmt_pct(il_only)}。")
    elif il_only > fv_only * 2:
        p1 += (f"在双方识别不一致的样本中，单边差异明显偏向 Illion：Illion 独有识别占 {fmt_pct(il_only)}"
               f"，高于 finv 独有识别的 {fmt_pct(fv_only)}。")
    else:
        p1 += (f"在双方识别不一致的样本中，单边识别两侧接近（Illion 独有 {fmt_pct(il_only)}，"
               f"finv 独有 {fmt_pct(fv_only)}）。")
    paras.append(p1)

    # 段落 2：相关差异占比 → 流向构成 → 主因判断与建议
    if not all_flows or not detail_n:
        paras.append("底稿中未发现 Gambling 相关差异明细，两侧识别结果一致，无需针对性调整。")
        return paras
    p2 = f"Gambling 相关差异占全部差异的 {fmt_pct(detail_n / diff_total) if diff_total else ''}。"
    if trans_in and fv_only_n and trans_in + fv_only_n >= detail_n * 0.5:
        p2 += (f"从差异流向看，主要来源为 finv 单边识别"
               f"（Illion 为空/无值，finv 识别为 Gambling）"
               f"和转账类流入（External Transfers / Internal Transfer → Gambling），"
               f"分别占 Gambling 相关差异的 {fmt_pct(fv_only_n / detail_n)} 和 {fmt_pct(trans_in / detail_n)}")
        if il_only_n:
            p2 += (f"；Illion 单边识别（Illion 识别为 Gambling，finv 无值）"
                   f"占 {fmt_pct(il_only_n / detail_n)}")
        p2 += "。"
        p2 += (f"finv 单边识别与转账类流入合计约 {fmt_pct((trans_in + fv_only_n) / detail_n)}，"
               f"说明 Gambling 差异主要集中在博彩识别与转账/其他消费的边界，"
               f"可能反映 Illion 对经转账渠道完成的博彩交易存在漏识别，或 finv 存在过度识别。"
               f"建议重点核对转账类交易中博彩特征收款方的识别规则，并抽查 finv 单边识别样本，"
               f"确认属于覆盖扩展还是过度识别。")
    else:
        top = all_flows[0]
        p2 += (f"从差异流向看，{top[0] or '（空）'} → {top[1] or '（空）'} 为主"
               f"（占 Gambling 相关差异的 {fmt_pct(top[3] / detail_n)}）")
        extras = []
        if fv_only_n:
            extras.append(f"finv 单边识别（仅 finv 有值 → Gambling）占 {fmt_pct(fv_only_n / detail_n)}")
        if il_only_n:
            extras.append(f"Illion 单边识别（Gambling → 仅 Illion 有值）占 {fmt_pct(il_only_n / detail_n)}")
        if trans_in:
            extras.append(f"转账类流入（External Transfers / Internal Transfer → Gambling）占 {fmt_pct(trans_in / detail_n)}")
        if extras:
            p2 += "；" + "；".join(extras)
        p2 += "。"
        p2 += "综合来看，Gambling 差异分布较为分散，建议按上述主要流向抽样核验后再确定规则调整方向。"
    paras.append(p2)
    return paras


def expense_flag_analysis(analysis: Analysis, item: Dict[str, Any]) -> List[str]:
    """支出类红黄灯指标专项差异分析（Retail / Donations / Automotive；Gambling 与 Information 走专项分支）。

    两段式（与 Rent 专项同构）：① 一致率·体量·交易方向·单边识别偏向；
    ② 差异流向构成·主因判断·处理建议。全部数据动态取自底稿，无硬编码数字。
    流向语义与报告一致：'X → Retail' 表示 Illion 侧为 X、finv 侧为 Retail；
    '仅 finv 有值 → Retail' 表示 finv 单边识别为 Retail、Illion 无分类。
    """
    cat = item["category"]
    # Gambling / Information 专项总结为两段式人工润色定稿文案（数字仍动态取自底稿）
    if cat == "Gambling":
        return gambling_summary_paragraphs(analysis, item)
    if cat == "Information":
        return information_summary_paragraphs(analysis, item)
    inter_share = to_float(item.get("交集占比（并集）"))
    il_only = to_float(item.get("illion独有占比（并集）"))
    fv_only = to_float(item.get("finv独有占比（并集）"))
    union = to_float(item.get("并集数量"))
    total = analysis.total_transactions or 0
    diff_total = analysis.diff_total or 0
    detail_n = len(analysis.category_details.get(cat, []))
    all_flows = sorted(
        [(il, fv, dt, s["count"], s["amount"])
         for (il, fv, dt), s in analysis.flow_stats.items() if cat in (il, fv)],
        key=lambda x: x[3], reverse=True,
    )
    fv_only_n = sum(f[3] for f in all_flows if f[1] == cat and f[0] == "")
    il_only_n = sum(f[3] for f in all_flows if f[0] == cat and f[1] == "")
    trans_in = sum(f[3] for f in all_flows if f[1] == cat and f[0] in ("External Transfers", "Internal Transfer"))

    paras: List[str] = []

    # 段落 1：一致率、体量、交易方向、单边识别偏向
    p1 = f"{cat} 类别一致率（类别并集）为 {fmt_pct(inter_share)}"
    if union and total:
        p1 += f"，并集占全部交易的 {fmt_pct(union / total)}"
    credit, debit = analysis.dr_cr_distribution(cat)
    if credit + debit:
        dr_share = debit / (credit + debit)
        if dr_share >= 0.5:
            p1 += f"；方向以 debit（支出）为主，占 {fmt_pct(dr_share)}"
        else:
            p1 += f"；方向以 credit（收入）为主，占 {fmt_pct(1 - dr_share)}"
    p1 += "。"
    if fv_only > il_only * 2:
        p1 += (f"单边差异明显偏向 finv 一侧：finv 独有识别占比 {fmt_pct(fv_only)}"
               f"，显著高于 Illion 独有占比 {fmt_pct(il_only)}。")
    elif il_only > fv_only * 2:
        p1 += (f"单边差异明显偏向 Illion 一侧：Illion 独有识别占比 {fmt_pct(il_only)}"
               f"，显著高于 finv 独有占比 {fmt_pct(fv_only)}。")
    else:
        p1 += f"单边差异两侧接近（Illion 独有 {fmt_pct(il_only)}，finv 独有 {fmt_pct(fv_only)}）。"
    paras.append(p1)

    # 段落 2：差异流向构成与主因判断（类别专用分支）
    if not all_flows or not detail_n:
        paras.append(f"底稿中未发现 {cat} 相关差异明细，两侧识别结果一致，无需针对性调整。")
        return paras
    top = all_flows[0]
    # 差异构成统一按占比表述（不放笔数；笔数与底稿明细数联动，占比更利于横向比较）
    p2 = (f"{cat} 相关差异占全部差异的 "
          f"{fmt_pct(detail_n / diff_total) if diff_total else ''}。"
          f"差异流向以 {top[0] or '（空）'} → {top[1] or '（空）'} 为主"
          f"（占 {cat} 相关差异的 {fmt_pct(top[3] / detail_n)}）")
    extras = []
    if fv_only_n:
        extras.append(f"finv 单边识别（仅 finv 有值 → {cat}）占 {fmt_pct(fv_only_n / detail_n)}")
    if il_only_n:
        extras.append(f"Illion 单边识别（{cat} → 仅 Illion 有值）占 {fmt_pct(il_only_n / detail_n)}")
    if trans_in:
        extras.append(f"转账类流入（External Transfers / Internal Transfer → {cat}）占 {fmt_pct(trans_in / detail_n)}")
    if extras:
        p2 += "；" + "；".join(extras)
    p2 += "。"

    if cat == "Retail":
        p2 += (f"综合来看，Retail 差异的主因是零售支付与转账类的边界划分：转账类流入占 "
               f"{fmt_pct(trans_in / detail_n)}，表明部分经转账渠道完成的零售消费，finv 识别为 Retail、"
               f"Illion 识别为转账类；叠加 finv 单边识别占 {fmt_pct(fv_only_n / detail_n)}，"
               f"整体表现为 finv 对零售交易的识别范围更广。建议优先核对转账类交易中商户为零售特征"
               f"（商超、便利店等）的识别规则，并抽查 finv 单边识别的 Retail 交易以确认覆盖合理性。")
    elif cat == "Donations":
        p2 += (f"综合来看，Donations 差异的主因是 finv 对捐赠类交易的识别覆盖不足"
               f"（{fmt_pct(il_only_n / detail_n)} 的捐赠相关差异仅 Illion 识别、finv 无分类），方向与其他红灯类别相反，"
               f"是 Illion 覆盖面更广的典型。鉴于体量较小（并集占全部交易的 "
               f"{fmt_pct(union / total) if total else ''}），建议在 finv 侧补充捐赠相关商户/关键词后复核。")
    elif cat == "Automotive":
        g2a = sum(f[3] for f in all_flows if f[0] == "Groceries" and f[1] == cat)
        a2g = sum(f[3] for f in all_flows if f[0] == cat and f[1] == "Groceries")
        t2a = sum(f[3] for f in all_flows if f[0] in ("External Transfers", "Internal Transfer") and f[1] == cat)
        a2t = sum(f[3] for f in all_flows if f[0] == cat and f[1] in ("External Transfers", "Internal Transfer"))
        a2e = sum(f[3] for f in all_flows if f[0] == cat and f[1] == "Entertainment")
        p2 += (f"综合来看，Automotive 差异没有单一主因，表现为汽车相关消费（油费、养护、车险等）"
               f"在两侧知识库中的归属规则存在系统性边界分歧：主要流向为 Groceries ↔ Automotive"
               f"（合计占 {fmt_pct((g2a + a2g) / detail_n)}）、转账类 ↔ Automotive"
               f"（合计占 {fmt_pct((t2a + a2t) / detail_n)}）、"
               f"Automotive → Entertainment（占 {fmt_pct(a2e / detail_n)}），且两侧单边识别均有贡献"
               f"（finv 单边占 {fmt_pct(fv_only_n / detail_n)}、Illion 单边占 {fmt_pct(il_only_n / detail_n)}）。"
               f"建议对上述主要流向抽样核验，优先确认 Groceries（商超便利店购车用品）与转账类"
               f"（转账付车款）的归属规则，再决定是否需要统一。")
    # 注：Gambling 已在函数开头走专项分支返回，此处不重复该类别分支
    else:
        p2 += "综合来看，该类别差异分布较为分散，建议对主要差异流向抽样核验后再确定规则调整方向。"
    paras.append(p2)
    return paras


def render_category_block(doc: Document, item: Dict[str, Any], analysis: Analysis, detailed: bool = False,
                          number: Optional[str] = None,
                          extra_samples: Optional[List[Dict[str, Any]]] = None) -> None:
    """结论先行结构渲染单个类别：类别结论 → 类别明细（定义 / 核心指标 / 解读 / 流向 / 方向金额 / 样本）。

    number: 小节编号（如 "3.1.1"）输出为标题；None 输出旧式标题；"" 不输出标题（专项小节沿用外部标题）。
    """
    category = item["category"]
    group = item.get("group", "")
    # 「合计差异金额」只覆盖底稿给出金额的流向，先算好覆盖范围交给 add_para_labeled
    doc.amount_note = _amount_note(analysis, category) if detailed else ""
    if number is None:
        add_heading(doc, f"Category: {category}（{group}）", level=3)
    elif number:
        add_heading(doc, f"{number} {category}", level=3)

    # 类别结论（结论先行，无小标题）
    add_para(doc, category_headline(item, analysis), size=9.5, space_after=4)

    # 类别明细
    add_para(doc, "类别明细", size=9.5, bold=True, space_after=2)
    add_para_labeled(doc, "类别定义", CATEGORY_DEFINITION.get(category, ""), size=9.5, space_after=3)

    add_para(doc, "核心指标", size=9.5, bold=True, space_after=2)
    total = analysis.total_transactions
    union = to_float(item.get("并集数量"))
    inter = to_float(item.get("交集数量"))
    add_table(doc,
              ["类别", "Illion覆盖率", "finv覆盖率", "并集占比", "交集占比", "一致率（类别并集）", "Illion独有占比", "finv独有占比"],
              [[category, fmt_pct(item.get("illion覆盖率")), fmt_pct(item.get("finv覆盖率")),
                fmt_pct(union / total if total else 0), fmt_pct(inter / total if total else 0),
                fmt_pct(item.get("交集占比（并集）")),
                fmt_pct(item.get("illion独有占比（并集）")), fmt_pct(item.get("finv独有占比（并集）"))]],
              [2.3, 1.9, 1.9, 1.7, 1.7, 2.2, 2.2, 2.2], font_size=8.5,
              align_center_cols={1, 2, 3, 4, 5, 6, 7},
              highlights=indicator_highlights([item]))
    add_para_labeled(doc, "指标解读", category_narrative(item, total), size=9.5, space_after=3)

    if detailed:
        flows = analysis.category_flows(category, limit=5)
        if flows:
            add_para(doc, "Top 差异流向", size=9.5, bold=True, space_after=2)
            add_flow_table(doc, flows, denom=len(analysis.category_details.get(category, [])),
                           share_label="占该类差异")
        credit, debit = analysis.dr_cr_distribution(category)
        dir_total = credit + debit
        if dir_total:
            credit_share = fmt_pct(credit / dir_total)
            debit_share = fmt_pct(debit / dir_total)
            dir_txt = f"差异样本中 credit 方向占 {credit_share}，debit 方向占 {debit_share}；"
        else:
            dir_txt = "差异样本未记录交易方向；"
        add_para_labeled(doc, "交易方向与金额",
                         f"{dir_txt}合计差异金额 "
                         f"{fmt_money(sum(stat['amount'] for (il, fv, _), stat in analysis.flow_stats.items() if category in (il, fv)))}。",
                         size=9.5, space_after=2)
        add_para(doc, "典型交易样本", size=9.5, bold=True, space_after=2)
        samples = analysis.top_samples(category, limit=5)
        if extra_samples:
            seen = {(s.get("text") or "", s.get("transaction_date") or "") for s in samples}
            for s in extra_samples:
                key = (s.get("text") or "", s.get("transaction_date") or "")
                if key not in seen:
                    seen.add(key)
                    samples.append(s)
            samples.sort(key=lambda d: -abs(to_float(d.get("amount")) or 0))
        add_samples_table(doc, samples)
    doc.amount_note = ""


def build_report(doc: Document, analysis: Analysis, chart_png: Optional[Path] = None,
                 chart_2_2_png: Optional[Path] = None,
                 input_name: str = paths.REVIEW_REPORT_NAME,
                 generated_date: str = "") -> None:
    m = analysis

    # ===== 封面 =====
    add_para(doc, "", size=10, space_after=40)
    p = add_para(doc, "BS-CAT 全分类类别表现分析报告", size=24, bold=True, color=BLUE, align="center", space_after=6)
    add_para(doc, "Income, Expense, Transfer and Liability Category Performance", size=11,
             color=GRAY, align="center", space_after=24)
    add_table(doc, ["样本范围", "分类范围", "比较对象", "报告日期"],
              [[f"{fmt_num(m.total_transactions)} 笔交易", f"{len(m.categories)} 个类别",
                "illion vs finv / BS-CAT", generated_date]],
              [4.25, 4.25, 4.25, 4.25], font_size=10, align_center_cols=set())
    add_para(doc, "", size=10, space_after=12)
    add_callout(doc,
                f"本报告由数据底稿 {input_name} 自动生成，覆盖收入、支出、转账、负债四大业务板块共 {len(m.categories)} 个类别。"
                "所有数字均从底稿动态计算；负债部分保留类别指标摘要，不重复展开已有 Liability 专项报告。")
    add_para(doc, "", size=10, space_after=12)
    add_para(doc, "数据来源", size=12, bold=True, color=BLUE_MID)
    add_para(doc, f"Excel 底稿：00_核心对比（核心指标与逐类别指标）、01_差异诊断地图（Top {len(analysis.top_flows)} 差异流向）、"
                  f"03_排查明细（{fmt_num(len(analysis.details))} 笔差异明细，含金额与交易方向）。",
             size=9.5, color=GRAY)
    add_page_break(doc)

    # ===== 1. 执行摘要 =====
    add_heading(doc, "1. 执行摘要", level=1)
    finv_only_share = m.finv_only_total / m.diff_total if m.diff_total else 0
    illion_only_share = m.illion_only_total / m.diff_total if m.diff_total else 0
    mismatch_share = m.mismatch / m.diff_total if m.diff_total else 0
    diff_sources = sorted(
        [("双方分类不一致", mismatch_share), ("仅 finv 有值", finv_only_share),
         ("仅 Illion 有值", illion_only_share)],
        key=lambda x: -x[1])
    diff_source_txt = "、".join(f"{name}占 {fmt_pct(share)}" for name, share in diff_sources)
    # 执行摘要：覆盖率对比（覆盖高者在前）、一致率、差异构成（降序、首项强调）、结论
    cov_leader = "finv" if to_float(m.finv_coverage) >= to_float(m.illion_coverage) else "Illion"
    cov_follower = "Illion" if cov_leader == "finv" else "finv"
    leader_cov = m.finv_coverage if cov_leader == "finv" else m.illion_coverage
    follower_cov = m.illion_coverage if cov_leader == "finv" else m.finv_coverage
    cmp_txt = "略高于" if abs(to_float(m.finv_coverage) - to_float(m.illion_coverage)) < 0.05 else "高于"
    mismatch_is_top1 = diff_sources[0][0] == "双方分类不一致"
    source_concl = ("分类分歧是主要差异来源，而非单方覆盖缺失"
                    if mismatch_is_top1 else "单方覆盖缺失是主要差异来源，而非分类分歧")
    summary_diff_txt = "，".join(
        f"{name}占比最高，为 {fmt_pct(share)}" if i == 0 else f"{name}占 {fmt_pct(share)}"
        for i, (name, share) in enumerate(diff_sources))
    summary = (
        f"本次评估显示，{cov_leader} 的分类覆盖率为 {fmt_pct(leader_cov)}，"
        f"{cmp_txt} {cov_follower} 的 {fmt_pct(follower_cov)}，"
        f"说明 {cov_leader} 覆盖率基本持平于 {cov_follower}，{cov_leader} 在覆盖范围上具有一定优势。"
        f"在双方均给出分类的交易中，分类一致率达到 {fmt_pct(m.joint_agreement)}，"
        f"表明两者对同一交易的分类结果整体较为一致。"
        f"在存在差异的交易中，{summary_diff_txt}，说明{source_concl}。"
        f"差异交易主要集中在转账类、高频消费类及部分负债类。"
        f"总体来看，{cov_leader} 的核心特征是覆盖更广，但覆盖范围的扩大也引入了更多分类差异；"
        f"当前主要问题并非覆盖不足，而是部分类别下双方分类规则和边界不一致。"
        f"后续优化应优先聚焦转账、高频消费和部分负债类场景，推动分类标准对齐与差异治理。"
    )
    add_para(doc, summary, size=10.5)
    add_callout(doc, "本段为报告的核心结论：两侧覆盖率对比、双方均分类交易的一致率、差异构成，以及主要差异来源。")
    add_page_break(doc)

    # ===== 2. 覆盖与一致全景 =====
    add_heading(doc, "2. 覆盖与一致全景", level=1)
    add_para(doc, "本部分自顶向下展示覆盖与一致情况：先看总体一致率与差异性质，再看业务板块，最后拆解到类别。"
                  "覆盖率的含义是「Illion / finv 识别为某个类别的交易占总交易的比例」；"
                  "一致率的含义是「双方分类一致（类别完全相同）的交易占至少一侧有分类交易的比例」。"
                  "覆盖与一致的情况清楚后，第 3 章进入类别细探，各类别的全量指标明细汇总于第 4 章附录。", size=9.5)

    # 2.1 总体覆盖、一致率与差异性质（原 2.2 差异性质拆解已并入本节）
    add_heading(doc, "2.1 总体覆盖、一致率与差异性质", level=2)
    adjusted_agreement = m.metrics.get("覆盖调整后一致率", {}).get("result")
    add_table(doc, ["指标", "数值", "口径说明"],
              [
                  ["Illion 覆盖率", fmt_pct(m.illion_coverage), "Illion 有分类交易 / 总交易数"],
                  ["finv 覆盖率", fmt_pct(m.finv_coverage), "finv 有分类交易 / 总交易数"],
                  ["覆盖差（finv − Illion）", fmt_pct(to_float(m.finv_coverage) - to_float(m.illion_coverage)),
                   "finv 高于 Illion 的幅度"],
                  ["双方均有分类", fmt_pct(m.joint_nonempty / m.total_transactions if m.total_transactions else 0),
                   "双方分类均非空的交易 / 总交易数"],
                  ["双方非空一致率", fmt_pct(m.joint_agreement), "类别一致 / 双方均有分类"],
                  ["覆盖调整后一致率", fmt_pct(adjusted_agreement), "类别一致 / 至少一侧有分类"],
                  ["差异率（至少一侧有分类）", fmt_pct(m.diff_total / m.union_nonempty if m.union_nonempty else 0),
                   "差异总数 / 至少一侧有分类"],
              ],
              [4.4, 4.4, 8.2], font_size=9, align_center_cols={1})
    union_rate = m.diff_total / m.union_nonempty if m.union_nonempty else 0
    d1_name, d1_share = diff_sources[0]
    d2_name, d2_share = diff_sources[1]
    d3_name, d3_share = diff_sources[2]
    cov_gap_pp = abs(to_float(m.finv_coverage) - to_float(m.illion_coverage)) * 100
    # 2.1 正文（严谨表述）：覆盖率对比 → 共同覆盖内一致率 → 并集口径 → 口径落差来源
    coverage_degree = "略广" if cov_gap_pp < 1.0 else "更广"
    p1 = (
        f"在覆盖率上，{cov_leader}（{fmt_pct(leader_cov)}）较 {cov_follower}（{fmt_pct(follower_cov)}）"
        f"高出 {cov_gap_pp:.2f} 个百分点，覆盖范围{coverage_degree}。"
        f"全部交易中，双方均有分类的占比为 "
        f"{fmt_pct(m.joint_nonempty / m.total_transactions if m.total_transactions else 0)}，"
        f"在这部分交易内，类别一致率达到 {fmt_pct(m.joint_agreement)}，"
        f"表明双方在共同覆盖范围内的判定结果较为一致。"
        f"然而，若将仅单侧有分类的交易也纳入评价基准（即「至少一侧有分类」口径），"
        f"一致率下降至 {fmt_pct(adjusted_agreement)}，差异率相应为 {fmt_pct(union_rate)}。"
        f"两个口径之间的落差（相差 "
        f"{abs(float(fmt_pct(m.joint_agreement).rstrip('%')) - float(fmt_pct(adjusted_agreement).rstrip('%'))):.2f} 个百分点）"
        f"源于仅单侧有分类的交易，该部分占并集（至少一侧有分类）的 "
        f"{fmt_pct((m.union_nonempty - m.joint_nonempty) / m.union_nonempty if m.union_nonempty else 0)}；"
        f"将其计入差异后，整体一致率明显走低。"
    )
    add_para(doc, p1, size=9.5)
    # 差异性质拆解（原 2.2 并入）：先给出分类逻辑，再以表格呈现具体分布，最后解读与收尾
    add_para(doc, "进一步拆解差异构成，可区分两种性质：双方分类不一致"
                  "（两侧均有分类但标签不同，反映分类规则或边界判定的分歧）与单侧识别缺失"
                  "（仅一侧有分类，反映覆盖缺口）。具体分布如下：", size=9.5)
    diff_notes = {"双方分类不一致": "两侧均有分类，但类别标签不同",
                  "仅 finv 有值": "finv 新增识别或 Illion 侧缺少分类",
                  "仅 Illion 有值": "finv 侧缺少分类（仅 Illion 侧识别）"}
    diff_rows = [[name, fmt_pct(share), diff_notes[name]] for name, share in diff_sources] + \
                [["总差异数", "100.00%", "分类不一致与单边缺失的合计"]]
    add_table(doc, ["差异类型", "占差异总数", "分析含义"], diff_rows, [4.2, 2.6, 9.2],
              font_size=9, align_center_cols={1})
    second_side = "finv 侧" if d2_name == "仅 finv 有值" else "Illion 侧"
    third_side = "finv 侧" if d3_name == "仅 finv 有值" else "Illion 侧"
    p2 = (
        f"{d1_name}占比最高（{fmt_pct(d1_share)}），是最大的差异来源；"
        f"{d2_name}（{fmt_pct(d2_share)}）次之；{d3_name}（{fmt_pct(d3_share)}）再次之。"
        f"前两类合计贡献约 {fmt_pct(d1_share + d2_share)} 的差异。"
        f"需说明的是，双方均为空的交易（占全部交易的 "
        f"{fmt_pct(m.both_empty / m.total_transactions if m.total_transactions else 0)}）未纳入差异分析，"
        f"因其不具备分类可比性。"
        f"综上，{cov_leader} 覆盖略优，但在共同覆盖范围内的一致性较高；"
        f"整体差异主要源于分类标签分歧，其次是 {second_side}的额外识别覆盖，"
        f"{third_side}的独有识别贡献相对较小。"
    )
    add_para(doc, p2, size=9.5)

    # 2.2 业务板块覆盖与一致率（原 2.3）
    add_heading(doc, "2.2 业务板块覆盖与一致率", level=2)
    add_para(doc, "36 个类别划分为收入类（3）、支出类（23）、转账类（2）、负债类（8）。转账类作为中性资金流独立统计，"
                  "不并入收入或支出。板块一致率以「至少一侧识别为该板块」为分母（并集口径），与总体覆盖调整后一致率对应。",
             size=9.5)
    seg_cov_rows = []
    for group in GROUP_ORDER:
        sc = m.segment_coverage(group)
        union_share = sc["union_count"] / m.total_transactions if m.total_transactions else 0
        seg_cov_rows.append([
            group, sc["category_count"], fmt_pct(sc["illion_coverage"]), fmt_pct(sc["finv_coverage"]),
            fmt_pct(sc["exact_rate"]), fmt_pct(sc["broad_rate"]), fmt_pct(union_share),
        ])
    add_table(doc, ["业务板块", "类别数", "Illion 覆盖率", "finv 覆盖率", "一致率（类别并集）", "一致率（同板块）", "板块并集占比"],
              seg_cov_rows, [2.2, 1.4, 2.4, 2.4, 3.0, 3.0, 2.0], font_size=8.5,
              align_center_cols={1, 2, 3, 4, 5, 6})
    add_para(doc, "「一致率（类别并集）」= 双方类别完全一致 / 至少一侧识别为该板块（并集口径）；「一致率（同板块）」= 双方同属该板块"
                  "（类别可以不同）/ 至少一侧识别为该板块，口径更宽松。板块并集占比 = 板块并集 / 全部交易；"
                  "板块并集因跨板块交易存在重叠，各板块一致率不与总体直接加总；"
                  "本表用于板块间横向比较。", size=8.5, color=GRAY)
    seg_cov_map = {g: m.segment_coverage(g) for g in GROUP_ORDER}
    best_g = max(GROUP_ORDER, key=lambda g: seg_cov_map[g]["exact_rate"])
    worst_g = min(GROUP_ORDER, key=lambda g: seg_cov_map[g]["exact_rate"])
    largest_g = max(GROUP_ORDER, key=lambda g: seg_cov_map[g]["union_count"])
    gap_g = max(GROUP_ORDER, key=lambda g: abs(seg_cov_map[g]["finv_coverage"] - seg_cov_map[g]["illion_coverage"]))
    gap_gap = abs(seg_cov_map[gap_g]["finv_coverage"] - seg_cov_map[gap_g]["illion_coverage"])
    worst_union_share = seg_cov_map[worst_g]["union_count"] / m.total_transactions if m.total_transactions else 0
    ws = m.segment_summary(worst_g)
    # 板块核心结论（动态生成：板块角色与全部数字随底稿变化）
    best_broad_max = seg_cov_map[best_g]["broad_rate"] >= max(
        seg_cov_map[g]["broad_rate"] for g in GROUP_ORDER)
    worst_side = ("finv" if seg_cov_map[worst_g]["finv_coverage"] >= seg_cov_map[worst_g]["illion_coverage"]
                  else "Illion")
    worst_other = "Illion" if worst_side == "finv" else "finv"
    worst_side_cov = (seg_cov_map[worst_g]["finv_coverage"] if worst_side == "finv"
                      else seg_cov_map[worst_g]["illion_coverage"])
    worst_other_cov = (seg_cov_map[worst_g]["illion_coverage"] if worst_side == "finv"
                       else seg_cov_map[worst_g]["finv_coverage"])
    worst_only_share = ws["finv_only"] if worst_side == "finv" else ws["illion_only"]
    worst_vol_txt = (
        f"{worst_g}交易体量较小，板块并集仅占全部交易的 {fmt_pct(worst_union_share)}，对整体影响有限"
        if worst_union_share < 0.1 else
        f"{worst_g}交易体量较大（板块并集占全部交易的 {fmt_pct(worst_union_share)}），是优化重点")
    largest_share = m.segment_summary(largest_g)["share"]
    share_max_g = max(GROUP_ORDER, key=lambda g: m.segment_summary(g)["share"])
    share_txt = "最为集中" if share_max_g == largest_g else "较为集中"
    gap_side = ("Illion" if seg_cov_map[gap_g]["illion_coverage"] > seg_cov_map[gap_g]["finv_coverage"]
                else "finv")
    gap_other = "finv" if gap_side == "Illion" else "Illion"
    add_callout(doc,
        f"从板块维度看，各板块表现存在明显分化。"
        f"{best_g}的一致率（类别并集）和一致率（同板块）分别达到 {fmt_pct(seg_cov_map[best_g]['exact_rate'])} 和 "
        f"{fmt_pct(seg_cov_map[best_g]['broad_rate'])}，"
        f"{'在各板块中均为最高' if best_broad_max else '一致率（类别并集）在各板块中最高'}，是两侧共识最强的板块。"
        f"{worst_g}的一致率最低，仅为 {fmt_pct(seg_cov_map[worst_g]['exact_rate'])}，"
        f"但主要由识别范围差异驱动：{worst_side}在该板块的覆盖率为 {fmt_pct(worst_side_cov)}，"
        f"高于 {worst_other} 的 {fmt_pct(worst_other_cov)}，单边识别占该板块差异的 "
        f"{fmt_pct(worst_only_share / ws['total'] if ws['total'] else 0)}——"
        f"一致率偏低反映的是{worst_side}覆盖面更广，而非识别质量缺陷。"
        f"{worst_vol_txt}。"
        f"{largest_g}是覆盖体量最大的板块，板块并集占全部交易的 "
        f"{fmt_pct(seg_cov_map[largest_g]['union_count'] / m.total_transactions if m.total_transactions else 0)}，"
        f"其中 Illion 和 finv 的覆盖率分别为 {fmt_pct(seg_cov_map[largest_g]['illion_coverage'])} 和 "
        f"{fmt_pct(seg_cov_map[largest_g]['finv_coverage'])}；"
        f"同时，{largest_g}的细分差异也{share_txt}，贡献了全部差异的 {fmt_pct(largest_share)}。"
        f"{gap_g}则是{gap_side}覆盖高于{gap_other}的板块，覆盖率相差 {gap_gap * 100:.2f} 个百分点，"
        f"这一差值在各板块中最大，反映出两侧在{gap_g}覆盖口径上存在主要分歧。")

    # 2.3 分类别覆盖与一致率（原 2.4）
    add_heading(doc, "2.3 分类别覆盖与一致率", level=2)
    add_para(doc, "36 个类别按业务板块逐一展示覆盖率和一致率的完整明细表见第 4 章附录 4.1（一致率（类别并集）"
                  "= 交集 / 该类别并集，即双方类别完全一致的比例）；重要类别的详细分析见第 3 章，其余类别统一在第 4 章附录 4.2、4.3 展示核心占比指标汇总。"
                  "红绿灯标记：红字（加粗）= 红灯指标（一致率（类别并集）< 50% 或独有占比 > 30%）；绿字（加粗）= 绿灯指标（一致率（类别并集）> 80%）。",
             size=9.5)
    add_para(doc, "分类别视图先看下方两图：图2.1 为各类别覆盖率对比，图2.2 为分类别一致率分布；"
                  "图后的结论框为分类别核心结论。",
             size=9.5)
    # 图2.1 各类别覆盖率对比热力矩阵（chart_2_2_png；先展示覆盖率，由本文件 render_coverage 渲染）
    if chart_2_2_png is not None and Path(chart_2_2_png).exists():
        pic_p = doc.add_paragraph()
        pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic_p.paragraph_format.space_before = Pt(2)
        pic_p.paragraph_format.space_after = Pt(2)
        pic_run = pic_p.add_run()
        pic_run.add_picture(str(chart_2_2_png), width=Cm(15.5))
        set_run_font_only(pic_run)  # 图片 run 同样显式设为微软雅黑（无文本，仅保持全文档统一）
        add_para(doc, "图2.1 各类别覆盖率对比（左两列：条形长度 = 该侧覆盖率，即该侧识别交易数/全部交易，"
                      "蓝 = Illion、橙 = finv；右列：覆盖率差 = finv − Illion，百分点，橙 = finv 覆盖更广、"
                      "蓝 = Illion 覆盖更广）",
                 size=9, bold=True, color=GRAY, align="center", space_after=8)
    # 图2.2 分类别一致率分布全景（chart_png；覆盖率之后展示一致率，由本文件 render_dotplot 渲染）
    if chart_png is not None and Path(chart_png).exists():
        pic_p = doc.add_paragraph()
        pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic_p.paragraph_format.space_before = Pt(2)
        pic_p.paragraph_format.space_after = Pt(2)
        pic_run = pic_p.add_run()
        pic_run.add_picture(str(chart_png), width=Cm(15.5))
        set_run_font_only(pic_run)  # 图片 run 同样显式设为微软雅黑（无文本，仅保持全文档统一）
        add_para(doc, "图2.2 分类别一致率分布（按业务板块，一致率 = 交集/并集；"
                      "红 = 一致率 < 50%，黄 = 50%–80%，绿 = ≥ 80%）",
                 size=9, bold=True, color=GRAY, align="center", space_after=8)
    # 2.3 类别层核心结论（动态计算，不硬编码；全量明细表已移至第 4 章附录 4.1）
    low_cats = sorted([c for c in m.categories
                       if to_float(c.get("并集数量")) >= 500 and to_float(c.get("交集占比（并集）")) < 0.5],
                      key=lambda c: to_float(c.get("交集占比（并集）")))
    low_top = low_cats[:5]
    low_txt = "；".join(
        f"{c['category']}一致率为 {fmt_pct(c.get('交集占比（并集）'))}，"
        + (f"并集占全部交易的 {fmt_pct(to_float(c.get('并集数量')) / m.total_transactions if m.total_transactions else 0)}"
           if i == 0 else
           f"占 {fmt_pct(to_float(c.get('并集数量')) / m.total_transactions if m.total_transactions else 0)}")
        for i, c in enumerate(low_top))
    high_cats = sorted([c for c in m.categories if to_float(c.get("并集数量")) >= 500],
                       key=lambda c: -to_float(c.get("交集占比（并集）")))
    high_top = high_cats[:3]
    high_names = "、".join(c["category"] for c in high_top[:-1]) + \
                 (f"和{high_top[-1]['category']}" if len(high_top) > 1 else "")
    high_rates = "、".join(fmt_pct(c.get("交集占比（并集）")) for c in high_top[:-1]) + \
                 (f"和{fmt_pct(high_top[-1].get('交集占比（并集）'))}" if len(high_top) > 1 else "")
    zero_cov = [c["category"] for c in m.categories if to_float(c.get("illion覆盖率")) == 0]
    zero_txt = ""
    if zero_cov:
        zero_names = zero_cov[0] if len(zero_cov) == 1 else "、".join(zero_cov[:-1]) + f"和{zero_cov[-1]}"
        zero_txt = f"此外，{zero_names}在Illion侧无覆盖，属于Illion的覆盖缺口。"
    low_num_cn = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五"}.get(len(low_top), str(len(low_top)))
    add_callout(doc,
        f"一致率最低的{low_num_cn}个类别中，{low_txt}。"
        f"一致率最高的三个类别为{high_names}，一致率分别为{high_rates}，"
        f"这些类别多为规则清晰的负债与固定支出类交易。"
        f"{zero_txt}")
    add_page_break(doc)

    # ===== 3. 分业务板块的类别细探 =====
    add_heading(doc, "3. 分业务板块的类别细探", level=1)
    add_para(doc, "本部分回答：每个板块中，哪些类别稳定？哪些类别存在单边覆盖？哪些类别需要进一步排查？"
                  "每个类别统一使用以下七项占比指标：① Illion 覆盖率；② finv 覆盖率；③ 并集占比（占全部交易）；"
                  "④ 交集占比（占全部交易）；⑤ 一致率（类别并集）；⑥ Illion 独有占比；⑦ finv 独有占比。"
                  "其中并集占比 / 交集占比的分母为全部交易，一致率（类别并集）的分母为该类别并集；"
                  "因两侧分类不同的交易会在两个类别的并集中重复计入，类别一致率不可直接加总为总体覆盖调整后一致率"
                  f"（{fmt_pct(m.metrics.get('覆盖调整后一致率', {}).get('result'))}，交易级去重口径）。")

    def categories_in(group: str) -> List[Dict[str, Any]]:
        return [item for item in m.categories if item["group"] == group]

    # 4.1 收入类（结论先行）
    add_heading(doc, "3.1 收入类", level=2)
    add_para(doc, segment_headline(m, "收入类", categories_in("收入类")), size=9.5, space_after=4)
    for i, item in enumerate(categories_in("收入类"), 1):
        render_category_block(doc, item, m, detailed=True, number=f"3.1.{i}")
    add_para(doc, "收入类边界小结", size=9.5, bold=True, space_after=2)
    add_boundary_bullets(doc, [("Wages", "All Other Credits"), ("Wages", "External Transfers"),
                               ("All Other Credits", "External Transfers")], m)
    finv_only_leader = side_only_leader(m, "收入类", "finv")
    add_para(doc, f"收入类差异以 credit 方向为主（收入识别侧），finv 独有收入识别主要落在 {finv_only_leader or '个别类别'}，"
                  "需要抽样核验其交易描述是否符合收入定义。", size=9)
    add_page_break(doc)

    # 4.2 支出类（结论先行；重要类别专项展开，其余见第 4 章附录）
    add_heading(doc, "3.2 支出类", level=2)
    add_para(doc, segment_headline(m, "支出类", categories_in("支出类")), size=9.5, space_after=4)
    add_para(doc, "支出类共 23 个类别，本报告针对重要类别 Rent 与 Gambling 专项展开（含 Top 差异流向、交易方向、"
                  "金额与典型样本），并对红灯/黄灯指标类别中差异体量或代表性较强的 4 个类别（Retail、Information、"
                  "Donations、Automotive）按同样结构展开差异原因专项分析（见 3.2.3–3.2.6）；其余类别的七项指标统一"
                  "汇总于第 4 章附录。",
             size=9.5)

    # 4.2.1 Gambling 专项
    add_heading(doc, "3.2.1 Gambling 专项", level=3)
    expense_items = categories_in("支出类")
    for item in expense_items:
        if item["category"] == "Gambling":
            # 典型样本补充：(空)→Gambling finv 单边识别（Score55 卡支付 + 赌场 ATM 取款），
            # 并入该类别"典型交易样本"表（该流向金额较小，单按金额 Top 5 不会入选）
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
            render_category_block(doc, item, m, detailed=True, number="", extra_samples=extra)
    gambling_item = m.category_by_name.get("Gambling")
    if gambling_item:
        add_para(doc, "Gambling 差异分析总结：", size=9.5, bold=True, space_after=2)
        for text in expense_flag_analysis(m, gambling_item):
            add_para(doc, text, size=9.5, space_after=3)
    add_page_break(doc)

    # 4.2.2 Rent 专项
    add_heading(doc, "3.2.2 Rent 专项", level=3)
    rent = m.category_by_name.get("Rent")
    if rent:
        render_category_block(doc, rent, m, detailed=True, number="")
        add_para(doc, "Rent 与相邻类别的差异流向：", size=9.5, bold=True, space_after=2)
        rent_flows = []
        for other in ["External Transfers", "Internal Transfer", "Utilities", "Home Improvement"]:
            rent_flows.extend(m.flow_between("Rent", other))
        rent_flows = sorted(rent_flows, key=lambda x: x[3], reverse=True)
        if rent_flows:
            add_flow_table(doc, rent_flows, denom=len(m.category_details.get("Rent", [])),
                           share_label="占该类差异")
        else:
            add_para(doc, "底稿中未发现 Rent 与上述类别之间的差异流向。", size=9)
        add_para(doc, "Rent 差异分析总结：", size=9.5, bold=True, space_after=2)
        for text in rent_summary_paragraphs(m, rent):
            add_para(doc, text, size=9.5, space_after=3)
    add_page_break(doc)

    # 4.2.3 支出类红黄灯指标专项分析（Retail / Information / Donations / Automotive）
    # 不设汇总标题：按 Rent / Gambling 同构的完整类别分析逐节展开
    # （类别结论 → 明细（定义/核心指标/解读）→ Top 差异流向 → 方向金额 → 典型样本 → 差异分析总结）
    for flag_i, flag_cat in enumerate(["Retail", "Information", "Donations", "Automotive"], 3):
        flag_item = m.category_by_name.get(flag_cat)
        if not flag_item:
            continue
        add_heading(doc, f"3.2.{flag_i} {flag_cat} 专项", level=3)
        render_category_block(doc, flag_item, m, detailed=True, number="")
        add_para(doc, f"{flag_cat} 差异分析总结：", size=9.5, bold=True, space_after=2)
        for text in expense_flag_analysis(m, flag_item):
            add_para(doc, text, size=9.5, space_after=3)
    add_page_break(doc)

    # 4.3 转账类（结论先行）
    add_heading(doc, "3.3 转账类", level=2)
    add_para(doc, segment_headline(m, "转账类", categories_in("转账类")), size=9.5, space_after=4)
    add_para(doc, "转账类包括 External Transfers 与 Internal Transfer，作为中性资金流处理，不计入收入或支出合计，"
                  "但纳入总体覆盖率、差异率和分类迁移分析。除七项指标外，补充 credit/debit 分布、转入/转出方向，"
                  "以及与 Wages、All Other Credits、Rent 和另一转账类别的边界分析。", size=9.5)
    for i, item in enumerate(categories_in("转账类"), 1):
        render_category_block(doc, item, m, detailed=True, number=f"3.3.{i}")
    add_para(doc, "转账类边界小结", size=9.5, bold=True, space_after=2)
    add_boundary_bullets(doc, [("External Transfers", "Wages"), ("External Transfers", "All Other Credits"),
                               ("External Transfers", "Rent"), ("External Transfers", "Internal Transfer")], m)
    add_para(doc, "转账差异分析总结：", size=9.5, bold=True, space_after=2)
    for text in transfer_summary_paragraphs(m):
        add_para(doc, text, size=9.5, space_after=3)
    add_para(doc, "转账与收入（Wages / All Other Credits）的差异集中在 credit 方向，"
             "说明「收款入账」场景下转账与收入类别边界是主要混淆点；与 Rent 的差异则出现在 debit 方向，"
             "提示租金支付被识别为转账的可能性。", size=9)
    add_callout(doc, "转账类处理原则：作为中性资金流，不并入收入或支出合计，也不计入净收入计算；"
                     "但纳入总体覆盖率、差异率和分类迁移分析。")
    add_page_break(doc)

    # 4.4 负债类（结论先行；重要类别展开，其余见第 4 章附录）
    add_heading(doc, "3.4 负债类", level=2)
    add_para(doc, segment_headline(m, "负债类", categories_in("负债类")), size=9.5, space_after=4)
    add_para(doc, "负债类共 8 个类别，本报告针对重要类别 SACC Loans、Non SACC Loans、Dishonours、"
                  "Credit Card Repayments 展开分析（含 Top 差异流向、交易方向、金额与典型样本）；"
                  "其余 4 个类别（Debt Collection、Overdrawn、Debt Consolidation、Unknown Loans）的七项指标"
                  "统一汇总于第 4 章附录。贷款生命周期、Counterparty matching、Dishonours 详细案例、"
                  "Unknown Loans 完整根因和 SACC / Non-SACC 深度交叉矩阵以 Liability 专项报告为准。", size=9.5)
    liab_i = 1
    for item in categories_in("负债类"):
        if item["category"] in IMPORTANT_LIABILITY:
            render_category_block(doc, item, m, detailed=True, number=f"3.4.{liab_i}")
            liab_i += 1
    add_para(doc, "负债板块差异排名与 Top 差异流向：", size=9.5, bold=True, space_after=2)
    liab_flows = m.segment_top_flows("负债类", limit=8)
    add_flow_table(doc, liab_flows, denom=m.segment_summary("负债类")["total"], share_label="占板块差异")
    add_callout(doc, "负债类的详细根因分析（贷款生命周期、Counterparty matching、Dishonours 案例、Unknown Loans 根因）"
                     "以已有 Liability 专项报告为准，本报告仅保留重要负债类别的展开分析、板块差异排名与类别侧定位。")
    add_page_break(doc)

    # 4.5 跨板块优化建议
    add_heading(doc, "3.5 跨板块优化建议", level=2)
    add_para(doc, "综合全景差异结构与类别细探，建议按以下优先级推进优化：", size=9.5)

    def flow_ref(illion: str, finv: str) -> str:
        count = sum(stat["count"] for (il, fv, _), stat in m.flow_stats.items() if il == illion and fv == finv)
        if not count:
            return f"{illion} → {finv or '（空）'}"
        share = fmt_pct(count / m.diff_total if m.diff_total else 0)
        return f"{illion} → {finv or '（空）'}（{share}）"

    add_table(doc, ["优先级", "观察方向", "建议动作", "数据依据"],
              [
                  ["P1", "验证 finv 新增识别", f"抽样核验仅 finv 有值分类占比（{fmt_pct(m.finv_only_total / m.diff_total if m.diff_total else 0)}）是否合理，区分覆盖扩展与归类分歧",
                   "All Other Credits、Retail、Dining Out、Unknown Loans"],
                  ["P1", "优化转账边界", "拆分 credit/debit 处理，明确转账与收入（Wages / All Other Credits）的识别优先级",
                   flow_ref("External Transfers", "Internal Transfer") + "、" + flow_ref("External Transfers", "All Other Credits")],
                  ["P1", "优化贷款类别边界", "核对 Non SACC / SACC Loans 与 Unknown Loans 的知识库与别名",
                   flow_ref("Non SACC Loans", "Unknown Loans") + "、" + flow_ref("SACC Loans", "Unknown Loans")],
                  ["P2", "优化 Rent 边界", "核对 Rent 与转账、Utilities、Home Improvement 的规则边界，建立 Rent 回归集",
                   flow_ref("External Transfers", "Rent") + " 等流向"],
                  ["P2", "优化高频消费边界", "针对 Groceries、Dining Out、Retail、Automotive、Gambling 建立规则回归集",
                   flow_ref("Groceries", "Dining Out") + "、" + flow_ref("Groceries", "Retail") + "、" + flow_ref("Groceries", "Automotive")],
                  ["P3", "其余类别复核", "低并集占比类别的比例指标结合样本量解读，重点核对占比指标异常的类别（见第 4 章附录）",
                   "附录各类别核心占比指标"],
              ],
              [1.4, 3.2, 6.4, 6.0], font_size=8.5, align_center_cols={0})
    add_para(doc, "后续迭代建议：每次模型或知识库更新后，重新计算 36 个类别的核心占比指标，并重点监控一致率（类别并集）、"
                  "并集占比、finv 独有占比、Illion 独有占比以及 Rent 和转账类的差异流向变化。", size=9.5)


    add_page_break(doc)

    # ===== 4. 附录：分类别指标明细与汇总 =====
    add_heading(doc, "4. 附录：分类别指标明细与汇总", level=1)
    add_para(doc, "4.1 为 36 个类别的七项指标全量明细；4.2 为支出类中除 Rent、Gambling 专项外的其余 21 个类别"
                  "（其中 Retail、Information、Donations、Automotive 已在 3.2.3–3.2.6 专项展开，此处保留指标汇总与建议优先级）；"
                  "4.3 为负债类其余 4 个类别。三节均用于快速排查与规则核对，指标口径与第 2 章、第 3 章完全一致。", size=9.5)

    # 4.1 分类别指标全量明细（自 2.3 正文移入附录）
    add_para(doc, "4.1 分类别指标全量明细（36 个类别）", size=10, bold=True, space_after=2)
    for group in GROUP_ORDER:
        add_para(doc, f"{group}：", size=9.5, bold=True, space_after=2)
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
                str(item.get("建议优先级") or "").strip() or "-",
            ])
        add_table(doc, ["类别", "Illion 覆盖率", "finv 覆盖率", "一致率（类别并集）",
                        "Illion独有占比", "finv独有占比", "建议优先级"],
                  cat_rows, [3.4, 2.0, 2.0, 2.4, 2.4, 2.4, 1.6], font_size=8,
                  align_center_cols={1, 2, 3, 4, 5},
                  highlights=indicator_highlights(group_cats, col_inter=3, col_il=4, col_fv=5))

    def appendix_table(group: str, excluded: set, title: str) -> None:
        add_para(doc, title, size=10, bold=True, space_after=2)
        rows = []
        items = []
        for item in categories_in(group):
            if item["category"] not in excluded:
                items.append(item)
                rows.append([
                    item["category"],
                    fmt_pct(item.get("illion覆盖率")), fmt_pct(item.get("finv覆盖率")),
                    fmt_pct(item.get("交集占比（并集）")),
                    fmt_pct(item.get("illion独有占比（并集）")), fmt_pct(item.get("finv独有占比（并集）")),
                    str(item.get("建议优先级") or "").strip() or "-",
                ])
        add_table(doc,
                  ["类别", "Illion覆盖率", "finv覆盖率", "一致率（类别并集）",
                   "Illion独有占比", "finv独有占比", "建议优先级"],
                  rows, [2.8, 2.0, 2.0, 2.4, 2.4, 2.4, 1.6], font_size=8,
                  align_center_cols={1, 2, 3, 4, 5},
                  highlights=indicator_highlights(items, col_inter=3, col_il=4, col_fv=5))

    appendix_table("支出类", IMPORTANT_EXPENSE, "4.2 支出类其余类别（21 个）")
    appendix_table("负债类", IMPORTANT_LIABILITY, "4.3 负债类其余类别（4 个）")
    add_para(doc, "附录口径说明：并集占比（占全部交易）= 该类别的并集交易 / 全部交易；一致率（类别并集）= 交集 / 该类别并集；"
                  "Illion 独有占比 / finv 独有占比 = 单侧识别的差异交易占该类别的比例。"
                  "各节「差异流向」表的占比列以该表语境为分母（类别明细 = 该类别相关差异；负债板块表 = 该板块相关差异），"
                  "即该类/板块内部差异构成，非全部差异。"
                  "两侧分类不同的交易会在两个类别的并集中重复计入，故类别一致率不可直接加总为总体覆盖调整后一致率"
                  f"（{fmt_pct(m.metrics.get('覆盖调整后一致率', {}).get('result'))}，总体按交易级去重）。"
                  "建议优先级来自底稿「建议优先级」列，缺失时以「-」表示。"
                  "红绿灯标记：红字（加粗）= 红灯指标（一致率（类别并集）< 50% 或独有占比 > 30%）；绿字（加粗）= 绿灯指标（一致率（类别并集）> 80%）。",
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
        print(f"[warn] 图2.1 覆盖率渲染失败，报告将不含该图：{exc}")
        coverage_png = None

    dotplot_png: Optional[Path] = out_dir / "md_chart_2_2_dotplot.png"
    try:
        segment_avg = {g: an.segment_coverage(g)["exact_rate"] for g in GROUP_ORDER}
        overall_avg = to_float(an.metrics.get("覆盖调整后一致率", {}).get("result"))
        render_dotplot(dotplot_png, an.categories, "b", segment_avg, overall_avg,
                       input_name=xlsx.name)
    except Exception as exc:
        print(f"[warn] 图2.2 一致率渲染失败，报告将不含该图：{exc}")
        dotplot_png = None

    return dotplot_png, coverage_png
def main() -> None:
    ap = argparse.ArgumentParser(
        description="从数据底稿生成 Markdown 版类别差异报告（自包含单文件）"
    )
    ap.add_argument("--input", default=str(paths.default_review_report()),
                    help="数据底稿 xlsx 路径")
    ap.add_argument("--output", default=str(paths.report_output_dir() / DEFAULT_OUTPUT_NAME),
                    help="输出 md 路径")
    ap.add_argument("--samples-per-category", type=int, default=5, help="每个类别保留的样本条数")
    ap.add_argument("--check", action="store_true", help="只做口径校验，不写文件")
    ap.add_argument("--no-charts", action="store_true", help="不渲染并嵌入图表")
    args = ap.parse_args()

    xlsx = Path(args.input)
    if not xlsx.exists():
        sys.exit(f"未找到数据底稿：{xlsx}")
    print(f"读取底稿：{xlsx}（{xlsx.stat().st_size / 1024 / 1024:.0f} MB）…", flush=True)

    wb = load_workbook(xlsx, read_only=True, data_only=True)
    try:
        metrics = read_metrics(wb[SHEET_00])
        categories = read_categories(wb[SHEET_00])
        top_flows = read_top_flows(wb[SHEET_01])
        matrix = read_matrix(wb[SHEET_01])
        generated_date = read_generated_date(wb[SHEET_00])
    finally:
        wb.close()

    print(f"  {SHEET_00}：{len(categories)} 个类别、{len(metrics)} 项总体指标")
    print(f"  {SHEET_01}：{len(top_flows)} 条 Top 流向、类别矩阵 {len(matrix)} 格")

    detail_rows = read_row_count(xlsx, SHEET_03)
    print(f"流式读取 {SHEET_03} 样本（全表 {detail_rows:,} 行）…", flush=True)
    samples = read_samples(xlsx, per_category=args.samples_per_category)
    with_samples = [c for c, v in samples.items() if v]
    print(f"  命中 {len(with_samples)} 个类别的样本：{'、'.join(sorted(with_samples))}")

    an = MdAnalysisFull(metrics, categories, top_flows, matrix, samples, generated_date,
                        detail_rows=detail_rows)

    print()
    print("=== 口径校验 ===")
    problems = an.reconcile()
    print(f"[1] 矩阵 ↔ {SHEET_00}：{'全部通过' if not problems else f'{len(problems)} 项失败'}")
    for p in problems:
        print("    [失败] " + p)

    problems_02 = an.reconcile_02(xlsx)
    print(f"[2] 类别矩阵聚合 ↔ {SHEET_02}："
          f"{'全部通过' if not problems_02 else f'{len(problems_02)} 项失败'}")
    for p in problems_02:
        print("    [失败] " + p)

    if problems or problems_02:
        sys.exit("\n口径校验未通过，已中止（不输出报告）。")

    if args.check:
        print("\n--check 模式：未写文件。")
        return

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    print()
    print(f"渲染图表…（{'跳过' if args.no_charts else '可用 --no-charts 跳过'}）")
    dotplot_png, coverage_png = render_charts(an, xlsx, out.parent, not args.no_charts)

    doc = MdDoc(out)
    doc.source_note = (
        f"统计口径说明：本报告的全部统计数字来自 {SHEET_00}、{SHEET_01}、{SHEET_02} 三张表；"
        f"{SHEET_03} 仅用于挑选「典型交易样本」（每个类别取金额最大的 "
        f"{args.samples_per_category} 条），不作任何分母或计数。"
        f"因此文中标注为「差异样本」的占比描述的是样本构成，不代表全量差异分布。"
    )

    # 正文逻辑与 docx 版同源：这里的 add_* / fmt_money / render_category_block
    # 就是上面定义的 Markdown 实现，build_report 里的裸名调用直接解析到它们。
    build_report(doc, an, dotplot_png, coverage_png,
                 input_name=xlsx.name, generated_date=generated_date)

    if out.exists():
        try:
            out.unlink()
        except PermissionError:
            sys.exit(f"输出文件被占用，无法覆盖：{out}（请先关闭）")
    out.write_text(doc.render(), encoding="utf-8", newline="\n")
    print(f"\n已生成：{out}（{out.stat().st_size / 1024:.0f} KB，"
          f"{len(doc.headings)} 个标题）")


if __name__ == "__main__":
    main()
