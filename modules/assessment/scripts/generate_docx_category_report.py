"""根据数据底稿生成 BS-CAT 全分类类别表现分析报告 (docx)。

数据来源: reviews/<最新>/label_compare_report.xlsx（--input 可指定其他底稿）
报告结构:
  1. 执行摘要
  2. 覆盖与一致全景 (2.1 总体覆盖、一致率与差异性质 / 2.2 业务板块 / 2.3 分类别)
  3. 分业务板块的类别细探 (3.1 收入类 / 3.2 支出类(含 Gambling 专项与 Rent 专项) / 3.3 转账类 / 3.4 负债类 / 3.5 跨板块优化建议)
  4. 附录：分类别指标明细与汇总 (4.1 分类别指标全量明细 / 4.2 支出类其余类别 / 4.3 负债类其余类别)

所有数字均从底稿动态计算，不硬编码。
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from openpyxl import load_workbook
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

import paths

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_NAME = "category_difference_report_100_zh.docx"

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
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return ""


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


def read_details(ws: Any) -> List[Dict[str, Any]]:
    """03_排查明细 第 4 行起：逐笔差异明细。

    列位置按表头名称动态解析（底稿列结构调整后仍可正确读取，不依赖固定列号）。
    """
    headers: List[str] = []
    for r in ws.iter_rows(min_row=1, max_row=5, values_only=True):
        vals = [str(v or "") for v in r]
        if "illion Category" in vals:
            headers = vals
            break
    if not headers:
        raise ValueError("03_排查明细 未找到含「illion Category」的表头行")

    def col(name: str) -> int:
        return headers.index(name)

    i_priority = col("排查优先级")
    i_diff = col("排查类型")
    i_date = col("transaction_date")
    i_amount = col("amount")
    i_drcr = col("dr_cr")
    i_text = col("text")
    i_third = col("third_party")
    i_cp = col("counterparty")
    i_il = col("illion Category")
    i_fv = col("finv Category")
    i_uid = col("user_id") if "user_id" in headers else None
    i_app = col("application_id") if "application_id" in headers else None

    details: List[Dict[str, Any]] = []
    for row in ws.iter_rows(min_row=4, max_row=ws.max_row, values_only=True):
        if not any(v not in (None, "") for v in row):
            continue
        details.append({
            "priority": str(row[i_priority] or "").strip(),
            "diff_type": diff_type_label(row[i_diff]),
            "amount": to_float(row[i_amount]),
            "dr_cr": str(row[i_drcr] or "").strip(),
            "text": str(row[i_text] or "").strip(),
            "third_party": str(row[i_third] or "").strip(),
            "counterparty": str(row[i_cp] or "").strip(),
            "illion_category": norm_cat(row[i_il]),
            "finv_category": norm_cat(row[i_fv]),
            "user_id": str(row[i_uid] or "").strip() if i_uid is not None else "",
            "application_id": str(row[i_app] or "").strip() if i_app is not None else "",
            "transaction_date": str(row[i_date] or "").strip(),
        })
    return details


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------
class Analysis:
    def __init__(self, metrics: Dict[str, Dict[str, Any]], categories: List[Dict[str, Any]],
                 top_flows: List[Dict[str, Any]], details: List[Dict[str, Any]]):
        self.metrics = metrics
        self.categories = categories
        self.top_flows = top_flows
        self.details = details

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
        # 至少一侧有分类 = 双方非空 + 仅 Illion + 仅 finv（与底稿"联合非空样本"口径一致）
        self.union_nonempty = self.joint_nonempty + self.illion_only_total + self.finv_only_total

        self.category_by_name = {item["category"]: item for item in categories}
        self._build_detail_aggregates()

    # -- 明细聚合 ----------------------------------------------------------
    def _build_detail_aggregates(self) -> None:
        # 流向统计: (illion, finv, diff_type) -> {count, amount}
        self.flow_stats: Dict[Tuple[str, str, str], Dict[str, float]] = defaultdict(lambda: {"count": 0, "amount": 0.0})
        # 类别涉及明细: category -> list of detail rows (任一命中)
        self.category_details: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        # 板块矩阵: (illion 板块 or 'Illion为空', finv 板块 or 'finv为空') -> count
        self.matrix: Dict[Tuple[str, str], int] = defaultdict(int)

        for detail in self.details:
            il, fv = detail["illion_category"], detail["finv_category"]
            key = (il, fv, detail["diff_type"])
            self.flow_stats[key]["count"] += 1
            self.flow_stats[key]["amount"] += detail["amount"]
            for cat in (il, fv):
                if cat:
                    self.category_details[cat].append(detail)
            il_group = GROUP_OF.get(il, "Illion为空") if il else "Illion为空"
            fv_group = GROUP_OF.get(fv, "finv为空") if fv else "finv为空"
            self.matrix[(il_group, fv_group)] += 1

    # -- 板块口径 ----------------------------------------------------------
    def segment_summary(self, group: str) -> Dict[str, Any]:
        """板块差异汇总：差异交易数 / 占比 / 板块内不一致 / 跨板块不一致 / 单边缺失。"""
        within = cross = illion_only = finv_only = 0
        for detail in self.details:
            il, fv = detail["illion_category"], detail["finv_category"]
            il_group = GROUP_OF.get(il) if il else ""
            fv_group = GROUP_OF.get(fv) if fv else ""
            hit = (il_group == group) or (fv_group == group)
            if not hit:
                continue
            if il and fv:
                if il_group == fv_group:
                    within += 1
                else:
                    cross += 1
            elif il:
                illion_only += 1
            else:
                finv_only += 1
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
        """板块级覆盖与一致率（口径与总体对齐：一致率 = 交集 / 板块并集）。

        板块并集 = sum(类别并集) - 板块内不一致笔数：
        双方类别不同但同属该板块的交易（如 External Transfers → Internal Transfer）
        会被计入两个类别的并集各一次，板块并集只计一次。
        """
        cats = [item for item in self.categories if item["group"] == group]
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

    def segment_top_flows(self, group: str, limit: int = 3) -> List[Tuple[str, str, str, int, float]]:
        """板块内 Top 差异流向：任一侧类别属于该板块的流向，按数量降序。"""
        group_cats = set(GROUPS[group])
        flows = []
        for (il, fv, diff_type), stat in self.flow_stats.items():
            if il in group_cats or fv in group_cats:
                flows.append((il, fv, diff_type, stat["count"], stat["amount"]))
        flows.sort(key=lambda x: x[3], reverse=True)
        return flows[:limit]

    # -- 类别口径 ----------------------------------------------------------
    def category_flows(self, category: str, limit: int = 5) -> List[Tuple[str, str, str, int, float]]:
        """类别涉及的 Top 差异流向（含金额）。"""
        flows = []
        for (il, fv, diff_type), stat in self.flow_stats.items():
            if category in (il, fv):
                flows.append((il, fv, diff_type, stat["count"], stat["amount"]))
        flows.sort(key=lambda x: x[3], reverse=True)
        return flows[:limit]

    def flow_between(self, cat_a: str, cat_b: str) -> List[Tuple[str, str, str, int, float]]:
        """两类别之间的双向流向。"""
        result = []
        for (il, fv, diff_type), stat in self.flow_stats.items():
            if {il, fv} == {cat_a, cat_b}:
                result.append((il, fv, diff_type, stat["count"], stat["amount"]))
        result.sort(key=lambda x: x[3], reverse=True)
        return result

    def dr_cr_distribution(self, category: str) -> Tuple[int, int]:
        """类别涉及明细的 credit / debit 分布。"""
        credit = debit = 0
        for detail in self.category_details.get(category, []):
            if detail["dr_cr"] == "credit":
                credit += 1
            elif detail["dr_cr"] == "debit":
                debit += 1
        return credit, debit

    def top_samples(self, category: str, limit: int = 5) -> List[Dict[str, Any]]:
        rows = self.category_details.get(category, [])
        rows = sorted(rows, key=lambda d: abs(d["amount"]), reverse=True)
        return rows[:limit]

    def top_samples_in_flow(self, category: str, flow: Tuple[str, str], limit: int = 1) -> List[Dict[str, Any]]:
        """类别特定差异流向内的样本：两侧分类与该流向 (illion, finv) 完全一致，按金额绝对值降序。

        用于结论行的样本佐证：样本与主流向方向天然一致，避免金额最大但与结论不符的样本。
        """
        il_t, fv_t = flow
        rows = [d for d in self.category_details.get(category, [])
                if d["illion_category"] == il_t and d["finv_category"] == fv_t]
        rows = sorted(rows, key=lambda d: abs(d["amount"]), reverse=True)
        return rows[:limit]


# ---------------------------------------------------------------------------
# docx 渲染工具
# ---------------------------------------------------------------------------
def set_run_font(run: Any, size: float, bold: bool = False, color: str = DARK, font: str = "微软雅黑") -> None:
    run.font.name = font  # 设置 w:ascii / w:hAnsi
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), font)
    rfonts.set(qn("w:cs"), font)


def set_run_font_only(run: Any, font: str = "微软雅黑") -> None:
    """仅设置 run 字体（ascii/hAnsi/eastAsia/cs），不动字号、加粗、颜色（用于继承样式字号的标题等）。"""
    run.font.name = font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), font)


def set_doc_defaults_font(doc: Document, font: str = "微软雅黑") -> None:
    """styles.xml 的 docDefaults -> rPrDefault -> rPr -> rFonts 全局默认字体设为微软雅黑（兜底无显式字体的 run）。"""
    styles_el = doc.styles.element
    dd = styles_el.find(qn("w:docDefaults"))
    if dd is None:
        dd = OxmlElement("w:docDefaults")
        styles_el.insert(0, dd)
    rpr_default = dd.find(qn("w:rPrDefault"))
    if rpr_default is None:
        rpr_default = OxmlElement("w:rPrDefault")
        dd.append(rpr_default)
    rpr = rpr_default.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        rpr_default.append(rpr)
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rf.set(qn(attr), font)


def init_document(input_name: str) -> Document:
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.0)

    # 全局兜底：docDefaults 默认字体 = 微软雅黑（未显式设置字体的 run 一律继承）
    set_doc_defaults_font(doc)

    # 默认字体（Normal 样式，四个字体槽位全部设为微软雅黑）
    normal = doc.styles["Normal"]
    normal.font.name = "微软雅黑"
    normal.font.size = Pt(10)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        normal._element.rPr.rFonts.set(qn(attr), "微软雅黑")

    # 标题样式（Heading 1/2/3，四个字体槽位全部设为微软雅黑）
    for level, size, color in ((1, 16, BLUE), (2, 13, BLUE_MID), (3, 11, BLUE)):
        style = doc.styles[f"Heading {level}"]
        style.font.name = "微软雅黑"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            style._element.rPr.rFonts.set(qn(attr), "微软雅黑")

    # 页脚：数据来源 + 页码
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"BS-CAT 全分类类别表现分析报告 | 数据来源: {input_name} | 第 ")
    set_run_font(run, 8, color=GRAY)
    page_run = p.add_run()
    set_run_font(page_run, 8, color=GRAY)
    fld_begin = OxmlElement("w:fldChar"); fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve"); instr.text = "PAGE"
    fld_end = OxmlElement("w:fldChar"); fld_end.set(qn("w:fldCharType"), "end")
    page_run._r.append(fld_begin); page_run._r.append(instr); page_run._r.append(fld_end)
    tail = p.add_run(" 页")
    set_run_font(tail, 8, color=GRAY)
    return doc


def add_heading(doc: Document, text: str, level: int = 1) -> None:
    p = doc.add_heading(text, level=level)
    for r in p.runs:
        set_run_font_only(r)  # 标题 run 显式指定微软雅黑（字号/加粗/颜色继承 Heading 样式）


def add_page_break(doc: Document) -> None:
    """新增含分页符的段落（分页 run 同样显式设为微软雅黑，保持全文档字体统一）。"""
    p = doc.add_paragraph()
    run = p.add_run()
    run.add_break(WD_BREAK.PAGE)
    set_run_font_only(run)


def add_para(doc: Document, text: str, size: float = 10, bold: bool = False, color: str = DARK,
             align: str = "left", space_after: float = 6) -> None:
    p = doc.add_paragraph()
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "justify":
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(space_after)
    run = p.add_run(text)
    set_run_font(run, size, bold=bold, color=color)
    return p


def add_para_labeled(doc: Document, label: str, text: str, size: float = 9.5,
                     space_after: float = 3) -> None:
    """加粗标签 + 普通文本同段（结论先行结构的明细项，如「类别定义：…」）。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    r1 = p.add_run(f"{label}：")
    set_run_font(r1, size, bold=True)
    r2 = p.add_run(text)
    set_run_font(r2, size)
    return p


