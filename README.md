# Sequoia-X V3: The King Returns

> A 股量化选股系统 V3 | A-Share Quantitative Stock Selection System V3
>
> 本项目基于 [sngyai/Sequoia-X](https://github.com/sngyai/Sequoia-X) 进行大幅改造和扩展，
> 在此感谢原作者的贡献。原项目使用 MIT 协议，本衍生项目同样遵循 MIT 协议。

---

## V3 新增功能

基于机构量化投资方法论的重大升级：

| 模块 | 说明 | 学术/机构背景 |
|---|---|---|
| **backtest/** | 滑动窗口回测 + 绩效归因 | 夏普比率/索提诺/卡玛比率/最大回撤 |
| **factors/** | 多因子横截面排名 | Fama-French 五因子 + AQR 质量因子 |
| **risk/** | ATR 动态仓位 + VaR 估算 | 海龟交易仓位管理 + Kelly 公式 |
| **regime/** | 市场状态识别（牛/熊/震荡） | 均线排列 + ATR 趋势强度 + 波动率分位 |
| **flow/** | 北向资金 + 融资融券 + 大宗交易 | 机构行为 Alpha 信号 |

---

## V2 原有功能

### 运行模式

```bash
python main.py                     # 日常模式：8进程增量补数据 + 跑策略 + 飞书推送
python main.py --backfill          # 串行回填历史K线（约12分钟）
python main.py --parallel-backfill # 并行回填历史K线（约3-4分钟）
python main.py --rating            # 评级模式：全市场7大策略评分
python main.py --backtest          # 回测模式：策略历史绩效评估
python main.py --regime            # 市场状态检测
python main.py --factors TOP20     # 多因子选股（取排名前20只）
python main.py --install-schedule  # 安装每日21:00定时任务（Windows）
python main.py --remove-schedule   # 卸载定时任务
```

### 内置策略

| 策略 | 说明 |
|---|---|
| **TurtleTrade** | 海龟突破：20日新高 + 成交额过亿 + 阳线防诱多 |
| **MaVolume** | 均线+放量突破 |
| **HighTightFlag** | 高而窄的旗形整理突破 |
| **LimitUpShakeout** | 涨停洗盘回踩确认 |
| **UptrendLimitDown** | 上升趋势中的跌停反包 |
| **RpsBreakout** | 欧奈尔 RPS 相对强度突破 |

### 评级引擎（7 大策略）

MaVolume / TurtleTrade / 均值回归 / 量价关系 / ATR通道 / MACD+RSI / MA交叉

---

## 快速开始

### 环境要求

- Python >= 3.10

### 1. 安装依赖

```bash
# 基础功能
pip install .

# 含因子基本面数据（需要 akshare）
pip install ".[factors]"
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写飞书 Webhook URL

# 可选：自定义评分器权重
# SCORER_WEIGHTS="1.0,1.2,0.8,1.0"  # vol, momentum, trend, stability
```

### 3. 首次回填历史数据

```bash
# 推荐使用并行回填（约3-4分钟）
python main.py --parallel-backfill

# 或串行回填（约12分钟）
python main.py --backfill
```

### 4. 日常运行

```bash
python main.py
```

### 5. 策略回测

```bash
python main.py --backtest
# 输出 output/backtest_YYYYMMDD.json 含夏普/最大回撤/胜率等
```

### 6. 多因子选股

```bash
python main.py --factors TOP20
# 输出横截面排名前20只，基于价值/质量/动量/低波/规模因子
```

### 7. 市场状态检测

```bash
python main.py --regime
# 输出当前牛熊判断、建议仓位、策略偏向
```

---

## 目录结构

```
Sequoia-X-v3/
├── main.py
├── pyproject.toml
├── .env.example
├── data/                          # SQLite 数据库
├── sequoia_x/
│   ├── analysis/                  # 技术分析（支撑/压力/买卖策略）
│   │   └── technical.py
│   ├── backtest/       **[NEW]**  # 回测引擎 + 绩效归因
│   │   ├── engine.py
│   │   └── metrics.py
│   ├── core/                      # 配置 + 日志
│   │   ├── config.py
│   │   └── logger.py
│   ├── data/                      # 数据引擎（baostock + SQLite）
│   │   └── engine.py
│   ├── factors/        **[NEW]**  # 多因子计算 + 横截面排名
│   │   └── __init__.py
│   ├── flow/           **[NEW]**  # 资金流向分析
│   │   └── __init__.py
│   ├── notify/                    # 飞书推送
│   │   └── feishu.py
│   ├── rating/                    # 7策略评级引擎
│   │   ├── engine.py
│   │   └── strategies.py
│   ├── regime/         **[NEW]**  # 市场状态识别
│   │   └── __init__.py
│   ├── risk/           **[NEW]**  # 风险管理
│   │   └── __init__.py
│   └── strategy/                  # 6个选股策略
│       ├── base.py
│       ├── scorer.py
│       ├── turtle_trade.py
│       ├── ma_volume.py
│       ├── high_tight_flag.py
│       ├── limit_up_shakeout.py
│       ├── rps_breakout.py
│       └── uptrend_limit_down.py
└── tests/
```

---

## 数据说明

- **数据源**：[mootdx](https://github.com/mootdx/mootdx)（主用，通达信TCP协议，永不封IP） + [baostock](http://baostock.com)（备选）
- **复权方式**：前复权
- **存储**：本地 SQLite（`data/sequoia_v2.db`）
- **日常增量**：走 mootdx，失败自动降级 baostock
- **基本面因子**：需安装 `akshare`（`pip install ".[factors]"`）

---

## 许可证

MIT
