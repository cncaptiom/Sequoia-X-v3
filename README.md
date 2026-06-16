# Sequoia-X V3

> 主要面向在职人员在工作期间不方便使用PC端进行炒股的人。A 股量化选股系统（操作数据量大在PC端运行） + 手机端实时监控 | A-Share Quantitative Stock Selection & Mobile Monitoring System

基于 [sngyai/Sequoia-X](https://github.com/sngyai/Sequoia-X) 进行大幅改造和扩展，在此感谢原作者的贡献。

📌 原项目 MIT 协议，本衍生项目同样遵循 MIT 协议。

---

## 📱 项目简介

一个完整的 A 股量化交易辅助系统，由 **PC 端（选股引擎）**和 **手机端（实时监控）** 两部分组成：

```
PC端 (每晚21:00自动运行)
  ┌─ 全市场5000+只股票扫描
  ├─ 6大选股策略 + 7大评级策略
  ├─ 按评分取前15名生成操作建议
  ├─ 推送飞书 + 上传云端
  └─ 手机端实时拉取数据

手机端 (盘中9:30-15:00)
  ┌─ 实时显示PC端推荐股票
  ├─ 实时行情监控 + 止损止盈检查
  ├─ 明确的操作建议（买入/持有/卖出）
  └─ 15秒自动刷新
```

---

## ⚙️ PC端 — 量化选股系统

### 核心架构

```
mootdx(主数据源) ─→ SQLite ─→ 6策略选股 ─→ ConsensusEngine融合
                                   ↓
baostock(备选) ─→ 数据清洗 ←─── 7策略评级  
                                   ↓
                             SignalGenerator信号生成
                                   ↓
                     ┌──── 飞书推送(操作建议) ────┐
                     │                            │
                     └── 腾讯云COS ─→ 手机端拉取 ──┘
```

### 6 大选股策略

| 策略 | 类型 | 说明 |
|------|------|------|
| **MaVolume** | 趋势跟踪 | MA5金叉MA20 + 成交量放大1.5倍 |
| **TurtleTrade** | 趋势突破 | 20日新高 + 成交额过亿 + 阳线确认 |
| **HighTightFlag** | 动量 | 40日上涨60%后缩量横盘<15%的旗形整理 |
| **LimitUpShakeout** | 反转 | 涨停次日阴线洗盘但不破昨收 |
| **UptrendLimitDown** | 反转 | 上升趋势中跌停放量见底 |
| **RpsBreakout** | 动量 | 120日相对强度>90% + 接近新高 |

### 7 大评级策略

| 策略 | 功能 |
|------|------|
| **MaVolume** | 均线金叉+放量评分 |
| **TurtleTrade** | 趋势突破评分 |
| **均值回归** | 布林带+RSI超买超卖 |
| **量价关系** | 放量上涨/缩量回调/量价背离 |
| **ATR通道** | 波动率趋势跟踪 |
| **MACD+RSI共振** | 金叉死叉+超买超卖共振 |
| **MA20/60金叉死叉** | 中长期趋势判定 |

### 市场状态驱动权重

不同的市场状态自动调整策略权重：

| 市场状态 | 趋势策略 | 动量策略 | 反转策略 | 建议仓位 |
|---------|---------|---------|---------|---------|
| 强牛 | 50% | 30% | 20% | 100% |
| 弱牛 | 40% | 35% | 25% | 70% |
| 震荡 | 20% | 25% | 55% | 50% |
| 弱熊 | 10% | 30% | 60% | 30% |
| 强熊 | 0% | 10% | 90% | 15% |

### 置信度评分（7 维度）

| 维度 | 权重 | 说明 |
|------|------|------|
| 多策略共振 | 0.25 | ≥3个策略选中加分 |
| 趋势强度 | 0.20 | 均线多头排列 |
| 量价配合 | 0.15 | 放量上涨或缩量回调 |
| 安全边际 | 0.15 | 接近支撑位 |
| 低波动环境 | 0.10 | 20日波动率<3% |
| 量价共振 | 0.10 | 放量+多头趋势同时满足 |
| 资金流向 | 0.05 | 北向资金增持 |

附加修正：周线趋势过滤（±0.10）、市场状态修正（±0.08）、风险控制修正（±0.13）

置信度等级：
- **S** (≥0.75)：强烈推荐，多策略共振+趋势强
- **A** (≥0.60)：推荐，多策略确认
- **B** (≥0.45)：关注，个别策略触发
- **C** (≥0.25)：观察，信号偏弱
- **D** (<0.25)：不参与

### 风险管理

- ATR 动态仓位计算（海龟交易法）
- Kelly 公式仓位优化
- VaR/CVaR 风险评估
- 单票仓位上限 15%
- 止损/止盈自动计算

---

## 📱 手机端 — 实时监控APP

### 技术栈

Flutter / Provider / Dio / Hive / fl_chart

### 功能列表

| 页面 | 功能 |
|------|------|
| **首页** | 大盘指数 + PC端推荐股票（含操作信号/买入区间/止损止盈） + 自选股 |
| **详情页** | 实时行情 + 实时操盘建议（BUY/WAIT/HOLD） + PC端评级 + K线 + 策略评分 |
| **搜索页** | 股票搜索 + 排行榜（涨幅/跌幅/成交额） + 行业板块 |
| **策略页** | 市场状态显示 + 操盘风格选择 + 策略开关 |
| **设置页** | PC数据同步状态 + 通知管理 + 效果跟踪 |

### 实时操盘建议

根据 PC 端每晚计算的买入区间 vs 盘中实时价格，自动判断：

```
当前价 < 买入区间 → 观望/关注
当前价在买入区间内 → 买入（红色大字）
当前价 > 买入区间 → 等回调/轻仓
已持仓 → 持有/警戒/止损/止盈
```

每种状态包含三种执行路径：
- **激进** — 快速入场/出场，适合牛市
- **保守** — 等回调/确认，保本优先
- **信号** — 等MACD+放量确认，最安全

---

## 🚀 快速开始（PC端）

### 环境要求

- Python >= 3.10
- Windows/Linux/macOS

### 1. 安装

```bash
git clone https://github.com/cncaptiom/Sequoia-X-v3.git
cd Sequoia-X-v3
pip install .
pip install ".[factors]"  # 如果需要基本面因子（可选）
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填入飞书 Webhook URL 和腾讯云 COS 配置（可选）
```

### 3. 回填历史数据

```bash
# 推荐（约3-5分钟，TCP协议更稳定）
python main.py --mootdx-backfill

# 或使用并行回填（约3-4分钟）
python main.py --parallel-backfill
```

### 4. 运行

```bash
# 全市场扫描 + 评级 + 推送（推荐）
python main.py --rating

# 或日常模式（增量更新 + 选股 + 推送）
python main.py
```

### 5. 查看市场状态

```bash
python main.py --regime
# 输出: 当前市场状态、建议仓位、趋势强度
```

### 6. 策略回测

```bash
python main.py --backtest
```

### 7. 安装定时任务（Windows）

```bash
python main.py --install-schedule
# 每日21:00自动运行日常模式
```

### 所有命令

| 命令 | 功能 |
|------|------|
| `python main.py` | 日常模式：增量更新+选股+推送 |
| `python main.py --rating` | 评级模式：全市场7策略评分+操作建议 |
| `python main.py --mootdx-backfill` | mootdx回填全市场历史数据 |
| `python main.py --backfill` | 串行回填（baostock） |
| `python main.py --parallel-backfill` | 并行回填（baostock） |
| `python main.py --backtest` | 全策略回测 |
| `python main.py --regime` | 市场状态检测 |
| `python main.py --factors TOP20` | 多因子选股 |
| `python main.py --review` | 推荐命中率回溯 |
| `python main.py --install-schedule` | 安装Windows定时任务 |
| `python main.py --remove-schedule` | 卸载定时任务 |

---

## 🚀 快速开始（手机端）

### 环境要求

- Flutter SDK >= 3.0
- Android Studio / Xcode

### 构建

```bash
cd gpfxapp/
flutter pub get
flutter build apk --debug   # Android
# 或
flutter build ios           # iOS
```

APK 生成在 `gpfxapp/build/app/outputs/flutter-apk/app-debug.apk`

**手机端依赖 PC 端**：PC端需配置好腾讯云 COS 并成功运行 `--rating` 模式，
手机端才能拉取到选股数据。

---

## 🏗️ 目录结构

```
Sequoia-X-v3/
├── main.py                 # PC端入口
├── pyproject.toml          # Python 依赖
├── .env.example            # 环境变量模板
│
├── sequoia_x/              # PC端核心代码
│   ├── core/               # 配置 + 日志
│   ├── data/               # 数据引擎（mootdx/baostock + SQLite）
│   ├── strategy/           # 6个选股策略
│   ├── rating/             # 7个评级策略
│   ├── signal/             # 交易信号生成
│   ├── recommender/        # 飞书报告生成
│   ├── ensemble/           # 多策略融合评分
│   ├── regime/             # 市场状态识别
│   ├── risk/               # 风险管理（ATR/Kelly/VaR）
│   ├── factors/            # 多因子模型（Fama-French）
│   ├── flow/               # 资金流向分析
│   ├── backtest/           # 回测引擎
│   ├── analysis/           # 技术分析
│   ├── notify/             # 飞书通知
│   ├── tracker/            # 推荐追踪
│   └── cloud/              # 腾讯云COS同步
│
├── gpfxapp/                # 手机端 Flutter APP
│   ├── lib/
│   │   ├── models/         # 数据模型
│   │   ├── pages/          # 页面（首页/详情/搜索/策略/设置）
│   │   ├── providers/      # 状态管理
│   │   ├── services/       # API服务
│   │   └── widgets/        # UI组件
│   └── pubspec.yaml
│
├── tests/                  # 测试用例
├── data/                   # 数据目录
└── output/                 # 输出目录
```

---

## 📊 数据流架构

```
                        PC端（每晚21:00）
                         
mootdx ──→ baostock(备)
    │
    ▼
SQLite ──→ DataValidator ──→ 6策略并行选股
    │                           │
    │                     ConsensusEngine评分
    │                           │
    │                     7策略评级扫描
    │                           │
    │                    SignalGenerator信号
    │                           │
    │              ┌────────────┴────────────┐
    │              │                         │
    │         飞书推送                   腾讯云COS
    │      （操作建议卡片）          （全量数据上传）
    │                                         │
    │                                         ▼
    │                                    手机端拉取
    │                                    （每日启动时）
    │                                         │
    │                              ┌──────────┴──────────┐
    │                              │                     │
    │                         首页推荐列表           详情页数据
    │                         （10只）           （评级+买入区间）
    │                              │                     │
    │                         盘中实时行情 ──── 实时操盘建议
    │                         （15秒轮询）     （BUY/HOLD/SELL）
    │
    └─── 推荐记录存入 SQLite ───→ 7天后 --review 回溯
```

---

## 📝 致谢

- 本项目基于 [sngyai/Sequoia-X](https://github.com/sngyai/Sequoia-X) 改造扩展
- 框架参考 [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) 多智能体决策流程
- 基本面研究参考 [muxuuu/serenity-skill](https://github.com/muxuuu/serenity-skill) 供应链瓶颈方法论
- 数据源感谢 [mootdx](https://github.com/mootdx/mootdx)、[baostock](http://baostock.com)、[akshare](https://github.com/akfamily/akshare)

---

## ⚠️ 声明

**本系统所有分析结果仅供参考，不构成投资建议。**
股市有风险，投资需谨慎。历史回测表现不代表未来结果。

MIT License
