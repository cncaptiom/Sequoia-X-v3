import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import '../theme/app_theme.dart';
import '../providers/stock_provider.dart';
import '../providers/watchlist_provider.dart';
import '../providers/position_provider.dart';
import '../models/position.dart';
import '../models/analysis_result.dart';
import '../services/order_suggestion_service.dart';
import '../services/pc_merge.dart';
import '../widgets/kline_chart.dart';
import '../widgets/analysis_section.dart';
import '../models/stock_info.dart';
import '../models/trade_signal.dart';
import '../providers/pc_data_provider.dart';

class StockDetailPage extends StatelessWidget {
  const StockDetailPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Consumer<StockProvider>(
          builder: (_, provider, __) {
            final quote = provider.currentQuote;
            if (quote == null) return const Text('股票详情');
            return Text(quote.name);
          },
        ),
        actions: [
          Consumer2<StockProvider, WatchlistProvider>(
            builder: (context, stockProvider, watchlistProvider, _) {
              final quote = stockProvider.currentQuote;
              if (quote == null) return const SizedBox();

              // 获取股票市场信息
              final stockInfo = stockProvider.currentStock;
              final market = stockInfo?.market ?? 'SH';
              final stock =
                  StockInfo(code: quote.code, name: quote.name, market: market);
              final isWatch =
                  watchlistProvider.watchlist.any((s) => s.code == quote.code);
              final isHeld = watchlistProvider.isHeld(quote.code);

              return Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  // 自选星标
                  IconButton(
                    icon: Icon(
                      isWatch ? Icons.star : Icons.star_border,
                      color: isWatch ? AppTheme.signalBuy : Colors.grey,
                    ),
                    onPressed: () {
                      if (isWatch) {
                        watchlistProvider.remove(stock);
                      } else {
                        watchlistProvider.add(stock);
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text('已添加 ${quote.name} 到自选')),
                        );
                      }
                    },
                  ),
                  // 已买入标记（自动刷新+止损止盈通知）
                  if (isWatch)
                    IconButton(
                      icon: Icon(
                        isHeld
                            ? Icons.shopping_bag
                            : Icons.shopping_bag_outlined,
                        color: isHeld ? AppTheme.signalBuy : Colors.grey,
                      ),
                      tooltip: isHeld ? '标记为已卖出' : '标记为已买入（15秒自动刷新+止损止盈通知）',
                      onPressed: () {
                        watchlistProvider.toggleHold(stock);
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(
                            content: Text(isHeld
                                ? '已将 ${quote.name} 标记为已卖出'
                                : '已将 ${quote.name} 标记为已买入（15秒自动刷新+止损止盈通知）'),
                            duration: const Duration(seconds: 1),
                          ),
                        );
                      },
                    ),
                ],
              );
            },
          ),
          Consumer<StockProvider>(
            builder: (_, provider, __) {
              return Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  // 复制股票代码
                  IconButton(
                    icon: const Icon(Icons.copy, size: 20),
                    tooltip: '复制股票代码',
                    onPressed: () {
                      final code = provider.currentStock?.code ??
                          provider.currentQuote?.code ??
                          '';
                      Clipboard.setData(ClipboardData(text: code));
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                            content: Text('已复制代码 $code'),
                            duration: Duration(seconds: 1)),
                      );
                    },
                  ),
                  // 刷新
                  IconButton(
                    icon: const Icon(Icons.refresh),
                    onPressed: () => provider.refreshCurrentQuote(),
                  ),
                ],
              );
            },
          ),
        ],
      ),
      body: Consumer<StockProvider>(
        builder: (context, provider, _) {
          if (provider.isLoading) {
            return const Center(child: CircularProgressIndicator());
          }

          final quote = provider.currentQuote;
          if (quote == null || provider.error != null) {
            // 无选中股票时，显示PC端推荐列表供快捷点击
            return Consumer<PcDataProvider>(
              builder: (context, pcProvider, _) {
                final picks = pcProvider.topPicks;
                if (picks.isEmpty) {
                  return Center(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(Icons.search_off,
                            size: 48,
                            color: AppTheme.textHint.withOpacity(0.5)),
                        const SizedBox(height: 12),
                        const Text('点击自选股或搜索选择股票查看详情',
                            style: TextStyle(color: AppTheme.textSecondary)),
                      ],
                    ),
                  );
                }
                return ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    const Row(
                      children: [
                        Icon(Icons.computer,
                            color: AppTheme.primaryDark, size: 20),
                        SizedBox(width: 6),
                        Text('PC端推荐股票',
                            style: TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w600,
                                color: AppTheme.textPrimary)),
                      ],
                    ),
                    const SizedBox(height: 8),
                    ...picks.map((pick) =>
                        _buildQuickAccessCard(context, pick, pcProvider)),
                  ],
                );
              },
            );
          }

          final analysis = provider.analysisResult;

          return RefreshIndicator(
            onRefresh: () async {
              await provider.refreshCurrentQuote();
            },
            child: ListView(
              padding: const EdgeInsets.symmetric(vertical: 8),
              children: [
                // 1. 价格卡片（紧凑）
                _buildPriceCard(quote),
                const SizedBox(height: 8),

                // 2. 综合决策卡（整体数据的综合分析）
                Consumer2<PcDataProvider, WatchlistProvider>(
                  builder: (context, pcProvider, watchProvider, _) {
                    final code = provider.currentQuote?.code ?? '';
                    final pcRating = pcProvider.getRating(code);
                    return _buildDecisionCard(
                      pcRating: pcRating,
                      analysis: analysis,
                      currentPrice: quote.currentPrice,
                      isHeld: watchProvider.isHeld(code),
                      marketRegime: pcProvider.marketRegimeLabel,
                      signal: pcProvider.getSignal(code),
                    );
                  },
                ),
                const SizedBox(height: 8),

                // 4. 不同策略分析（PC 标准化评分优先，与决策卡一致）
                if (analysis != null)
                  Consumer<PcDataProvider>(
                    builder: (context, pcProvider, _) {
                      final code4 = provider.currentQuote?.code ?? '';
                      final pcRating4 = pcProvider.getRating(code4);
                      final overridden4 = pcRating4 != null
                          ? mergePcData(analysis, pcRating4)
                          : analysis;
                      return _buildCategoryStrip(overridden4,
                          pcBias: pcProvider.summary?.strategyBias ?? const {});
                    },
                  ),
                const SizedBox(height: 8),

                // 5. K线图
                if (provider.klineData.isNotEmpty) const KLineChart(),
                if (provider.klineData.isEmpty &&
                    !provider.isLoading &&
                    provider.currentQuote != null)
                  _buildKLineLoadingCard(),
                const SizedBox(height: 8),

                // 6. 技术分析详情
                Consumer<PcDataProvider>(
                  builder: (context, pcProvider2, _) {
                    if (analysis == null) return const SizedBox.shrink();
                    final code2 = provider.currentQuote?.code ?? '';
                    final pcRating2 = pcProvider2.getRating(code2);
                    final overridden = pcRating2 != null
                        ? mergePcData(analysis, pcRating2)
                        : analysis;
                    return Column(
                      children: [
                        AnalysisSection(analysis: overridden),
                        const SizedBox(height: 8),
                      ],
                    );
                  },
                ),
                const SizedBox(height: 8),

                // 7. 持仓信息
                _buildPositionSection(context, quote, analysis),
                const SizedBox(height: 8),

                // 8. 条件单建议
                Consumer<PcDataProvider>(
                  builder: (context, pcProvider3, _) {
                    if (analysis == null) return const SizedBox.shrink();
                    final code3 = provider.currentQuote?.code ?? '';
                    final pcRating3 = pcProvider3.getRating(code3);
                    final overridden = pcRating3 != null
                        ? mergePcData(analysis, pcRating3)
                        : analysis;
                    return _buildOrderSuggestions(context, quote, overridden);
                  },
                ),
                const SizedBox(height: 20),
              ],
            ),
          );
        },
      ),
    );
  }

  /// PC 是权威评级源，本地仅离线兜底；合并逻辑统一在 services/pc_merge.dart
  Widget _buildPriceCard(dynamic quote) {
    final changeColor = AppTheme.priceColor(quote.changePercent);
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16),
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)
        ],
      ),
      child: Column(
        children: [
          // 股价 + 涨跌幅
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                quote.priceText,
                style: TextStyle(
                    fontSize: 36,
                    fontWeight: FontWeight.bold,
                    color: changeColor),
              ),
              const SizedBox(width: 12),
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(quote.changePercentText,
                    style: TextStyle(
                        fontSize: 16,
                        color: changeColor,
                        fontWeight: FontWeight.w500)),
              ),
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(quote.changeAmountText,
                    style: TextStyle(fontSize: 14, color: changeColor)),
              ),
              const Spacer(),
            ],
          ),
          const SizedBox(height: 12),
          // 关键指标一行
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              _infoItem('昨收', quote.yesterdayClose.toStringAsFixed(2)),
              _infoItem('今开', quote.openPrice.toStringAsFixed(2)),
              _infoItem('最高', quote.highPrice.toStringAsFixed(2)),
              _infoItem('最低', quote.lowPrice.toStringAsFixed(2)),
              _infoItem('换手', '${quote.turnoverRate.toStringAsFixed(1)}%'),
              _infoItem(
                  '量比',
                  quote.volumeRatio > 0
                      ? quote.volumeRatio.toStringAsFixed(1)
                      : '-'),
            ],
          ),
        ],
      ),
    );
  }

  Widget _infoItem(String label, String value) {
    return Column(
      children: [
        Text(label,
            style: const TextStyle(color: AppTheme.textHint, fontSize: 12)),
        const SizedBox(height: 4),
        Text(value,
            style: const TextStyle(
                color: AppTheme.textPrimary,
                fontSize: 16,
                fontWeight: FontWeight.w500)),
      ],
    );
  }

  // ═══════════════════════════════════════════
  // 综合决策卡（合并评级 + 操盘建议 + 止损止盈）
  // ═══════════════════════════════════════════

  Widget _buildDecisionCard({
    required dynamic pcRating,
    required dynamic analysis,
    required double currentPrice,
    required bool isHeld,
    String marketRegime = '',
    PcTradeSignal? signal,
  }) {
    // — 提取数据（PC端优先） —
    final bool hasPc = pcRating != null;
    final String ratingStr = hasPc ? (pcRating.rating as String? ?? '') : '';
    // 本地离线分数与 PC 截面分不是同一把尺子（见
    // docs/mobile-datalayer-review-2026-10-15.md 第 1 条），**不能复用 PC 的 S/A/B 标签**：
    // PC 缺失时改用「本地估算」，并去掉星级（星级是 PC 分位的表达，本地分没有对应标定）。
    final String label =
        hasPc ? (pcRating.ratingLabel as String? ?? '') : '本地估算';
    final String stars = hasPc ? (pcRating.ratingStars as String? ?? '') : '';
    final double score = hasPc
        ? ((pcRating.totalScore as num?)?.toDouble() ?? 0)
        : (analysis?.totalScore ?? 0);
    final double rr = hasPc
        ? ((pcRating.riskRewardRatio as num?)?.toDouble() ?? 0)
        : (analysis?.riskRewardRatio ?? 0);
    final String rrDisplay = hasPc
        ? ((pcRating.riskRewardText as String?) ?? '')
        : (signal?.riskRewardText ?? '');
    final String zoneStatus = hasPc
        ? ((pcRating.zoneStatus as String?) ?? '')
        : (signal?.zoneStatus ?? '');
    final double? stopLoss = hasPc
        ? (pcRating.stopLossPrice as num?)?.toDouble()
        : analysis?.stopLossPrice;
    final double? takeProfit = hasPc
        ? (pcRating.takeProfitPrice as num?)?.toDouble()
        : analysis?.takeProfitPrice;
    final String buyRange = hasPc ? (pcRating.buyRange as String? ?? '') : '';

    // 完整操作信号（signals/today.json：形态分/仓位/理由）
    final double sigConfidence = signal?.confidence ?? 0;
    final double sigPositionPct = signal?.positionPct ?? 0;
    final String sigRationale = signal?.rationale ?? '';
    final String sigEntryCondition = signal?.entryCondition ?? '';

    // 解析买入区间
    double entryLow = 0, entryHigh = 0;
    if (buyRange.isNotEmpty) {
      final parts = buyRange.split(' - ');
      if (parts.length == 2) {
        entryLow = double.tryParse(parts[0]) ?? 0;
        entryHigh = double.tryParse(parts[1]) ?? 0;
      }
    }

    // 评级判定。
    // PC 口径：S >=1.5(前0.2%) / A >=1.0(前7%) / B >=0.5(前28%)，颜色对齐全站
    // （红=强买入、橙=买入、琥珀=持有、灰=普通）。
    // 本地分没有自己的标定，**不套用这套档位配色**，只给中性色。
    // ⚠️ 遗留：下面的建仓/关注/观望分支仍在用同一套 PC 阈值判本地分，
    // 这一层尚未处理（见 docs/mobile-datalayer-review-2026-10-15.md）。
    bool isTop = score >= AppTheme.scoreA; // 前7%的股票
    bool isAvoid = ratingStr == 'avoid';
    Color ratingColor;
    if (!hasPc) {
      ratingColor = AppTheme.textHint;
    } else if (score >= AppTheme.scoreS) {
      ratingColor = AppTheme.gradeS;
    } else if (score >= AppTheme.scoreA) {
      ratingColor = AppTheme.gradeA;
    } else if (score >= AppTheme.scoreB) {
      ratingColor = AppTheme.gradeB;
    } else {
      ratingColor = AppTheme.gradeC;
    }

    // 价格相对买入区间的位置 + 评分 → 操盘动作
    String action, aggText, conText, sigText;
    Color actionColor;
    double deviation = 0;

    if (isHeld) {
      // 已持仓 → 止损止盈逻辑
      if (stopLoss != null && currentPrice <= stopLoss) {
        action = "止损";
        actionColor = AppTheme.signalSell;
        aggText = "市价卖出";
        conText = "跌破${stopLoss.toStringAsFixed(2)}离场";
        sigText = "严格执行止损";
      } else if (takeProfit != null && currentPrice >= takeProfit) {
        action = "止盈";
        actionColor = AppTheme.signalBuy;
        aggText = "全部止盈";
        conText = "先卖一半锁利";
        sigText = "注意MACD顶背离";
      } else if (stopLoss != null && currentPrice <= stopLoss * 1.05) {
        action = "警戒";
        actionColor = AppTheme.signalHold;
        aggText = "准备止损";
        conText = "减仓降风险";
        sigText = "等反弹再减";
      } else {
        action = "持有";
        actionColor = AppTheme.signalHold;
        aggText = "移动止损";
        conText = "持有待涨";
        sigText = "未触信号";
      }
    } else if (isAvoid) {
      action = "回避";
      actionColor = AppTheme.signalSell;
      aggText = "不建议";
      conText = "评级回避";
      sigText = "等待后续";
    } else if (entryLow <= 0 || entryHigh <= 0) {
      action = "观察";
      actionColor = AppTheme.textHint;
      aggText = zoneStatus == 'none' ? "只观察" : "无数据";
      conText = zoneStatus == 'none' ? "本期只观察" : "数据不足";
      sigText = "-";
    } else {
      deviation =
          entryLow > 0 ? ((currentPrice - entryLow) / entryLow * 100) : 0;
      final bool strongSignal = isTop;
      // 根据市场状态调整动作强度
      bool allowBuy =
          marketRegime.contains('强牛') || marketRegime.contains('弱牛');
      bool allowAttention = !marketRegime.contains('强熊');
      if (currentPrice <= entryHigh) {
        // 价格在买入区间或以下
        if (currentPrice < entryLow * 0.95) {
          action = strongSignal ? "关注" : "等待";
          actionColor = AppTheme.signalHold;
          aggText = "等企稳";
          conText = "等回到${entryLow.toStringAsFixed(2)}";
          sigText = "等MACD金叉";
        } else if (currentPrice < entryLow) {
          if (strongSignal && allowBuy) {
            action = "低吸";
            actionColor = AppTheme.signalBuy;
            aggText = "接近区间";
            conText = "回踩${entryLow.toStringAsFixed(2)}确认";
            sigText = "等放量";
          } else {
            // C级(score<0.5)即使价格到区间也只观望，不提示"关注"避免误导
            if (score >= AppTheme.scoreB) {
              action = allowAttention ? "关注" : "等待";
              actionColor =
                  allowAttention ? AppTheme.signalHold : AppTheme.textHint;
            } else {
              action = "观望";
              actionColor = AppTheme.textHint;
            }
            aggText = "接近区间";
            conText = "回踩${entryLow.toStringAsFixed(2)}确认";
            sigText = "等放量";
          }
        } else {
          if (strongSignal && allowBuy) {
            action = "建仓";
            actionColor = AppTheme.signalBuy;
            aggText = "正常建仓";
            conText = "回踩${entryLow.toStringAsFixed(2)}加仓";
            sigText = "止损${stopLoss?.toStringAsFixed(2) ?? '-'}";
          } else {
            // C级(score<0.5)即使价格到区间也只观望，不提示"关注"避免误导
            if (score >= AppTheme.scoreB) {
              action = allowAttention ? "关注" : "等待";
              actionColor =
                  allowAttention ? AppTheme.signalHold : AppTheme.textHint;
            } else {
              action = "观望";
              actionColor = AppTheme.textHint;
            }
            aggText = "轻仓试探";
            conText = "回踩${entryLow.toStringAsFixed(2)}确认";
            sigText = "止损${stopLoss?.toStringAsFixed(2) ?? '-'}";
          }
        }
      } else {
        // 区间只作风险参考：高于区间仍可按现价买，但明确标注追高
        action = "追高";
        actionColor = AppTheme.signalHold;
        aggText = "高于支撑区";
        conText = "可按现价买，注意追高";
        sigText = "止损${stopLoss?.toStringAsFixed(2) ?? '-'}";
      }
    }

    // 本地数据（PC 未同步）时，动作与下面的建议仍是用同一套 PC 阈值算出来的，
    // 阈值本身并没有针对本地分标定——标注来源，避免用户把「离线估算的建仓」
    // 当成 PC 的判断。
    if (!hasPc) {
      action = '估算·$action';
    }

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: ratingColor.withOpacity(0.3), width: 1.5),
        boxShadow: [
          BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 8)
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── 第一行：标题 + 等级 ──
          Row(
            children: [
              const Icon(Icons.auto_awesome,
                  size: 18, color: Color(0xFF1565C0)),
              const SizedBox(width: 6),
              const Text('综合决策',
                  style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
              const Spacer(),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: ratingColor.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(8),
                ),
                // 星级与标签分两行，避免窄屏一行溢出/拥挤
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(stars,
                        style: TextStyle(
                            fontSize: 11, height: 1.1, color: ratingColor)),
                    Text(label,
                        style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.bold,
                            color: ratingColor)),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),

          // ── 第二行：评级星级 + 动作标签 ──
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: actionColor.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(action,
                      style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                          color: actionColor)),
                ),
                const SizedBox(width: 12),
                Text('${score.toStringAsFixed(1)}分',
                    style: const TextStyle(
                        fontSize: 13, fontWeight: FontWeight.w500)),
                if (rr > 0) ...[
                  const SizedBox(width: 8),
                  Text('盈亏比 ${rr.toStringAsFixed(1)}:1',
                      style: const TextStyle(
                          fontSize: 12, color: AppTheme.textHint)),
                ] else if (rrDisplay.isNotEmpty && rrDisplay != '—') ...[
                  const SizedBox(width: 8),
                  Text('盈亏比 $rrDisplay',
                      style: const TextStyle(
                          fontSize: 12, color: AppTheme.textHint)),
                ],
                if (deviation != 0) ...[
                  const SizedBox(width: 8),
                  Text(
                      '偏离 ${deviation >= 0 ? "+" : ""}${deviation.toStringAsFixed(1)}%',
                      style: TextStyle(
                          fontSize: 12,
                          color: deviation.abs() > 5
                              ? AppTheme.downColor
                              : AppTheme.textHint)),
                ],
                // 形态分（解释性指标）。评级已在上方单独展示。
                if (sigConfidence > 0) ...[
                  const SizedBox(width: 8),
                  Text(
                      '形态 ${(sigConfidence * 100).round()}%',
                      style: const TextStyle(
                          fontSize: 11, color: AppTheme.textHint)),
                ],
              ],
            ), // Row
          ), // SingleChildScrollView
          const SizedBox(height: 14),

          // ── 第三行：买入区间 + 止损止盈（紧凑） ──
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: [
              if (buyRange.isNotEmpty)
                _infoTag('区间', buyRange, const Color(0xFF1565C0)),
              if (zoneStatus == 'none')
                _infoTag('状态', '只观察', AppTheme.textHint),
              if (stopLoss != null)
                _infoTag('止损', stopLoss.toStringAsFixed(2), AppTheme.downColor),
              if (takeProfit != null)
                _infoTag('止盈', takeProfit.toStringAsFixed(2), AppTheme.upColor),
              if (sigPositionPct > 0)
                _infoTag('仓位', '${(sigPositionPct * 100).round()}%', const Color(0xFF6A1B9A)),
            ],
          ),
          const SizedBox(height: 12),

          // ── 第四行：三段式操作建议（精简标签） ──
          Row(
            children: [
              Expanded(child: _adviceChip3('激进', aggText, AppTheme.signalBuy)),
              const SizedBox(width: 6),
              Expanded(child: _adviceChip3('保守', conText, AppTheme.signalHold)),
              const SizedBox(width: 6),
              Expanded(
                  child: _adviceChip3('信号', sigText, AppTheme.signalWatch)),
            ],
          ),
          // ── 依据说明（signals：触发条件 + 买入理由） ──
          if (sigEntryCondition.isNotEmpty || sigRationale.isNotEmpty) ...[
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: AppTheme.primaryDark.withOpacity(0.05),
                borderRadius: BorderRadius.circular(8),
                border:
                    Border.all(color: AppTheme.primaryDark.withOpacity(0.15)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (sigEntryCondition.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: Text('触发：$sigEntryCondition',
                          style: const TextStyle(
                              fontSize: 11, color: AppTheme.textSecondary)),
                    ),
                  if (sigRationale.isNotEmpty)
                    Text('依据：$sigRationale',
                        style: const TextStyle(
                            fontSize: 11, color: AppTheme.textSecondary)),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _adviceChip3(String label, String text, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: color.withOpacity(0.08),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withOpacity(0.2)),
      ),
      child: Row(
        children: [
          Text(label,
              style: TextStyle(
                  fontSize: 10, fontWeight: FontWeight.bold, color: color)),
          const SizedBox(width: 4),
          Flexible(
            child: Text(text,
                style: TextStyle(fontSize: 10, color: AppTheme.textSecondary),
                maxLines: 1,
                overflow: TextOverflow.ellipsis),
          ),
        ],
      ),
    );
  }
  // ═══════════════════════════════════════════
  // 策略评分明细（可折叠）
  // ═══════════════════════════════════════════

  Widget _buildKLineLoadingCard() {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)
        ],
      ),
      child: Column(
        children: [
          const Icon(Icons.hourglass_empty, size: 36, color: AppTheme.textHint),
          const SizedBox(height: 8),
          const Text('技术分析数据加载中',
              style: TextStyle(color: AppTheme.textSecondary)),
          const SizedBox(height: 4),
          const Text('需要至少20根K线数据',
              style: TextStyle(color: AppTheme.textHint, fontSize: 12)),
        ],
      ),
    );
  }

  // ============================================
  // 三类策略概览（紧凑行）
  // ============================================

  Widget _buildCategoryStrip(dynamic analysis,
      {Map<String, double> pcBias = const {}}) {
    final categories = [
      {
        'name': '趋势',
        'icon': Icons.trending_up,
        'color': Color(0xFF2196F3),
        'strategies': ['maVolume', 'turtleTrade', 'atrChannel'],
        'kw': ['MaVolume', 'Turtle', 'ATR']
      },
      {
        'name': '动量',
        'icon': Icons.compare_arrows,
        'color': Color(0xFF4CAF50),
        'strategies': ['macdRsi', 'maCross'],
        'kw': ['MACD', 'MA20/60', 'MA60']
      },
      {
        'name': '反转',
        'icon': Icons.swap_vert,
        'color': Color(0xFFFF6B35),
        'strategies': ['meanReversion', 'volumePrice', 'kdj'],
        'kw': ['RSI', '量价', 'KDJ', '背离', '超卖', '超买']
      },
    ];

    // 各类别的权重**直接来自 PC 的 REGIME_BIAS**（随 summary 同步下来）。
    //
    // 此前这里硬编码了一份 regimeWeights 表：只有趋势/动量/反转三类、没有 fundamental、
    // 而且数字与 PC 的全部不同（如强熊 PC 是 trend 0.00 / reversal 0.68 / fundamental 0.32，
    // 这里是 0.05 / 0.75）——见 docs/mobile-datalayer-review-2026-10-15.md 第 5 条。
    // PC 数据没到时不显示权重，而不是拿一份近似值冒充。
    final weights = <String, double>{
      if (pcBias['trend'] != null) '趋势': pcBias['trend']!,
      if (pcBias['momentum'] != null) '动量': pcBias['momentum']!,
      if (pcBias['reversal'] != null) '反转': pcBias['reversal']!,
    };
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(color: Colors.black.withOpacity(0.04), blurRadius: 6)
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            const Icon(Icons.category, size: 16, color: AppTheme.primaryDark),
            const SizedBox(width: 6),
            const Text('不同策略分析',
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
          ]),
          const SizedBox(height: 10),
          ...categories.map((cat) => _buildCatDetail(analysis, cat, weights)),
        ],
      ),
    );
  }

  Widget _buildCatDetail(
      dynamic analysis, Map<String, dynamic> cat, Map<String, double> weights) {
    final strategies = cat['strategies'] as List<String>;
    final color = cat['color'] as Color;
    final kw = cat['kw'] as List<String>;
    final name = cat['name'] as String;
    final weight = weights[name];
    double sum = 0;
    int cnt = 0;
    final stratScores = <MapEntry<String, double>>[];
    for (final s in strategies) {
      final v = analysis.strategyScores[s];
      if (v != null) {
        sum += v;
        cnt++;
        stratScores.add(MapEntry(s, v));
      }
    }
    final avg = cnt > 0 ? sum / cnt : 0.0;
    stratScores.sort((a, b) => b.value.compareTo(a.value));
    final sigs = analysis.signals
        .where((s) => kw.any((k) => s.name.contains(k)))
        .toList();
    final bullish = sigs.where((s) => s.type == SignalType.bullish).length;
    final bearish = sigs.where((s) => s.type == SignalType.bearish).length;

    String action;
    Color ac;
    // 阈值对齐 PC 截面标准化评分尺度（PC strategy_scores 为标准化 Z 值，均值≈0）
    // 积极≥1.0 / 关注≥0.5 / 谨慎≤-0.5，与决策卡 S/A/B 阈值同量级
    //
    // ⚠️ 数据来源必须一致：PC 数据时 avg 是 PC 的 Z 分，而 signals 仍是手机端本地生成的，
    // 两者来源/量纲不同，混进同一个判断会互相矛盾（见 mobile-datalayer-review 第 3、7 条）。
    // 所以 PC 数据只按 avg 判，本地数据才用「avg + 信号数」。
    // （PC 的 signals 是**没有类型的裸字符串**，且 PC 评级导出里根本没有这个字段，
    //  换不过来——除非先在 PC 侧给信号补类型。）
    final bool fromPc = !analysis.isLocalEstimate;
    if (avg >= 1.0 && (fromPc || bullish >= 1)) {
      action = '积极';
      ac = AppTheme.signalBuy;
    } else if (avg <= -0.5 && (fromPc || bearish >= 1)) {
      action = '谨慎';
      ac = AppTheme.downColor;
    } else if (avg >= 0.5) {
      action = '关注';
      ac = AppTheme.signalHold;
    } else {
      action = '观望';
      ac = AppTheme.textHint;
    }

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: color.withOpacity(0.04),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withOpacity(0.15)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Icon(cat['icon'] as IconData, size: 14, color: color),
            const SizedBox(width: 4),
            Text(cat['name'] as String,
                style: TextStyle(
                    fontWeight: FontWeight.w600, fontSize: 13, color: color)),
            if (weight != null) ...[
              const SizedBox(width: 4),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                decoration: BoxDecoration(
                  color: AppTheme.textHint.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(3),
                ),
                child: const Text('等权',
                    style: TextStyle(
                        fontSize: 9,
                        fontWeight: FontWeight.w500,
                        color: AppTheme.textHint)),
              ),
            ],
            const SizedBox(width: 4),
            Text(
                avg >= 0
                    ? '+' + avg.toStringAsFixed(1)
                    : avg.toStringAsFixed(1),
                style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.bold,
                    color: avg > 0 ? color : AppTheme.textHint)),
            const Spacer(),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
              decoration: BoxDecoration(
                color: ac.withOpacity(0.12),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text(action,
                  style: TextStyle(
                      fontSize: 10, fontWeight: FontWeight.bold, color: ac)),
            ),
            if (bullish > 0 || bearish > 0) ...[
              const SizedBox(width: 6),
              Text('$bullish↑ $bearish↓',
                  style: TextStyle(fontSize: 9, color: AppTheme.textHint)),
            ],
          ]),
          const SizedBox(height: 4),
          Row(
              children: stratScores.map((e) {
            final labels = {
              'maVolume': '均量',
              'turtleTrade': '海龟',
              'atrChannel': 'ATR',
              'macdRsi': 'MACD',
              'maCross': 'MA交叉',
              'meanReversion': '均值',
              'volumePrice': '量价',
              'kdj': 'KDJ'
            };
            final lb = labels[e.key] ?? e.key;
            return Padding(
              padding: EdgeInsets.only(right: 6),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                decoration: BoxDecoration(
                  color: AppTheme.signalBuy.withOpacity(0.08),
                  borderRadius: BorderRadius.circular(3),
                ),
                child: Text(
                    '$lb${e.value >= 0 ? "+" : ""}${e.value.toStringAsFixed(1)}',
                    style: TextStyle(
                        fontSize: 9,
                        fontWeight: FontWeight.w500,
                        color: AppTheme.signalBuy)),
              ),
            );
          }).toList()),
          if (sigs.isNotEmpty) ...[
            const SizedBox(height: 4),
            ...sigs.take(2).map((s) => Row(children: [
                  Icon(
                      s.type == SignalType.bullish
                          ? Icons.arrow_upward
                          : Icons.arrow_downward,
                      size: 10,
                      color: s.type == SignalType.bullish
                          ? AppTheme.upColor
                          : AppTheme.downColor),
                  const SizedBox(width: 3),
                  Expanded(
                      child: Text(s.name,
                          style: const TextStyle(
                              fontSize: 10, color: AppTheme.textSecondary))),
                ])),
          ],
        ],
      ),
    );
  }

  /// 持仓信息卡片（含录入表单）
  Widget _buildPositionSection(
      BuildContext context, dynamic quote, dynamic analysis) {
    final positionProvider = context.watch<PositionProvider>();
    final stockProvider = context.watch<StockProvider>();
    final stock = stockProvider.currentStock;
    final code = quote.code;
    final pos = positionProvider.getPosition(code);

    if (pos != null) {
      // 已有持仓 → 显示盈亏
      final pl = pos.profitLoss(quote.currentPrice);
      final plp = pos.profitLossPercent(quote.currentPrice);
      final plColor = pl >= 0 ? AppTheme.upColor : AppTheme.downColor;
      final sign = pl >= 0 ? '+' : '';

      return Container(
        margin: const EdgeInsets.symmetric(horizontal: 16),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          boxShadow: [
            BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)
          ],
        ),
        child: Row(
          children: [
            Container(
              width: 4,
              height: 48,
              decoration: BoxDecoration(
                color: plColor,
                borderRadius: BorderRadius.circular(2),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('持仓',
                      style:
                          TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                  const SizedBox(height: 4),
                  Text(
                    '成本 ${pos.buyPrice.toStringAsFixed(2)} × ${pos.quantity}股',
                    style: const TextStyle(
                        color: AppTheme.textSecondary, fontSize: 12),
                  ),
                  Text(
                    '买入 ${pos.buyDate.toLocal().toString().substring(0, 10)}',
                    style:
                        const TextStyle(color: AppTheme.textHint, fontSize: 11),
                  ),
                ],
              ),
            ),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text('${sign}${pl.toStringAsFixed(0)}',
                    style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.bold,
                        color: plColor)),
                Text('${sign}${plp.toStringAsFixed(1)}%',
                    style: TextStyle(fontSize: 13, color: plColor)),
              ],
            ),
            const SizedBox(width: 8),
            IconButton(
              icon: const Icon(Icons.edit_square,
                  size: 18, color: AppTheme.textHint),
              tooltip: '编辑持仓',
              onPressed: () => _showPositionEditor(context, pos, stock, code),
            ),
            IconButton(
              icon: const Icon(Icons.delete_outline,
                  size: 18, color: AppTheme.downColor),
              tooltip: '删除持仓',
              onPressed: () {
                positionProvider.removePosition(code);
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                      content: Text('持仓已删除'), duration: Duration(seconds: 1)),
                );
              },
            ),
          ],
        ),
      );
    }

    // 无持仓 → 显示录入按钮（仅已买入股票可添加）
    if (stock == null) return const SizedBox();
    final watchlistProvider = context.watch<WatchlistProvider>();
    if (!watchlistProvider.isHeld(code)) return const SizedBox();

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)
        ],
      ),
      child: Row(
        children: [
          const Icon(Icons.add_shopping_cart,
              size: 18, color: AppTheme.primaryDark),
          const SizedBox(width: 8),
          const Expanded(child: Text('记录持仓盈亏', style: TextStyle(fontSize: 13))),
          TextButton(
            onPressed: () => _showPositionEditor(context, null, stock, code),
            child: const Text('录入持仓', style: TextStyle(fontSize: 13)),
          ),
        ],
      ),
    );
  }

  /// 持仓编辑对话框
  void _showPositionEditor(
      BuildContext context, Position? existing, StockInfo? stock, String code) {
    final buyPriceCtrl =
        TextEditingController(text: existing?.buyPrice.toString() ?? '');
    final quantityCtrl =
        TextEditingController(text: existing?.quantity.toString() ?? '100');
    DateTime buyDate = existing?.buyDate ?? DateTime.now();

    showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          title: Text(existing != null ? '编辑持仓' : '录入持仓'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: buyPriceCtrl,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(
                    labelText: '买入价', hintText: '如 168.50'),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: quantityCtrl,
                keyboardType: TextInputType.number,
                decoration:
                    const InputDecoration(labelText: '持股数量', hintText: '如 100'),
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  const Text('买入日期: ', style: TextStyle(fontSize: 13)),
                  TextButton(
                    onPressed: () async {
                      final picked = await showDatePicker(
                        context: ctx,
                        initialDate: buyDate,
                        firstDate: DateTime(2020),
                        lastDate: DateTime.now(),
                      );
                      if (picked != null)
                        setDialogState(() => buyDate = picked);
                    },
                    child: Text(buyDate.toLocal().toString().substring(0, 10)),
                  ),
                ],
              ),
            ],
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.of(ctx).pop(),
                child: const Text('取消')),
            FilledButton(
              onPressed: () {
                final price = double.tryParse(buyPriceCtrl.text);
                final qty = int.tryParse(quantityCtrl.text);
                if (price == null || qty == null || price <= 0 || qty <= 0)
                  return;

                final position = Position(
                  code: code,
                  name: stock?.name ?? code,
                  market: stock?.market ?? 'SH',
                  buyPrice: price,
                  quantity: qty,
                  buyDate: buyDate,
                );

                final provider = context.read<PositionProvider>();
                if (existing != null) {
                  // 找到索引并替换
                  final idx =
                      provider.positions.indexWhere((p) => p.code == code);
                  if (idx >= 0) provider.updatePosition(idx, position);
                } else {
                  provider.addPosition(position);
                }

                Navigator.of(ctx).pop();
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(
                      content: Text(existing != null ? '持仓已更新' : '持仓已录入'),
                      duration: Duration(seconds: 1)),
                );
              },
              child: const Text('保存'),
            ),
          ],
        ),
      ),
    );
  }

  /// 条件单建议卡片
  Widget _buildOrderSuggestions(
      BuildContext context, dynamic quote, AnalysisResult analysis) {
    final positionProvider = context.watch<PositionProvider>();
    final watchlistProvider = context.watch<WatchlistProvider>();
    final stockProvider = context.watch<StockProvider>();
    final code = quote.code;
    final isHeld = watchlistProvider.isHeld(code);
    final pos = positionProvider.getPosition(code);
    final shares = pos?.quantity ?? 100;
    final style = stockProvider.strategyConfig.defaultStyle;

    final suggestions = OrderSuggestionService.generate(
      analysis: analysis,
      currentPrice: quote.currentPrice,
      rating: analysis.rating,
      isHeld: isHeld,
      positionShares: shares,
      style: style,
    );

    if (suggestions.isEmpty) return const SizedBox();

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: ExpansionTile(
          shape: const Border(),
          collapsedShape: const Border(),
          tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          leading: const Icon(Icons.assignment,
              size: 18, color: AppTheme.primaryDark),
          title: Text('条件单建议 · ${style.label}',
              style:
                  const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
          subtitle: const Text('招商证券',
              style: TextStyle(fontSize: 11, color: AppTheme.textHint)),
          children: [
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                ...suggestions.map((s) => _buildSuggestionTile(context, s)),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSuggestionTile(BuildContext context, dynamic suggestion) {
    final isBuy = suggestion.type.action == '买入';
    final color = isBuy ? AppTheme.upColor : AppTheme.downColor;

    return Container(
      margin: const EdgeInsets.only(bottom: 6),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: color.withOpacity(0.05),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withOpacity(0.2)),
      ),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            decoration: BoxDecoration(
              color: color.withOpacity(0.15),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Text(suggestion.type.label,
                style: TextStyle(
                    fontSize: 11, fontWeight: FontWeight.bold, color: color)),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(suggestion.description,
                    style: const TextStyle(
                        fontSize: 12, color: AppTheme.textSecondary)),
                const SizedBox(height: 2),
                Text(
                  suggestion.clipboardText,
                  style:
                      const TextStyle(fontSize: 11, color: AppTheme.textHint),
                ),
              ],
            ),
          ),
          IconButton(
            icon: const Icon(Icons.copy, size: 16, color: AppTheme.primaryDark),
            tooltip: '复制条件单参数',
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
            onPressed: () {
              Clipboard.setData(ClipboardData(text: suggestion.clipboardText));
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text('已复制: ${suggestion.clipboardText}'),
                  duration: const Duration(seconds: 2),
                ),
              );
            },
          ),
        ],
      ),
    );
  }

  Widget _infoTag(String label, String value, Color color) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          decoration: BoxDecoration(
            color: color.withOpacity(0.1),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Text('$label $value',
              style: TextStyle(
                  fontSize: 11, fontWeight: FontWeight.w600, color: color)),
        ),
      ],
    );
  }

  /// 快捷进入卡片（详情页空白时显示PC推荐）
  Widget _buildQuickAccessCard(
      BuildContext context, dynamic pick, dynamic pcProvider) {
    String actionText = '';
    try {
      final pcRating = pcProvider.getRating(pick.symbol);
      if (pcRating != null) {
        final buyRange = (pcRating.buyRange as String?) ?? '';
        if (buyRange.isNotEmpty) {
          final parts = buyRange.split(' - ');
          if (parts.length == 2) {
            final low = double.tryParse(parts[0]) ?? 0;
            final high = double.tryParse(parts[1]) ?? 0;
            if (low > 0) actionText = '$low-${high.toStringAsFixed(1)}';
          }
        }
      }
    } catch (_) {}

    return Card(
      margin: const EdgeInsets.symmetric(vertical: 4),
      child: ListTile(
        leading: Container(
          width: 4,
          height: 40,
          decoration: BoxDecoration(
            color: AppTheme.primaryDark,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        title: Text(pick.name,
            style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(
            '${pick.symbol}  ${pick.ratingLabel}  ${pick.totalScore.toStringAsFixed(1)}分',
            style: const TextStyle(fontSize: 12)),
        trailing: actionText.isNotEmpty
            ? Text(actionText,
                style:
                    const TextStyle(color: AppTheme.primaryDark, fontSize: 11))
            : null,
        onTap: () {
          final stock = StockInfo(
              code: pick.symbol,
              name: pick.name,
              market: _inferMarket(pick.symbol));
          context.read<StockProvider>().loadStockDetail(stock);
        },
      ),
    );
  }

  /// 从代码推断市场
  String _inferMarket(String code) {
    if (code.startsWith('6') || code.startsWith('9')) return 'SH';
    if (code.startsWith('0') || code.startsWith('3')) return 'SZ';
    if (code.startsWith('8') || code.startsWith('4')) return 'BJ';
    return 'SH';
  }
}