def set_cell_shading(cell: Any, hex_color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def set_cell_margins(cell: Any, top: int = 40, bottom: int = 40, left: int = 80, right: int = 80) -> None:
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.find(qn("w:tcMar"))
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for margin, value in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        el = tc_mar.find(qn(f"w:{margin}"))
        if el is None:
            el = OxmlElement(f"w:{margin}")
            tc_mar.append(el)
        el.set(qn("w:w"), str(value))
        el.set(qn("w:type"), "dxa")


def repeat_header_row(row: Any) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)


def add_table(doc: Document, headers: List[str], rows: List[List[Any]],
              col_widths: Optional[List[float]] = None, font_size: float = 8.5,
              header_color: str = BLUE, align_center_cols: Optional[set] = None,
              zebra: bool = True,
              highlights: Optional[Dict[Tuple[int, int], str]] = None,
              fills: Optional[Dict[Tuple[int, int], str]] = None) -> None:
    """通用表格：蓝底表头、网格线、斑马纹、表头跨页重复。宽度单位 cm。
    highlights: {(数据行号, 列号): 颜色十六进制}，命中的单元格以该颜色加粗显示（红绿灯标记）。
    fills: {(数据行号, 列号): 底色十六进制}，命中的单元格设置底色（覆盖斑马纹，用于发散色块等）。"""
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    width_cm = col_widths or [16.0 / len(headers)] * len(headers)

    def fill_cell(cell: Any, value: Any, header: bool, center: bool) -> None:
        text = "" if value is None else str(value)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        set_cell_margins(cell)
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER if (center or header) else WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        set_run_font(run, font_size, bold=header, color="FFFFFF" if header else DARK)

    align_center_cols = align_center_cols or set()
    header_row = table.rows[0]
    repeat_header_row(header_row)
    for j, (header, width) in enumerate(zip(headers, width_cm)):
        fill_cell(header_row.cells[j], header, True, True)
        set_cell_shading(header_row.cells[j], header_color)
        header_row.cells[j].width = Cm(width)

    for i, row in enumerate(rows):
        cells = table.add_row().cells
        for j, (value, width) in enumerate(zip(row, width_cm)):
            fill_cell(cells[j], value, False, j in align_center_cols)
            cells[j].width = Cm(width)
            if zebra and i % 2 == 1:
                set_cell_shading(cells[j], GRAY_PALE)
            fill_color = (fills or {}).get((i, j))
            if fill_color:
                set_cell_shading(cells[j], fill_color)
            hl_color = (highlights or {}).get((i, j))
            if hl_color:
                run = cells[j].paragraphs[0].runs[0]
                set_run_font(run, font_size, bold=True, color=hl_color)
    return table


