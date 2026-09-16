"""
Business bd Pipeline — 全局配置
===============================
所有路径、过滤规则、关键词清洗参数集中管理。
每次换新报文只需修改此文件（如果规则变了），其余 pipeline 代码无需改动。
"""

import csv
from pathlib import Path

# ─── csv 解析上限 ─────────────────────────────────────
# 必须在任何 CSV 读取之前生效，所以放在 settings 里：本模块 8 个脚本全都
# import 它，而只有 3 个 import utils。KB 里有单行 keywords 字段超过 csv
# 默认的 128KB 上限（"Australia Post" 的 5,486 个变体 → 137KB），不放宽的
# 话读取整个 KB 会在那一行抛 _csv.Error: field larger than field limit。
# （utils.py 里也有一份，供单独 import utils 的调用方兜底）
csv.field_size_limit(10_000_000)

# ─── 目录 ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent          # modules/merchant_kb/
REPO_ROOT = BASE_DIR.parent.parent                  # Auto_Rule_Extension/
RAW_DIR = BASE_DIR / "xml_input"
DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = BASE_DIR / "backup"
ADD_DIR = BASE_DIR / "manual_entries"

# ─── 中间产物 ─────────────────────────────────────────
PARSED_DIR = DATA_DIR / "parsed"           # parse.py 输出（每个 XML 一个 CSV）
FILTERED_FILE = DATA_DIR / "filtered.csv"  # filter.py 输出
INTERNAL_FILE = DATA_DIR / "kb_internal.csv"  # 含全部元数据的内部 KB
CHANGELOG_FILE = DATA_DIR / "changelog.csv"   # 增量变更日志

# ─── 最终产出 ─────────────────────────────────────────
# 知识库的唯一事实源。直接位于仓库的 raw/ 下，供 finv_category_V2 的
# initial_engine 加载（该引擎以 usecols 只读 3 列）。
# ⚠️ 此文件的列结构由 initial_engine 决定，不是本模块可以自由调整的。
KB_DIR = REPO_ROOT / "raw" / "initial_rule"
FINAL_OUTPUT = KB_DIR / "merchant_kb.csv"

# ═══════════════════════════════════════════════════════
# 过滤规则（换报文时按需调整）
# ═══════════════════════════════════════════════════════

# 只保留这两种实体类型
KEEP_ENTITY_TYPES = frozenset({"PRV", "PUB"})
# PRV = Australian Private Company
# PUB = Australian Public Company

# 注销日期阈值：此日期**之前**注销的丢弃（格式 YYYYMMDD）
CANCEL_CUTOFF_DATE = "20230101"  # 2023-01-01

# 匹配键长度（SHA256 截断字符数）
MATCH_KEY_LENGTH = 16

# 内部标记：旧 KB 中有但新报文中消失的记录
STATUS_GONE = "GONE"

# ═══════════════════════════════════════════════════════
# 关键词清洗参数
# ═══════════════════════════════════════════════════════

MIN_KEYWORD_LEN = 5  # 最短关键词长度
MIN_DISTINCTIVE_KEYWORD_TOKENS = 1  # 清理后至少保留多少个非泛化词

# 模糊匹配阈值：关键词与商户名的 token 重叠率低于此值时移除
# 0.0 = 仅移除与商户名零重叠的关键词（最保守，只清除完全无关的污染）
KEYWORD_NAME_SIMILARITY_THRESHOLD = 0.0

# 交易流水中常见的支付/渠道前缀。它们本身不能帮助识别商户，
# 出现在关键词开头时会被剥离，整条只剩这些词时会被移除。
PAYMENT_PREFIX_WORDS = frozenset({
    "AP", "APPLE", "GOOGLE", "PAYPAL", "PP", "SQ", "SQUARE",
    "TST", "UBEREATS", "DOORDASH", "MENULOG",
    "WWW", "HTTP", "HTTPS",
})

# ─── 知名缩写白名单 ───
# 这些品牌/机构缩写虽然短于 MIN_KEYWORD_LEN，但在银行交易中高频出现，
# 是有效的匹配关键词，豁免长度过滤。

