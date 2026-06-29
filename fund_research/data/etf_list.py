"""A股核心ETF标的池。

涵盖宽基指数、行业/主题、风格策略三大类。
所有ETF均为上市满1年、日均成交额>5000万的流动性标的。
"""

from dataclasses import dataclass

# ── 分类定义 ──

ETF_CATEGORIES = {
    "宽基": "覆盖全市场的规模指数",
    "消费": "食品饮料、家电、旅游等",
    "医药": "创新药、医疗器械、医疗服务等",
    "科技": "芯片、半导体、计算机、通信等",
    "制造": "新能源、军工、高端制造等",
    "周期": "煤炭、有色、化工、钢铁等",
    "金融": "证券、银行、保险等",
    "策略": "红利低波、价值等SmartBeta",
}


@dataclass
class EtfInfo:
    code: str          # 6位ETF代码
    name: str          # 简称
    category: str      # 分类
    description: str = ""  # 说明


# ── 核心ETF池（25只） ──

ETF_POOL: list[EtfInfo] = [
    # ── 宽基（4只） ──
    EtfInfo("510300", "沪深300ETF", "宽基", "大盘蓝筹代表"),
    EtfInfo("510500", "中证500ETF", "宽基", "中盘成长代表"),
    EtfInfo("510050", "上证50ETF", "宽基", "超大盘权重股"),
    EtfInfo("159915", "创业板ETF", "宽基", "创业板龙头"),

    # ── 消费（3只） ──
    EtfInfo("159928", "消费ETF", "消费", "主要消费+可选消费"),
    EtfInfo("512690", "酒ETF", "消费", "白酒+啤酒+葡萄酒"),
    EtfInfo("159825", "农业ETF", "消费", "种植业+畜牧业"),

    # ── 医药（2只） ──
    EtfInfo("512010", "医药ETF", "医药", "医药卫生全行业"),
    EtfInfo("159992", "创新药ETF", "医药", "创新药产业链"),

    # ── 科技（5只） ──
    EtfInfo("159995", "芯片ETF", "科技", "半导体芯片全链"),
    EtfInfo("512480", "半导体ETF", "科技", "半导体设备+材料"),
    EtfInfo("515880", "通信ETF", "科技", "5G+光通信+运营商"),
    EtfInfo("159998", "计算机ETF", "科技", "IT服务+软件+硬件"),
    EtfInfo("512980", "传媒ETF", "科技", "游戏+影视+广告"),

    # ── 制造（4只） ──
    EtfInfo("515700", "新能源车ETF", "制造", "新能源车全产业链"),
    EtfInfo("159857", "光伏ETF", "制造", "光伏组件+逆变器"),
    EtfInfo("512660", "军工ETF", "制造", "航空+船舶+信息化"),
    EtfInfo("159611", "电力ETF", "制造", "火电+水电+新能源发电"),

    # ── 周期（4只） ──
    EtfInfo("515220", "煤炭ETF", "周期", "煤炭开采+焦化"),
    EtfInfo("512400", "有色金属ETF", "周期", "铜铝+黄金+稀土"),
    EtfInfo("159870", "化工ETF", "周期", "基础化工+化纤"),
    EtfInfo("515210", "钢铁ETF", "周期", "普钢+特钢"),

    # ── 金融（2只） ──
    EtfInfo("512880", "证券ETF", "金融", "券商全行业"),
    EtfInfo("512800", "银行ETF", "金融", "银行全行业"),

    # ── 策略（1只） ──
    EtfInfo("510880", "红利ETF", "策略", "高股息+低波动"),
]


def get_etf_by_category(category: str) -> list[EtfInfo]:
    """按分类获取ETF列表。"""
    return [e for e in ETF_POOL if e.category == category]


def get_etf_codes() -> list[str]:
    """获取所有ETF代码。"""
    return [e.code for e in ETF_POOL]


def get_etf_map() -> dict[str, EtfInfo]:
    """代码→ETF信息映射。"""
    return {e.code: e for e in ETF_POOL}