def add_callout(doc: Document, text: str, fill: str = YELLOW_PALE, edge: str = "C98200",
                size: float = 9.5) -> None:
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    table.autofit = False
    cell = table.rows[0].cells[0]
    cell.width = Cm(17.0)
    set_cell_shading(cell, fill)
    set_cell_margins(cell, top=80, bottom=80, left=120, right=120)
    # 左侧强调色条
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    left_border = OxmlElement("w:left")
    left_border.set(qn("w:val"), "single")
    left_border.set(qn("w:sz"), "24")
    left_border.set(qn("w:color"), edge)
    borders.append(left_border)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run(text)
    set_run_font(run, size, color=DARK)


def add_flow_table(doc: Document, flows: List[Tuple[str, str, str, int, float]], title: str = "",
                   diff_total: float = 0) -> None:
    if title:
        add_para(doc, title, size=9.5, bold=True, space_after=3)
    rows = []
    for index, (il, fv, diff_type, count, amount) in enumerate(flows, 1):
        rows.append([
            index,
            il or "（空）",
            fv or "（空）",
            diff_type,
            fmt_pct(count / diff_total if diff_total else 0),
            fmt_money(amount),
        ])
    add_table(doc, ["排名", "Illion 类别", "finv 类别", "差异类型", "占差异总数", "差异金额"],
              rows, [1.1, 3.6, 3.6, 2.6, 2.3, 2.8], font_size=8.5, align_center_cols={0, 4, 5})