KNOWN_ABBREVIATIONS = frozenset({
    # ── 加油站 / 能源 ──
    "BP", "OTR",          # BP加油站, On The Run
    # ── 零售 / 超市 ──
    "IGA", "ALDI", "SPAR", "MYER", "IKEA",
    # ── 快餐 / 餐饮 ──
    "KFC", "GYG", "MCD", "HJS",   # Guzman y Gomez, McDonald's, Hungry Jack's
    # ── 酒类零售 ──
    "BWS",                # Beer Wine Spirits
    # ── 户外 / 运动 ──
    "BCF",                # Boating Camping Fishing
    # ── 烟草 / 便利店 ──
    "TSG",                # Tobacco Station Group
    # ── 时尚 / 配饰 ──
    "H&M", "ZARA", "LUSH", "STAX", "ZING",
    # ── 电商 / 平台 ──
    "TEMU", "ETSY",
    # ── 交通 / 出行 ──
    "UBER", "DIDI", "SIXT", "MYKI", "OLA",
    # ── 保险 / 道路服务 ──
    "NRMA", "RACV", "RACQ", "RAA", "RACT", "GIO",
    # ── 金融 / 支付 ──
    "ING", "HUMM",        # ING银行, humm先买后付
    # ── 能源 / 公用事业 ──
    "AGL",
    # ── 政府 ──
    "ATO",                # Australian Taxation Office
    # ── 电信 ──
    "TPG",
    # ── 娱乐 / 流媒体 ──
    "STAN", "NEDS", "HAYU",
    # ── 药房 ──
    "CWH",                # Chemist Warehouse
})

# ─── 完整 STOPWORDS 集合 ───
STOPWORDS = {
    # ── 澳洲主要城市 ──
    "SYDNEY", "MELBOURNE", "PERTH", "BRISBANE", "ADELAIDE",
    "HOBART", "DARWIN", "CANBERRA", "GOLD COAST", "NEWCASTLE",

    # ── 常见区/镇名 ──
    "IPSWICH", "TOOWOOMBA", "CAIRNS", "BALLARAT", "BENDIGO",
    "ALBURY", "DUBBO", "ORANGE", "PENRITH", "CAMPBELLTOWN",
    "LIVERPOOL", "PARRAMATTA", "CHATSWOOD", "HURSTVILLE",
    "BANKSTOWN", "BLACKTOWN", "FAIRFIELD", "CABRAMATTA",
    "WOLLONGONG", "DAPTO", "CORRIMAL", "SHELLHARBOUR", "FIGTREE",
    "NOWRA", "BATEMANS", "BEGA", "COOMA", "GOULBURN",
    "MORUYA", "YASS", "COWRA", "FORBES", "PARKES",
    "BROKEN", "GRIFFITH", "LEETON", "NARRANDERA", "WAGGA",
    "WODONGA", "SHEPPARTON", "WANGARATTA", "BENALLA",
    "ECHUCA", "SWAN", "MILDURA", "HORSHAM", "ARARAT",
    "BAIRNSDALE", "SALE", "TRARALGON", "WARRAGUL", "MOE",
    "MORWELL", "DANDENONG", "FRANKSTON", "CRANBOURNE", "BERWICK",
    "PAKENHAM", "MORNINGTON", "ROSEDALE", "SUNBURY", "MELTON",
    "WERRIBEE", "GEELONG", "TORQUAY", "COLAC", "WARRNAMBOOL",
    "HAMILTON", "PORTLAND", "CASTLEMAINE", "KYNETON",
    "SUNSHINE", "BROADMEADOWS", "CRAIGIEBURN", "EPPING", "BUNDOORA",
    "HEIDELBERG", "DONCASTER", "RINGWOOD", "BOX", "BOROONDARA",
    "MOONEE", "ESSENDON", "BRUNSWICK", "COBURG", "PRESTON",
    "RESERVOIR", "THOMASTOWN", "LALOR", "JACANA", "GLENROY",
    "OAKLEIGH", "CLAYTON", "SPRINGVALE", "DINGLEY", "MORDIALLOC",
    "MENTONE", "SANDRINGHAM", "BRIGHTON", "ST", "ELSTERNWICK",
    "CAULFIELD", "MALVERN", "ARMADALE", "TOORAK", "PRAHRAN",
    "SOUTH", "PORT", "ALBERT", "FOOTSCRAY", "WILLIAMSTOWN",
    "ASCOT", "HENDRA", "CLAYFIELD", "ALBION",
    "LUTWYCHE", "CHERMSIDE", "ASPLEY", "ZILLMERE", "GEEBUNG",
    "STRATHPINE", "PETRIE", "KALLANGUR", "CABOOLTURE", "MORAYFIELD",
    "BURPENGARY", "DECEPTION", "NORTH", "REDCLIFFE", "MARGATE",
    "SCARBOROUGH", "WOODY", "CLONTARF", "SANDGATE", "BRACKEN",
    "BALD", "FITZGIBBON", "TAIGUM", "BOONDALL", "NUDGEE",
    "BANYO", "VIRGINIA", "NUNDAH", "TOOMBUL", "WAVELL",
    "KEDRON", "GORDON", "EVERTON", "MCDOWALL", "BRIDGEMAN",
    "ALBANY", "CANNING", "FREMANTLE", "JOONDALUP", "MANDURAH",
    "MIDLAND", "ROCKINGHAM", "GOSNELLS", "KALAMUNDA",
    "BELMONT", "VICTORIA", "KEWDALE", "CLOVERDALE",
    "BURSWOOD", "RIVERVALE", "MAYLANDS", "BASSENDEAN", "GUILDFORD",
    "MIDVALE", "ELLENBROOK", "AVON", "KALGOORLIE",
    "BUNBURY", "BUSSELTON", "GERALDTON", "CARNARVON",
    "BROOME", "KUNUNURRA", "EAST", "NORAM",
    "LAUNCESTON", "DEVONPORT", "BURNIE", "KINGSTON", "GLENORCHY",
    "CLAREMONT", "MOONAH", "LINDISFARNE", "HOWRAH", "SORELL",
    "NEW", "ROSETTA", "BERRIEDALE", "CHIGWELL", "MONTROSE",

    # ── 商业通用词 ──
    "REAL", "ESTATE", "PROPERTY", "FINANCIAL", "FINANCE",
    "LOAN", "LOANS", "HOME", "HOMES", "RENTAL",
    "RENTALS", "AGENCY", "AGENT", "INVESTMENT", "INVESTMENTS",
    "VENTURES", "ENTERPRISE", "ENTERPRISES", "TRADING",
    "HOLDING", "MANAGEMENT", "SOLUTIONS", "CONSULTING",
    "CONSULTANCY", "CONSULTANTS", "ASSOCIATES", "PARTNERS",
    "DISTRIBUTORS", "WHOLESALE", "SUPPLIES", "SUPPLIERS",

    # ── 方位/基础设施词 ──
    "WEST", "EAST", "NORTH", "SOUTH", "CENTRAL", "CITY",
    "TOWN", "VALLEY", "BAY", "BEACH", "PARK",
    "STREET", "ROAD", "STATION", "SQUARE", "CENTRE",
    "CENTER", "PLAZA", "MALL", "AIRPORT", "HARBOUR",
    "HARBOR", "BRIDGE", "HILL", "HILLS", "MOUNT",
    "LAKE", "RIVER", "COAST", "POINT", "CREEK",
    "ISLAND", "GARDEN", "GARDENS", "HEIGHTS",
    "JUNCTION", "CROSSING", "GATE", "GATES",
    "VILLAGE", "GROVE", "GLEN", "DALE",
    "WOOD", "WOODS", "FIELD", "FIELDS",
    "MEADOW", "MEADOWS", "GREEN", "SPRINGS",

    # ── 文档中提到的具体问题关键词 ──
    "PARKINSON", "VINCENTIA",
    "YAMBA", "ILUKA", "LIDCOMBE", "KIRWAN",
    "METRO", "STATE", "SYSTEMS", "SALES",
    "ELEVEN", "WEST", "SPRING",

    # ── 支付通道词/交易类型词 ──
    "CARD", "VISA", "EFTPOS", "BPAY", "OSKO",
    "PAYMENT", "PURCHASE", "TRANSFER", "DEPOSIT",
    "FEE", "INTEREST", "OVERDRAWN",
    "LIMITED", "GROUP", "HOLDINGS",
    "AUSTRALIA", "AUS",
}

