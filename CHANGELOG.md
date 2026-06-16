# Sequoia-X V3 变更记录

> 生成日期: 2026-06-10
> 会话: 代码审查 + 修复 + 功能增强

---

## 一、数据问题诊断与修复

### 发现的问题
- `sync_today_bulk()` 中 `DELETE FROM stock_daily WHERE date = ?` 删除某日 ALL 股票数据
- 然后只重写 volume>0 的 ~2126 只，其余 ~3072 只永久丢失数据
- 导致数据库 5.28~6.9 期间仅 40.8% 股票有数据

### 修复
1. **DELETE 改为精准删除** (`data/engine.py`)
   - 旧: `DELETE FROM stock_daily WHERE date = ?`
   - 新: `DELETE FROM stock_daily WHERE date = ? AND symbol IN (...)`
2. **多进程"用户未登录"重试** (`data/engine.py` - `_bs_fetch_batch`)
   - 检测到"未登录"错误时自动 logout+login 重试
   - 188 只失败降至 5 只
3. **补全缺失的 Pool 导入** (`data/engine.py` - `backfill_parallel`)
4. **_is_trading_day() 改进** (`main.py`)
   - 旧: 仅判断周末
   - 新: baostock query_trade_dates() API，含节假日判断
5. **数据恢复**: 运行 --parallel-backfill，数据覆盖从 40.8% 恢复到 99.8%

---

## 二、代码修复

### backtest/engine.py
- 完整重写 `run()` 方法，清除 `_rewrite_engine.py` 造成的残缺代码
- 预加载全市场数据到内存缓存加速
- 基准曲线改用真实沪深300/中证500数据
- 持仓市值准确计算

### data/engine.py
- 清除末尾游离的 `return count`
- 清除 BOM (U+FEFF) 导致的语法错误

### 废弃文件删除
- `_rewrite_engine.py` (会破坏回测引擎)
- `intersection.py`, `print_results.py`, `show_intersection.py`

---

## 三、新增模块

### signal/ - 交易信号模块
- `models.py`: TradeSignal 数据类
  - 买入区间 (entry_zone_low/entry_zone_high)
  - 止损价 / 目标价1/2
  - 建议仓位 / ATR / 置信度
  - risk_reward_ratio() 盈亏比计算
  - summary() 一行摘要
- `generator.py`: SignalGenerator
  - 基于均线/布林带/ATR 生成买入区间
  - 多策略置信度计算
  - 接入 technical.py 支撑压力分析
  - 自动填充股票名称

### recommender/ - 推荐报告模块
- `reporter.py`: ReportGenerator
  - 终端打印结构化报告
  - 飞书卡片推送 Top 10

### ensemble/ - 多策略融合评分
- `consensus.py`: ConsensusEngine
  - 策略数量加分 (每策略+15)
  - 策略类型差异加分 (趋势+反转双类型确认+15)
  - 策略权重加权 (趋势类1.0, RPS 0.9, 反转类0.7~0.8)
  - 反转类小众策略额外加分

### utils/ - 工具模块
- `name_resolver.py`: NameResolver
  - 一次性拉取 baostock 全量股票名称
  - LRU 缓存，全局单例

---

## 四、main.py 重构

### 日常流程架构

```
python main.py

阶段 A: 全6策略并行
  MaVolume + TurtleTrade + HighTightFlag
  + LimitUpShakeout + UptrendLimitDown + RpsBreakout
  → 各策略独立的飞书推送

阶段 B: 多策略融合评分 (ConsensusEngine)
  → 输出 Top 50 排名
  → 保存 Consensus_YYYYMMDD.csv
  → 推送 Top 30 多策略确认股

阶段 C: 精选 Top 5 详细分析
  → 技术面评分
  → 多因子评分 (PE/PB/ROE)
  → 飞书推送详细分析卡片

阶段 D: 结构化交易信号 (SignalGenerator + ReportGenerator)
  → 终端打印推荐报告
  → 飞书推送 Top 10 推荐
```

### CLI 参数
| 参数 | 功能 | 频率 |
|---|---|---|
| (无参数) | 日常增量更新+选股+推荐 | 每天 |
| --backfill | 串行回填历史数据 | 首次/数据损坏 |
| --parallel-backfill | 并行回填 (8进程) | 首次/数据损坏 |
| --rating | 全市场7策略评级 | 按需 |
| --backtest | 策略历史回测 | 按需 |
| --regime | 市场状态检测 | 按需 |
| --factors TOP20 | 多因子选股 | 按需 |
| --install-schedule | 安装每日21:00定时任务 | 一次性 |

---

## 五、待办 (后续阶段)

- [ ] 阶段二续: 改造各策略 run() 直接返回 TradeSignal
- [ ] 阶段三续: 周线/60分钟线多周期确认过滤假突破
- [ ] 阶段四: 申万一级行业轮动评分
- [ ] 阶段四: 实盘模拟记录+命中率追踪
- [ ] 阶段五: 回测驱动的参数自适应优化


## 手机端数据桥接 — 腾讯云COS同步

### 新增功能
1. **COS云端同步** (`sequoia_x/cloud.py`)
   - 新增云端同步模块，通过腾讯云COS XML API实现推荐结果上传
   - 使用HMAC-SHA1签名认证，无需额外SDK依赖
   - 支持自定义存储桶、地域、路径

2. **Settings配置** (`sequoia_x/core/config.py`)
   - 新增COS相关配置字段: cos_bucket, cos_region, cos_secret_id, cos_secret_key, cos_path, cos_enabled
   - 从环境变量或.env文件自动加载

3. **main.py集成**
   - D阶段尾部增加COS上传逻辑
   - 仅在cos_enabled=true且cos_bucket非空时执行
   - 上传Top 20推荐股票的结构化数据（含买卖区间、止损止盈、置信度等）

### 配置方式
在.env中添加以下参数：
- COS_BUCKET=存储桶名称
- COS_REGION=地域（默认ap-guangzhou）
- COS_SECRET_ID=腾讯云API密钥ID
- COS_SECRET_KEY=腾讯云API密钥Key
- COS_PATH=对象路径（默认recommend/today.json）
- COS_ENABLED=true（启用同步）