def add_samples_table(doc: Document, samples: List[Dict[str, Any]]) -> None:
    rows = []
    for s in samples:
        flow = f"{s['illion_category'] or '（空）'} → {s['finv_category'] or '（空）'}"
        cp = (s.get("counterparty", "") or "").strip()
        cp = "" if cp == "-" else cp
        rows.append([
            s.get("transaction_date", ""),
            (s.get("text", "") or "")[:30],
            cp[:14] or "（空）",
            s.get("dr_cr", ""),
            fmt_money(s.get("amount")),
            flow,
        ])
    if rows:
        add_table(doc, ["日期", "交易描述", "对手方", "方向", "金额", "Illion → finv"],
                  rows, [2.2, 4.8, 3.6, 1.5, 2.4, 2.5], font_size=8, align_center_cols={0, 3, 4})


# ---------------------------------------------------------------------------
# 文字生成
# ---------------------------------------------------------------------------
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
        sample_txt = (f"典型样本「{(s.get('text') or '')[:30]}」两侧分类为 "
                      f"{s['illion_category'] or '（空）'} → {s['finv_category'] or '（空）'}（{fmt_money(s.get('amount'))}），"
                      f"与上述判断一致")
    if inter_share >= 0.9:
        return f"{category}的{rate_txt}。{flow_txt}。{sample_txt}。" if flow_txt else f"{category}的{rate_txt}。{sample_txt}。"
    if finv_only_share > illion_only_share + 0.05:
        core = f"核心矛盾是 finv 对「{category}」识别范围更广"
    elif illion_only_share > finv_only_share + 0.05:
        core = f"核心矛盾是 Illion 对「{category}」识别范围更广"
    else:
        core = f"核心矛盾是两侧对「{category}」的分类边界不一致"
    return f"{category}的{rate_txt}，{core}，{flow_txt}。{sample_txt}。" if flow_txt \
        else f"{category}的{rate_txt}，{core}。{sample_txt}。"


