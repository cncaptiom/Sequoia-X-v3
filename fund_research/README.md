# Sequoia Fund — 基金量化分析系统

基于 Sequoia-X v3 选股系统的技术框架，拓展到公募基金/ETF 领域。

## 项目结构

```
fund_research/
├── main.py                # 入口
├── core/                  # 核心（配置、日志）
├── data/                  # 数据获取
│   ├── fund_fetcher.py    # 基金净值/持仓数据
│   └── etf_fetcher.py     # ETF 行情数据
├── analysis/              # 分析模块
│   ├── nav_analyzer.py    # 净值分析（收益/风险/回撤）
│   ├── position_analyzer.py  # 持仓穿透分析
│   ├── risk_metrics.py    # 风险指标
│   └── screener.py        # 基金筛选器
├── strategy/              # 策略模块
│   ├── index_enhance.py   # 指数增强策略
│   ├── sector_rotation.py # 行业轮动策略
│   └── etf_momentum.py    # ETF动量策略
├── backtest/              # 回测引擎
│   └── engine.py
└── notebooks/             # Jupyter 分析笔记
```

## 数据源

- **天天基金/东方财富**: 基金净值、持仓、基本信息
- **akshare**: ETF 行情、行业指数
- **mootdx/baostock**: 股票/指数 K 线（复用选股系统）

## 使用方法

```bash
# 拉取基金数据
python -m fund_research.main --fetch-data

# 列出基金
python -m fund_research.main --list-funds

# 分析单只基金
python -m fund_research.main --analyze 110011

# 全市场筛选
python -m fund_research.main --screen

# 回测策略
python -m fund_research.main --backtest sector_rotation
```

## 进度

- [x] 项目骨架搭建
- [ ] 基金数据获取
- [ ] 净值分析
- [ ] 持仓穿透
- [ ] 基金筛选器
- [ ] 指数增强策略
- [ ] 行业轮动策略
- [ ] ETF动量策略
- [ ] 回测引擎