# 归一化为大写
STOPWORDS = {w.upper() for w in STOPWORDS}

# ═══════════════════════════════════════════════════════
# 内部 CSV 列定义
# ═══════════════════════════════════════════════════════

KB_INTERNAL_COLUMNS = [
    "match_key",          # sha256(法定主体名称)[:16]
    "merchant_name",      # 清洗后的商户名
    "keywords",           # 匹配关键词
    "link",               # 链接
    "category",           # 分类
    "entity_type",        # PRV 或 PUB
    "abn_status",         # ACT / CAN / GONE
    "status_date",        # ABNStatusFromDate
    "state",              # BusinessAddress State
    "record_updated",     # recordLastUpdatedDate
    "keyword_updated_at",  # 关键词最近一次生成/变更时间
    "category_updated_at", # 分类最近一次更新/变更时间
    "in_kb_since",        # 首次进入 KB 的时间
]

# 最终输出列（3 列）
# ⚠️ 这是与 finv_category_V2 initial_engine 的加载契约，不可自由扩展：
#    domain/classification.py:198 以 usecols=["merchant_name","keywords","category"] 读取。
# 2026-08-07（commit 29ae8b5「压缩了kb大小」）起 KB 就是这 3 列；此前的
# link / keyword_updated_at / category_updated_at 列已废弃。
# ⚠️ 2026-08-27 的 commit c45ac5a 只是给赌博商户补 keyword 变体，不是 3 列化的起点。
# 需要这些元数据时用 KB_INTERNAL_COLUMNS 描述的中间产物 —— 但注意该产物**已不再生成**
# （见 KB_INTERNAL_COLUMNS 处的说明）。
FINAL_OUTPUT_COLUMNS = [
    "merchant_name",
    "keywords",
    "category",
]