def segment_headline(analysis: Analysis, group: str, items: List[Dict[str, Any]]) -> str:
    """板块总体结论（结论先行）：主要差异类别 + 主导侧 + 板块外边界 + 共识强类别。"""
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


def add_boundary_bullets(doc: Document, pairs: List[Tuple[str, str]], analysis: Analysis) -> None:
    """边界小结（结论先行）：每对类别一行，金额动态。"""
    for a, b in pairs:
        flows = analysis.flow_between(a, b)
        if not flows:
            continue
        if len(flows) == 1:
            il, fv, dt, cnt, amount = flows[0]
            add_para(doc, f"{a} 与 {b}：{il or '（空）'} → {fv or '（空）'} 差异金额 {fmt_money(amount)}。",
                     size=9.5, space_after=2)
        else:
            amounts = " 和 ".join(fmt_money(f[4]) for f in sorted(flows, key=lambda x: -x[4]))
            add_para(doc, f"{a} 与 {b}：双向混淆，金额分别为 {amounts}。",
                     size=9.5, space_after=2)


def rent_summary_paragraphs(analysis: Analysis, rent: Dict[str, Any]) -> List[str]:
    """Rent 专项差异分析总结（基于底稿实际数据动态生成，无硬编码数字）。

    覆盖：一致率与体量、交易方向、单边识别偏向、差异流向集中度与主因判断。
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
    finv_only_n = sum(f[3] for f in all_flows if f[1] == "Rent" and f[0] == "")
    illion_only_n = sum(f[3] for f in all_flows if f[0] == "Rent" and f[1] == "")

    paras: List[str] = []

    # 段落 1：一致率、体量、方向、单边偏向
    p1 = f"Rent 类别一致率（类别并集）为 {fmt_pct(inter_share)}"
    if union and total:
        p1 += f"，并集占全部交易的 {fmt_pct(union / total)}"
    p1 += "。"
    if detail_n:
        p1 += f"Rent 相关差异占全部差异的 {fmt_pct(detail_n / diff_total) if diff_total else ''}，"
        credit, debit = analysis.dr_cr_distribution("Rent")
        if credit + debit:
            dr_share = debit / (credit + debit)
            if dr_share >= 0.5:
                p1 += f"方向以 debit（转出/借方）为主，占 {fmt_pct(dr_share)}，与租金支出场景一致"
            else:
                p1 += f"方向以 credit（转入/贷方）为主，占 {fmt_pct(1 - dr_share)}"
            p1 += "。"
        else:
            p1 += "差异样本未记录交易方向。"
    if finv_only_share or illion_only_share:
        if finv_only_share > illion_only_share * 2:
            p1 += (f"单边差异偏向 finv 一侧：finv 独有识别占比 {fmt_pct(finv_only_share)}"
                   f"，显著高于 Illion 独有占比 {fmt_pct(illion_only_share)}，存在未被 Illion 识别出的租金交易。")
        elif illion_only_share > finv_only_share * 2:
            p1 += (f"单边差异偏向 Illion 一侧：Illion 独有识别占比 {fmt_pct(illion_only_share)}"
                   f"，显著高于 finv 独有占比 {fmt_pct(finv_only_share)}。")
        else:
            p1 += (f"单边差异两侧接近（Illion 独有 {fmt_pct(illion_only_share)}，"
                   f"finv 独有 {fmt_pct(finv_only_share)}）。")
    paras.append(p1)

    # 段落 2：差异流向集中度与主因判断（占比+金额口径）
    if not all_flows or not detail_n:
        paras.append("底稿中未发现 Rent 相关差异明细，Rent 两侧识别结果一致，无需针对性调整。")
        return paras
    top = all_flows[0]
    p2 = (f"Rent 相关差异流向以 {top[0] or '（空）'} → {top[1] or '（空）'} 为主"
          f"（占 Rent 相关差异的 {fmt_pct(top[3] / detail_n)}，涉及金额 {fmt_money(top[4])}）")
    extras = []
    if trans_in:
        extras.append(f"转账类流入（Internal Transfer / External Transfers → Rent）合计占 Rent 相关差异的 {fmt_pct(trans_in / detail_n)}")
    if finv_only_n:
        extras.append(f"finv 单边识别（仅 finv 有值 → Rent）占 {fmt_pct(finv_only_n / detail_n)}")
    if illion_only_n:
        extras.append(f"Illion 单边识别（Rent → 仅 Illion 有值）占 {fmt_pct(illion_only_n / detail_n)}")
    if extras:
        p2 += "；" + "；".join(extras)
    p2 += "。"
    # 主因判断（按数据占比触发，阈值与各章口径一致）
    if trans_in >= detail_n * 0.3:
        p2 += (f"综合来看，Rent 差异的主因是租金支付与转账类（Internal Transfer / External Transfers）的边界划分"
               f"（合计占 Rent 相关差异的 {fmt_pct(trans_in / detail_n)}），即部分租金支付被识别为转账，"
               f"建议优先核对转账类交易中收款方为房产中介、物业管理等租金特征的识别规则，将疑似租金收款方优先识别为 Rent。")
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


def expense_flag_analysis(analysis: Analysis, item: Dict[str, Any]) -> List[str]:
    """支出类红黄灯指标专项差异分析（Retail / Information / Donations / Automotive）。

    两段式（与 Rent 专项同构）：① 一致率·体量·交易方向·单边识别偏向；
    ② 差异流向构成·主因判断·处理建议。全部数据动态取自底稿，无硬编码数字。
    流向语义与报告一致：'X → Retail' 表示 Illion 侧为 X、finv 侧为 Retail；
    '仅 finv 有值 → Retail' 表示 finv 单边识别为 Retail、Illion 无分类。
    """
    cat = item["category"]
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
    elif cat == "Information":
        il_cov = to_float(item.get("illion覆盖率"))
        fv_cov = to_float(item.get("finv覆盖率"))
        p2 += (f"综合来看，Information 差异的主因是 finv 对该类别的识别范围远大于 Illion"
               f"（finv 覆盖率 {fmt_pct(fv_cov)} vs Illion {fmt_pct(il_cov)}）："
               f"finv 单边识别占 {fmt_pct(fv_only_n / detail_n)} 而 Illion 未给出分类，并非双方对同一批交易的分类分歧。"
               f"建议核对 finv 侧信息类交易（资讯/信息服务类商户）的识别规则与 Illion 的覆盖边界，"
               f"确认新增识别是否合理。")
        # 定义差异核验：Illion 侧 Information 均为 0 金额豁免/通知类明细，finv 侧为有金额信息消费
        il_cat = [d for d in analysis.details if d["illion_category"] == cat]
        fv_cat = [d for d in analysis.details if d["finv_category"] == cat]
        both_n = sum(1 for d in analysis.details
                     if d["illion_category"] == cat and d["finv_category"] == cat)
        il_zero = sum(1 for d in il_cat if d["amount"] == 0)
        fv_zero = sum(1 for d in fv_cat if d["amount"] == 0)
        il_zero_share = il_zero / len(il_cat) if il_cat else 0
        fv_zero_share = fv_zero / len(fv_cat) if fv_cat else 0
        union_n = len(il_cat) + len(fv_cat) - both_n
        inter_share = both_n / union_n if union_n else 0
        inter_txt = (f"完全不存在重合（交集占比 0%）" if inter_share == 0
                     else f"重合较少（交集占比 {fmt_pct(inter_share)}）")
        sample = next((d for d in il_cat if d["text"]), None)
        sample_txt = f"（如「{(sample['text'] or '')[:34]}」）" if sample else ""
        p3 = (f"进一步核验两侧对 Information 的定义：Illion 侧识别出的 Information 明细"
              f"占全部差异的 {fmt_pct(len(il_cat) / diff_total) if diff_total else ''}，"
              f"其中金额为 0 的占 {fmt_pct(il_zero_share)}，典型为费用豁免/通知类交易{sample_txt}；"
              f"finv 侧识别出的 Information 明细均为有金额的信息类消费"
              f"（金额为 0 的占 {fmt_pct(fv_zero_share)}）。"
              f"两侧的 Information 明细{inter_txt}，即 Illion 与 finv 的 Information 是两批完全不同的交易，"
              f"差异并非同一笔交易的归类分歧，而是类别定义口径不同："
              f"Illion 将 0 金额的通知/豁免明细归入 Information，finv 仅将有金额的信息类消费归入。"
              f"综合来看，Illion 侧的 Information 类别基本没有业务意义——识别结果全部为 0 金额的"
              f"豁免/通知明细，不构成实际消费，属于无效识别；finv 侧的 Information（有金额的信息类消费）"
              f"才是具有实际意义的真实消费类别，建议以 finv 口径为准，"
              f"并明确 Information 的准入口径（是否包含 0 金额明细），"
              f"或将费用豁免/通知类交易单独归类后复核。")
        paras.append(p3)
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
    elif cat == "Gambling":
        p2 += (f"综合来看，Gambling 差异的主因是博彩识别与转账/其他消费的边界：转账类流入占 "
               f"{fmt_pct(trans_in / detail_n)}、finv 单边识别占 {fmt_pct(fv_only_n / detail_n)}，"
               f"两者合计约占 Gambling 相关差异的 {fmt_pct((trans_in + fv_only_n) / detail_n)}，"
               f"表明存在经转账渠道完成的博彩交易未被 Illion 识别（或 finv 过度识别）的情况。"
               f"建议核对转账类交易中博彩特征收款方的识别规则，并抽查 finv 单边识别样本，"
               f"确认是覆盖扩展还是过度识别。")
    else:
        p2 += "综合来看，该类别差异分布较为分散，建议对主要差异流向抽样核验后再确定规则调整方向。"
    paras.append(p2)
    return paras


def render_category_block(doc: Document, item: Dict[str, Any], analysis: Analysis, detailed: bool = False,
                          number: Optional[str] = None) -> None:
    """结论先行结构渲染单个类别：类别结论 → 类别明细（定义 / 核心指标 / 解读 / 流向 / 方向金额 / 样本）。

    number: 小节编号（如 "3.1.1"）输出为标题；None 输出旧式标题；"" 不输出标题（专项小节沿用外部标题）。
    """
    category = item["category"]
    group = item.get("group", "")
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
            add_flow_table(doc, flows, diff_total=analysis.diff_total)
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
        add_samples_table(doc, analysis.top_samples(category, limit=5))


# ---------------------------------------------------------------------------
# 报告组装
# ---------------------------------------------------------------------------
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
    # 图2.1 各类别覆盖率对比热力矩阵（chart_2_2_png；先展示覆盖率，由 scripts/generate_coverage_heatmap.py 渲染）
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
    # 图2.2 分类别一致率分布全景（chart_png；覆盖率之后展示一致率，由 scripts/generate_dotplot_preview.py 渲染）
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
            render_category_block(doc, item, m, detailed=True, number="")
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
            add_flow_table(doc, rent_flows, diff_total=m.diff_total)
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
    add_flow_table(doc, liab_flows, diff_total=m.diff_total)
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
                  "两侧分类不同的交易会在两个类别的并集中重复计入，故类别一致率不可直接加总为总体覆盖调整后一致率"
                  f"（{fmt_pct(m.metrics.get('覆盖调整后一致率', {}).get('result'))}，总体按交易级去重）。"
                  "建议优先级来自底稿「建议优先级」列，缺失时以「-」表示。"
                  "红绿灯标记：红字（加粗）= 红灯指标（一致率（类别并集）< 50% 或独有占比 > 30%）；绿字（加粗）= 绿灯指标（一致率（类别并集）> 80%）。",
             size=8.5, color=GRAY)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Chinese full category performance report (docx)")
    parser.add_argument("--input", type=Path, default=paths.default_review_report())
    parser.add_argument(
        "--output", type=Path, default=paths.report_output_dir() / DEFAULT_OUTPUT_NAME
    )
    args = parser.parse_args()

    wb = load_workbook(args.input, data_only=True, read_only=True)
    metrics = read_metrics(wb["00_核心对比"])
    generated_date = read_generated_date(wb["00_核心对比"])
    categories = read_categories(wb["00_核心对比"])
    top_flows = read_top_flows(wb["01_差异诊断地图"])
    details = read_details(wb["03_排查明细"])
    wb.close()

    if len(categories) != 36:
        raise ValueError(f"类别数量异常: {len(categories)} (期望 36)")

    analysis = Analysis(metrics, categories, top_flows, details)

    # 图2.1 各类别覆盖率对比热力矩阵（渲染后嵌入报告；失败仅告警，不影响报告生成）
    chart_2_2_png: Optional[Path] = BASE_DIR / "tmp" / "chart_2_2.png"
    try:
        from generate_coverage_heatmap import render as render_coverage
        render_coverage(chart_2_2_png, analysis.categories, input_name=args.input.name)
    except Exception as exc:
        print(f"[warn] 图2.1 渲染失败，报告将不包含该图: {exc}")
        chart_2_2_png = None

    # 图2.2 分类别一致率分布（渲染后嵌入报告；失败仅告警，不影响报告生成）
    # 面板/整体 Avg 与 2.2 表同口径（交易加权），避免图与表数字不一致
    chart_png: Optional[Path] = BASE_DIR / "tmp" / "chart_2_1.png"
    try:
        from generate_dotplot_preview import render as render_chart
        segment_avg = {g: analysis.segment_coverage(g)["exact_rate"] for g in GROUP_ORDER}
        overall_avg = to_float(analysis.metrics.get("覆盖调整后一致率", {}).get("result"))
        render_chart(chart_png, analysis.categories, "b", segment_avg, overall_avg)
    except Exception as exc:
        print(f"[warn] 图2.2 渲染失败，报告将不包含该图: {exc}")
        chart_png = None

    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc = init_document(args.input.name)
    doc.core_properties.title = "BS-CAT 全分类类别表现分析报告"
    doc.core_properties.author = "BS-CAT"
    build_report(doc, analysis, chart_png, chart_2_2_png,
                 input_name=args.input.name, generated_date=generated_date)
    doc.save(args.output)

    print(f"Generated: {args.output}")
    print(f"Categories: {len(categories)}; detail rows: {len(details)}; top flows: {len(top_flows)}")
    print(f"Total transactions: {fmt_num(analysis.total_transactions)}; diff total: {fmt_num(analysis.diff_total)}")
    print(f"Matrix sum: {fmt_num(sum(analysis.matrix.values()))}")


if __name__ == "__main__":
    main()
