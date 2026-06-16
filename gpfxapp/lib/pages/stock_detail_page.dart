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
import '../models/strategy_config.dart';
import '../widgets/kline_chart.dart';
import '../widgets/analysis_section.dart';
import '../widgets/signal_badge.dart';
import '../models/stock_info.dart';
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
              final stock = StockInfo(code: quote.code, name: quote.name, market: market);
              final isWatch = watchlistProvider.watchlist.any((s) => s.code == quote.code);
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
                        isHeld ? Icons.shopping_bag : Icons.shopping_bag_outlined,
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
                      final code = provider.currentStock?.code ?? provider.currentQuote?.code ?? '';
                      Clipboard.setData(ClipboardData(text: code));
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text('已复制代码 $code'), duration: Duration(seconds: 1)),
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
                        Icon(Icons.search_off, size: 48, color: AppTheme.textHint.withOpacity(0.5)),
                        const SizedBox(height: 12),
                        const Text('点击自选股或搜索选择股票查看详情', style: TextStyle(color: AppTheme.textSecondary)),
                      ],
                    ),
                  );
                }
                return ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    const Row(
                      children: [
                        Icon(Icons.computer, color: AppTheme.primaryDark, size: 20),
                        SizedBox(width: 6),
                        Text('PC端推荐股票', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: AppTheme.textPrimary)),
                      ],
                    ),
                    const SizedBox(height: 8),
                    ...picks.map((pick) => _buildQuickAccessCard(context, pick, pcProvider)),
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
                // 价格卡片
                _buildPriceCard(quote),
                const SizedBox(height: 8),

                // 实时操盘建议（基于PC端推荐数据+实时价+持仓状态）
                Consumer2<PcDataProvider, WatchlistProvider>(
                  builder: (context, pcProvider, watchProvider, _) {
                    final code = provider.currentQuote?.code ?? '';
                    final pcRating = pcProvider.getRating(code);
                    if (pcRating == null) return const SizedBox.shrink();
                    return _buildTradingAdvisor(pcRating, quote.currentPrice,
                        isHeld: watchProvider.isHeld(code));
                  },
                ),

                // 持仓信息
                _buildPositionSection(context, quote, analysis),

                // PC端评级 + 综合评级（当PC端数据存在时以PC端为准）
                Consumer<PcDataProvider>(
                  builder: (context, pcProvider, _) {
                    if (!pcProvider.hasRatings) {
                      if (analysis == null) return const SizedBox.shrink();
                      return _buildRatingCard(analysis, null);
                    }
                    final code = provider.currentQuote?.code ?? '';
                    final pcRating = pcProvider.getRating(code);
                    if (pcRating == null) {
                      if (analysis == null) return const SizedBox.shrink();
                      return _buildRatingCard(analysis, null);
                    }
                    if (analysis != null) {
                      return Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          _buildPcRatingCard(pcRating),
                          const SizedBox(height: 6),
                          _buildRatingCard(analysis, pcRating),
                        ],
                      );
                    }
                    return _buildPcRatingCard(pcRating);
                  },
                ),

                // 三类评分 + 分析详情 + 条件单建议（止损止盈统一用PC数据）
                Consumer<PcDataProvider>(
                  builder: (context, pcProvider2, _) {
                    if (analysis == null) return const SizedBox.shrink();
                    final code2 = provider.currentQuote?.code ?? '';
                    final pcRating2 = pcProvider2.getRating(code2);
                    // 用PC止损止盈覆盖本地数据
                    final overridden = pcRating2 != null ? _overrideStopTp(analysis, pcRating2) : analysis;

                    return Column(
                      children: [
                        const SizedBox(height: 8),
                        _buildCategoryScores(overridden, quote.currentPrice),

                        const SizedBox(height: 8),

                        // K线图
                        if (provider.klineData.isNotEmpty)
                          const KLineChart(),
                        if (provider.klineData.isEmpty && !provider.isLoading && provider.currentQuote != null)
                          _buildKLineLoadingCard(),

                        const SizedBox(height: 8),
                        AnalysisSection(analysis: overridden),

                        const SizedBox(height: 8),
                        _buildOrderSuggestions(context, quote, overridden),

                        const SizedBox(height: 20),
                      ],
                    );
                  },
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  /// 用PC止损止盈覆盖本地分析结果
  AnalysisResult _overrideStopTp(AnalysisResult original, dynamic pcRating) {
    final pcStop = (pcRating.stopLossPrice as num?)?.toDouble();
    final pcTp = (pcRating.takeProfitPrice as num?)?.toDouble();
    final pcRange = (pcRating.buyRange as String?);
    if (pcStop == null && pcTp == null && pcRange == null) return original;
    return AnalysisResult(
      rating: original.rating,
      totalScore: original.totalScore,
      strategyScores: original.strategyScores,
      trendPassed: original.trendPassed,
      trendDescription: original.trendDescription,
      riskRewardRatio: original.riskRewardRatio,
      stopLossPrice: pcStop ?? original.stopLossPrice,
      takeProfitPrice: pcTp ?? original.takeProfitPrice,
      buyRange: pcRange ?? original.buyRange,
      signals: original.signals,
      time: original.time,
    );
  }

  Widget _buildPriceCard(dynamic quote) {
    final changeColor = AppTheme.priceColor(quote.changePercent);
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16),
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)],
      ),
      child: Column(
        children: [
          Text(
            quote.priceText,
            style: TextStyle(
              fontSize: 36,
              fontWeight: FontWeight.bold,
              color: changeColor,
            ),
          ),
          const SizedBox(height: 4),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(quote.changePercentText,
                  style: TextStyle(fontSize: 16, color: changeColor, fontWeight: FontWeight.w500)),
              const SizedBox(width: 8),
              Text(quote.changeAmountText,
                  style: TextStyle(fontSize: 16, color: changeColor, fontWeight: FontWeight.w500)),
            ],
          ),
          const SizedBox(height: 16),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              _infoItem('昨收', quote.yesterdayClose.toStringAsFixed(2)),
              _infoItem('今开', quote.openPrice.toStringAsFixed(2)),
              _infoItem('最高', quote.highPrice.toStringAsFixed(2)),
              _infoItem('最低', quote.lowPrice.toStringAsFixed(2)),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              _infoItem('量比', quote.volumeRatio > 0 ? quote.volumeRatio.toString() : '-'),
              _infoItem('换手', '${quote.turnoverRate.toStringAsFixed(2)}%'),
              _infoItem('PE', quote.pe > 0 ? quote.pe.toStringAsFixed(1) : '-'),
              _infoItem('市值', '${quote.marketCap.toStringAsFixed(0)}亿'),
            ],
          ),
        ],
      ),
    );
  }

  Widget _infoItem(String label, String value) {
    return Column(
      children: [
        Text(label, style: const TextStyle(color: AppTheme.textHint, fontSize: 12)),
        const SizedBox(height: 4),
        Text(value, style: const TextStyle(color: AppTheme.textPrimary, fontSize: 16, fontWeight: FontWeight.w500)),
      ],
    );
  }

  /// PC端评级卡片
  /// PC端评级卡片
  Widget _buildPcRatingCard(dynamic pcRating) {
    final label = pcRating.ratingLabel ?? '未知';
    final starStr = pcRating.ratingStars ?? '★';
    final scoreVal = (pcRating.totalScore as num?)?.toDouble() ?? 0;
    final buyStr = (pcRating.buyRange as String?) ?? '';
    final stopVal = pcRating.stopLossPrice;
    final takeVal = pcRating.takeProfitPrice;
    final rewardVal = (pcRating.riskRewardRatio as num?)?.toDouble() ?? 0;

    bool isStrongBuy = pcRating.rating == 'strong_buy';
    bool isBuy = pcRating.rating == 'buy';
    bool isAvoid = pcRating.rating == 'avoid';
    Color borderColor;
    if (isStrongBuy) {
      borderColor = const Color(0xFF4CAF50);
    } else if (isBuy) {
      borderColor = const Color(0xFF8BC34A);
    } else if (isAvoid) {
      borderColor = const Color(0xFFE53935);
    } else {
      borderColor = Colors.orange;
    }

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: borderColor.withOpacity(0.4), width: 1.5),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 8)],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.computer, size: 18, color: Color(0xFF1565C0)),
              const SizedBox(width: 6),
              const Text('PC端评级', style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: Color(0xFF1565C0))),
              const Text(' 每日更新', style: TextStyle(fontSize: 10, color: Color(0xFF90CAF9))),
              const Spacer(),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: borderColor.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text('$starStr $label',
                    style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: borderColor)),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              Text('总分: ${scoreVal.toStringAsFixed(1)}', style: const TextStyle(fontSize: 12, color: Color(0xFF666666))),
              if (rewardVal > 0) ...[
                const SizedBox(width: 12),
                Text('盈亏比: ${rewardVal.toStringAsFixed(1)}:1',
                    style: const TextStyle(fontSize: 12, color: Color(0xFF666666))),
              ],
              if (buyStr.isNotEmpty) ...[
                const SizedBox(width: 12),
                Text('买入区间: $buyStr',
                    style: const TextStyle(fontSize: 12, color: Color(0xFF1565C0))),
              ],
            ],
          ),
          if (stopVal != null || takeVal != null) ...[
            const SizedBox(height: 4),
            Row(
              children: [
                if (stopVal != null)
                  Text('止损: ${stopVal.toStringAsFixed(2)}',
                      style: const TextStyle(fontSize: 12, color: Color(0xFFE53935))),
                if (stopVal != null && takeVal != null) const SizedBox(width: 12),
                if (takeVal != null)
                  Text('止盈: ${takeVal.toStringAsFixed(2)}',
                      style: const TextStyle(fontSize: 12, color: Color(0xFF4CAF50))),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildRatingCard(dynamic analysis, [dynamic pcRating]) {
    // PC端字符串评级转换为Rating枚举
    Rating _pcRatingToEnum(String s) {
      switch (s) {
        case "strong_buy": return Rating.strongBuy;
        case "buy": return Rating.buy;
        case "watch": return Rating.watch;
        case "attention": return Rating.attention;
        case "avoid": return Rating.avoid;
        default: return analysis?.rating ?? Rating.attention;
      }
    }

    // 当pcRating存在时，用PC端数据覆盖
    final bool hasPc = pcRating != null;
    final Rating finalRating = hasPc ? _pcRatingToEnum((pcRating.rating as String?) ?? "") : analysis?.rating ?? Rating.attention;
    final double pcRR = hasPc ? ((pcRating.riskRewardRatio as num?)?.toDouble() ?? 0) : 0.0;
    final double? pcStopLoss = hasPc ? (pcRating.stopLossPrice as num?)?.toDouble() : null;
    final double? pcTakeProfit = hasPc ? (pcRating.takeProfitPrice as num?)?.toDouble() : null;
    final String pcTrend = hasPc ? (pcRating.trendDescription as String?) ?? "" : "";
    final double pcScore = hasPc ? ((pcRating.totalScore as num?)?.toDouble() ?? 0) : 0.0;

    final double displayRR = hasPc ? pcRR : (analysis?.riskRewardRatio ?? 0);
    final double? displayStopLoss = hasPc ? pcStopLoss : analysis?.stopLossPrice;
    final double? displayTakeProfit = hasPc ? pcTakeProfit : analysis?.takeProfitPrice;
    final String displayTrend = hasPc ? pcTrend : (analysis?.trendDescription ?? "");

    String stars;
    switch (finalRating) {
      case Rating.strongBuy: stars = "★★★★★"; break;
      case Rating.buy: stars = "★★★★"; break;
      case Rating.watch: stars = "★★★"; break;
      case Rating.attention: stars = "★★"; break;
      case Rating.avoid: stars = "★"; break;
    }

    String label;
    switch (finalRating) {
      case Rating.strongBuy: label = "强烈买入"; break;
      case Rating.buy: label = "买入"; break;
      case Rating.watch: label = "观望"; break;
      case Rating.attention: label = "关注"; break;
      case Rating.avoid: label = "回避"; break;
    }

    Color ratingColor = AppTheme.ratingColorFromEnum(finalRating);

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)],
      ),
      child: Row(
        children: [
          // 评级
          Container(
            width: 80,
            height: 80,
            decoration: BoxDecoration(
              color: AppTheme.ratingColorFromEnum(finalRating).withOpacity(0.1),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Center(
              child: Text(
                stars,
                style: const TextStyle(fontSize: 16),
              ),
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    SignalBadge(rating: finalRating),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(displayTrend,
                          style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                if (displayRR > 0)
                  Text(
                    '盈亏比 ${displayRR.toStringAsFixed(2)}:1',
                    style: const TextStyle(color: AppTheme.textPrimary, fontWeight: FontWeight.w500),
                  ),
                if (displayRR > 0 && displayStopLoss != null)
                  Text(
                    '止损 ${displayStopLoss!.toStringAsFixed(2)}  |  止盈 ${displayTakeProfit?.toStringAsFixed(2) ?? "-"}',
                    style: const TextStyle(color: AppTheme.textHint, fontSize: 13),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildKLineLoadingCard() {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)],
      ),
      child: Column(
        children: [
          const Icon(Icons.hourglass_empty, size: 36, color: AppTheme.textHint),
          const SizedBox(height: 8),
          const Text('技术分析数据加载中', style: TextStyle(color: AppTheme.textSecondary)),
          const SizedBox(height: 4),
          const Text('需要至少20根K线数据', style: TextStyle(color: AppTheme.textHint, fontSize: 12)),
        ],
      ),
    );
  }

  /// 持仓信息卡片（含录入表单）
  Widget _buildPositionSection(BuildContext context, dynamic quote, dynamic analysis) {
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
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)],
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
                  const Text('持仓', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                  const SizedBox(height: 4),
                  Text(
                    '成本 ${pos.buyPrice.toStringAsFixed(2)} × ${pos.quantity}股',
                    style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12),
                  ),
                  Text(
                    '买入 ${pos.buyDate.toLocal().toString().substring(0, 10)}',
                    style: const TextStyle(color: AppTheme.textHint, fontSize: 11),
                  ),
                ],
              ),
            ),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text('${sign}${pl.toStringAsFixed(0)}',
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: plColor)),
                Text('${sign}${plp.toStringAsFixed(1)}%',
                    style: TextStyle(fontSize: 13, color: plColor)),
              ],
            ),
            const SizedBox(width: 8),
            IconButton(
              icon: const Icon(Icons.edit_square, size: 18, color: AppTheme.textHint),
              tooltip: '编辑持仓',
              onPressed: () => _showPositionEditor(context, pos, stock, code),
            ),
            IconButton(
              icon: const Icon(Icons.delete_outline, size: 18, color: AppTheme.downColor),
              tooltip: '删除持仓',
              onPressed: () {
                positionProvider.removePosition(code);
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(content: Text('持仓已删除'), duration: Duration(seconds: 1)),
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
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)],
      ),
      child: Row(
        children: [
          const Icon(Icons.add_shopping_cart, size: 18, color: AppTheme.primaryDark),
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
  void _showPositionEditor(BuildContext context, Position? existing, StockInfo? stock, String code) {
    final buyPriceCtrl = TextEditingController(text: existing?.buyPrice.toString() ?? '');
    final quantityCtrl = TextEditingController(text: existing?.quantity.toString() ?? '100');
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
                decoration: const InputDecoration(labelText: '买入价', hintText: '如 168.50'),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: quantityCtrl,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: '持股数量', hintText: '如 100'),
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
                      if (picked != null) setDialogState(() => buyDate = picked);
                    },
                    child: Text(buyDate.toLocal().toString().substring(0, 10)),
                  ),
                ],
              ),
            ],
          ),
          actions: [
            TextButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('取消')),
            FilledButton(
              onPressed: () {
                final price = double.tryParse(buyPriceCtrl.text);
                final qty = int.tryParse(quantityCtrl.text);
                if (price == null || qty == null || price <= 0 || qty <= 0) return;

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
                  final idx = provider.positions.indexWhere((p) => p.code == code);
                  if (idx >= 0) provider.updatePosition(idx, position);
                } else {
                  provider.addPosition(position);
                }

                Navigator.of(ctx).pop();
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text(existing != null ? '持仓已更新' : '持仓已录入'), duration: Duration(seconds: 1)),
                );
              },
              child: const Text('保存'),
            ),
          ],
        ),
      ),
    );
  }

  /// 按策略类型评分卡片（趋势/反转/信号）
  Widget _buildCategoryScores(AnalysisResult analysis, double currentPrice) {
    // 三类策略归属：策略key → 所属类型
    const categoryDefs = <Map<String, dynamic>>[
      {
        'key': 'aggressive',
        'label': '趋势策略',
        'icon': Icons.trending_up,
        'strategies': ['maVolume', 'turtleTrade', 'atrChannel'],
        'signalKeywords': ['MaVolume', 'Turtle', 'ATR'],
      },
      {
        'key': 'conservative',
        'label': '反转策略',
        'icon': Icons.swap_vert,
        'strategies': ['meanReversion', 'volumePrice'],
        'signalKeywords': ['超卖', '超买', 'RSI-底背离', 'RSI-顶背离', '量价'],
      },
      {
        'key': 'signal',
        'label': '信号策略',
        'icon': Icons.compare_arrows,
        'strategies': ['macdRsi', 'maCross'],
        'signalKeywords': ['MACD', 'MA20/60', 'MA60'],
      },
    ];

    // 解析买入区间
    double? buyZoneLow;
    if (analysis.buyRange != null) {
      final parts = analysis.buyRange!.split(' - ');
      if (parts.length == 2) {
        buyZoneLow = double.tryParse(parts[0]);
      }
    }

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(Icons.category, size: 16, color: AppTheme.primaryDark),
              SizedBox(width: 6),
              Text('按策略类型分析', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
            ],
          ),
          const SizedBox(height: 12),
          ...categoryDefs.map((cat) => _buildCategoryTile(
            analysis, cat, currentPrice, buyZoneLow,
          )),
        ],
      ),
    );
  }

  Widget _buildCategoryTile(
    AnalysisResult analysis,
    Map<String, dynamic> cat,
    double currentPrice,
    double? buyZoneLow,
  ) {
    final strategies = cat['strategies'] as List<String>;
    final label = cat['label'] as String;
    final icon = cat['icon'] as IconData;
    final signalKeywords = cat['signalKeywords'] as List<String>;
    final catKey = cat['key'] as String;

    // 计算该类平均分
    double totalScore = 0;
    int count = 0;
    for (final s in strategies) {
      final score = analysis.strategyScores[s];
      if (score != null) {
        totalScore += score;
        count++;
      }
    }
    final avgScore = count > 0 ? totalScore / count : 0.0;

    // 从信号中筛选属于该类型的信号
    final typeSignals = analysis.signals.where((s) =>
        signalKeywords.any((kw) => s.name.contains(kw))).toList();

    // 生成买卖建议
    final action = _categoryAction(avgScore, typeSignals);
    final isBuy = action == '买入';
    final isSell = action == '卖出';
    final actionColor = isBuy ? AppTheme.upColor
        : isSell ? AppTheme.downColor
        : AppTheme.textHint;

    // 计算该类别的买卖价位
    final stopLoss = analysis.stopLossPrice;
    final takeProfit = analysis.takeProfitPrice;
    final entryPrice = _categoryEntryPrice(catKey, currentPrice, buyZoneLow, stopLoss);
    final entryLabel = _categoryEntryLabel(catKey);

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: actionColor.withOpacity(0.04),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: actionColor.withOpacity(0.15)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 第一行：图标 + 类型名 + 评分 + 买卖建议
          Row(
            children: [
              Icon(icon, size: 16, color: AppTheme.primaryDark),
              const SizedBox(width: 6),
              Text(label, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14)),
              const Spacer(),
              Text(
                avgScore >= 0 ? '+${avgScore.toStringAsFixed(1)}' : avgScore.toStringAsFixed(1),
                style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: actionColor),
              ),
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: actionColor.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(action,
                  style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: actionColor),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          // 第二行：关键信号
          if (typeSignals.isNotEmpty)
            ...typeSignals.take(2).map((s) => Padding(
              padding: const EdgeInsets.only(bottom: 2),
              child: Row(
                children: [
                  Icon(
                    s.type == SignalType.bullish ? Icons.arrow_upward
                        : s.type == SignalType.bearish ? Icons.arrow_downward
                        : Icons.remove,
                    size: 12,
                    color: s.type == SignalType.bullish ? AppTheme.upColor
                        : s.type == SignalType.bearish ? AppTheme.downColor
                        : AppTheme.textHint,
                  ),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(s.name, style: const TextStyle(fontSize: 11, color: AppTheme.textSecondary)),
                  ),
                  Text(
                    s.score >= 0 ? '+${s.score.toStringAsFixed(0)}' : s.score.toStringAsFixed(0),
                    style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600,
                      color: s.score >= 0 ? AppTheme.upColor : AppTheme.downColor),
                  ),
                ],
              ),
            )),
          // 无信号时显示说明
          if (typeSignals.isEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text(_categoryNoSignalHint(catKey),
                style: const TextStyle(fontSize: 11, color: AppTheme.textHint)),
            ),

        ],
      ),
    );
  }

  Widget _priceItem(String label, String value, Color color) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(fontSize: 10, color: AppTheme.textHint)),
        const SizedBox(height: 1),
        Text(value, style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: color)),
      ],
    );
  }

  /// 各类别的入场价计算
  double _categoryEntryPrice(String key, double currentPrice, double? buyZoneLow, double? stopLoss) {
    switch (key) {
      case 'aggressive':
        // 追突破：现价附近或刚过支撑
        if (buyZoneLow != null && buyZoneLow < currentPrice) {
          return buyZoneLow * 1.05; // 突破确认价
        }
        return currentPrice * 0.99;
      case 'conservative':
        // 等回调：接近支撑再买
        if (buyZoneLow != null && buyZoneLow < currentPrice) {
          return buyZoneLow * 1.02; // 支撑上方2%
        }
        if (stopLoss != null && stopLoss < currentPrice) {
          return stopLoss / 0.97 * 1.02; // 从止损反推支撑
        }
        return currentPrice * 0.96;
      case 'signal':
        // 指标确认：支撑附近等信号
        if (buyZoneLow != null && buyZoneLow < currentPrice) {
          return buyZoneLow * 1.03;
        }
        return currentPrice * 0.98;
      default:
        return currentPrice;
    }
  }

  String _categoryEntryLabel(String key) {
    switch (key) {
      case 'aggressive':
        return '突破确认';
      case 'conservative':
        return '回踩买入';
      case 'signal':
        return '信号确认';
      default:
        return '入场价';
    }
  }

  /// 根据平均分和信号生成买卖建议
  String _categoryAction(double avgScore, List<Signal> signals) {
    final buySignals = signals.where((s) => s.type == SignalType.bullish && s.score >= 2).length;
    final sellSignals = signals.where((s) => s.type == SignalType.bearish && s.score <= -2).length;

    if (avgScore >= 3 && buySignals >= 1) return '买入';
    if (avgScore <= -2 && sellSignals >= 1) return '卖出';
    if (avgScore >= 2) return '关注';
    if (avgScore <= -1) return '回避';
    return '观望';
  }

  String _categoryNoSignalHint(String key) {
    switch (key) {
      case 'aggressive':
        return '当前无突破/金叉信号，等待趋势确认';
      case 'conservative':
        return '价格未到极端区间，无逆向交易机会';
      case 'signal':
        return '指标无明显共振信号，保持观望';
      default:
        return '暂无信号';
    }
  }

  /// 条件单建议卡片
  Widget _buildOrderSuggestions(BuildContext context, dynamic quote, AnalysisResult analysis) {
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
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.assignment, size: 18, color: AppTheme.primaryDark),
              const SizedBox(width: 6),
              Text('条件单建议 · ${style.label}', style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600)),
              const Spacer(),
              Text('招商证券', style: TextStyle(fontSize: 11, color: AppTheme.textHint)),
            ],
          ),
          const SizedBox(height: 8),
          ...suggestions.map((s) => _buildSuggestionTile(context, s)),
        ],
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
                style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: color)),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(suggestion.description, style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                const SizedBox(height: 2),
                Text(
                  suggestion.clipboardText,
                  style: const TextStyle(fontSize: 11, color: AppTheme.textHint),
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

  /// 实时操盘建议卡片（基于PC端推荐数据 + 实时行情）
  Widget _buildTradingAdvisor(dynamic pcRating, double currentPrice, {bool isHeld = false}) {
    final buyRange = (pcRating.buyRange as String?) ?? '';
    final stopLoss = (pcRating.stopLossPrice as num?)?.toDouble();
    final takeProfit = (pcRating.takeProfitPrice as num?)?.toDouble();
    final rr = (pcRating.riskRewardRatio as num?)?.toDouble() ?? 0;
    final score = (pcRating.totalScore as num?)?.toDouble() ?? 0;

    // 如果止损价高于现价，说明股价已跌破止损位，止损不再有意义
    final effectiveStopLoss = (stopLoss != null && currentPrice < stopLoss) ? null : stopLoss;
    // 如果止盈价低于现价，说明已突破止盈
    final effectiveTakeProfit = (takeProfit != null && currentPrice > takeProfit) ? null : takeProfit;

    // 解析买入区间
    double entryLow = 0, entryHigh = 0;
    if (buyRange.isNotEmpty) {
      final parts = buyRange.split(' - ');
      if (parts.length == 2) {
        entryLow = double.tryParse(parts[0]) ?? 0;
        entryHigh = double.tryParse(parts[1]) ?? 0;
      }
    }

    // 如果止损价高于现价，说明股价已跌破止损位，止损不再有意义
    final effSl = (stopLoss != null && currentPrice < stopLoss) ? null : stopLoss;
    final effTp = (takeProfit != null && currentPrice > takeProfit) ? null : takeProfit;

    // 判断当前价格相对买入区间的位置 + 持仓状态
    String action, actionDesc, aggText, conText, sigText;
    Color actionColor;

    if (isHeld) {
      // 已持仓 → 根据止损止盈给出操作建议
      if (effSl != null && currentPrice <= effSl) {
        action = "止损"; actionColor = AppTheme.signalSell;
        actionDesc = "已触及止损位，建议卖出";
        aggText = "市价卖出，避免更大亏损";
        conText = "跌破${effSl.toStringAsFixed(2)}无条件离场";
        sigText = "严格执行止损纪律";
      } else if (effTp != null && currentPrice >= effTp) {
        action = "止盈"; actionColor = AppTheme.signalBuy;
        actionDesc = "已达到目标价，可考虑卖出";
        aggText = "现价${currentPrice.toStringAsFixed(2)}全部止盈";
        conText = "分批止盈，先卖一半锁定利润";
        sigText = "涨幅过大，MACD顶背离则全出";
      } else if (effSl != null && currentPrice <= effSl * 1.05) {
        action = "警戒"; actionColor = AppTheme.signalHold;
        actionDesc = "接近止损位，密切关注";
        aggText = "跌破${effSl.toStringAsFixed(2)}立即止损";
        conText = "减仓降低风险";
        sigText = "等反弹再减仓";
      } else if (effTp != null && currentPrice >= effTp * 0.95) {
        action = "接近"; actionColor = AppTheme.signalHold;
        actionDesc = "接近目标价，准备止盈";
        aggText = "接近${effTp.toStringAsFixed(2)}可逐步减仓";
        conText = "设移动止盈保护利润";
        sigText = "关注量能变化";
      } else {
        action = "持有"; actionColor = AppTheme.signalHold;
        actionDesc = "持有中，设好止损止盈";
        aggText = "移动止损至${(currentPrice*0.95).toStringAsFixed(2)}";
        conText = "止损${effSl?.toStringAsFixed(2) ?? '-'} 止盈${effTp?.toStringAsFixed(2) ?? '-'}";
        sigText = "未触发买卖信号，继续持有";
      }
    } else if (entryLow <= 0 || entryHigh <= 0) {
      action = "暂无数据";
      actionColor = AppTheme.textHint;
      actionDesc = "PC端未提供买入区间";
      aggText = conText = sigText = "-";
    } else if (currentPrice < entryLow * 0.95) {
      action = "观望";
      actionColor = AppTheme.signalWatch;
      actionDesc = "价格远低于买入区间，等待企稳";
      aggText = "价格回到${entryLow.toStringAsFixed(2)}以上可考虑";
      conText = "等放量站上${entryLow.toStringAsFixed(2)}后再介入";
      sigText = "等MACD金叉确认再入场";
    } else if (currentPrice < entryLow) {
      action = "关注";
      actionColor = AppTheme.signalWatch;
      actionDesc = "价格接近买入区间下沿，观察支撑";
      aggText = "现价${currentPrice.toStringAsFixed(2)}可轻仓试多";
      conText = "等回踩${entryLow.toStringAsFixed(2)}确认支撑再买入";
      sigText = "回踩${entryLow.toStringAsFixed(2)}+放量可入场";
    } else if (currentPrice <= entryHigh) {
      action = "买入";
      actionColor = AppTheme.signalBuy;
      actionDesc = "价格在买入区间内，可择机入场";
      aggText = "现价${currentPrice.toStringAsFixed(2)}直接入场";
      conText = "等回踩${entryLow.toStringAsFixed(2)}附近买入更安全";
      sigText = "现价入场，止损设在${stopLoss?.toStringAsFixed(2) ?? '-'}";
    } else if (currentPrice <= entryHigh * 1.05) {
      action = "轻仓";
      actionColor = AppTheme.signalHold;
      actionDesc = "价格略高于买入区间，等回调或轻仓";
      aggText = "价格回落至${entryHigh.toStringAsFixed(2)}以下再入场";
      conText = "等回踩${entryLow.toStringAsFixed(2)}附近买入";
      sigText = "MACD金叉且回踩${entryLow.toStringAsFixed(2)}可入场";
    } else {
      action = "等待";
      actionColor = AppTheme.textHint;
      actionDesc = "价格高于买入区间较多，不追高";
      aggText = "等价格回到${entryHigh.toStringAsFixed(2)}以下";
      conText = "耐心等回踩${entryLow.toStringAsFixed(2)}附近";
      sigText = "等明确回调信号再入场";
    }

    // 偏离度
    final midPrice = (entryLow + entryHigh) / 2;
    final deviation = midPrice > 0 ? ((currentPrice - midPrice) / midPrice * 100) : 0.0;
    final deviationText = deviation >= 0 ? "+${deviation.toStringAsFixed(1)}%" : "${deviation.toStringAsFixed(1)}%";
    final deviationColor = deviation > 5 ? AppTheme.downColor : deviation < -5 ? AppTheme.upColor : AppTheme.textSecondary;

    // 置信度等级
    String level;
    Color levelColor;
    if (score >= 6) { level = "S"; levelColor = const Color(0xFF1B5E20); }
    else if (score >= 4) { level = "A"; levelColor = const Color(0xFF2E7D32); }
    else if (score >= 2) { level = "B"; levelColor = const Color(0xFFF57F17); }
    else { level = "C"; levelColor = const Color(0xFFE65100); }

    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: actionColor.withOpacity(0.3), width: 1.5),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 标题行
          Row(
            children: [
              const Icon(Icons.tips_and_updates, size: 18, color: AppTheme.primaryDark),
              const SizedBox(width: 6),
              const Text('实时操盘建议', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
              const Spacer(),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(
                  color: levelColor.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text('Lv.$level', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: levelColor)),
              ),
            ],
          ),
          const SizedBox(height: 10),
          // 状态行
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: actionColor.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(action,
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: actionColor)),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(actionDesc, style: const TextStyle(fontSize: 13, color: AppTheme.textPrimary)),
                    const SizedBox(height: 2),
                    Row(
                      children: [
                        Text('偏离度 ', style: TextStyle(fontSize: 11, color: AppTheme.textHint)),
                        Text(deviationText, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: deviationColor)),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          // 三段式操作建议
          Row(
            children: [
              Expanded(child: _adviceChip('激进', aggText, AppTheme.signalBuy)),
              const SizedBox(width: 6),
              Expanded(child: _adviceChip('保守', conText, AppTheme.signalHold)),
              const SizedBox(width: 6),
              Expanded(child: _adviceChip('信号', sigText, AppTheme.signalWatch)),
            ],
          ),
          // 止损止盈
          if (effSl != null || effTp != null) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                if (effSl != null)
                  _infoTag('止损', effSl.toStringAsFixed(2), AppTheme.downColor),
                if (effSl != null && effTp != null) const SizedBox(width: 12),
                if (effTp != null)
                  _infoTag('止盈', effTp.toStringAsFixed(2), AppTheme.upColor),
                const Spacer(),
                if (rr > 0)
                  Text('盈亏比 ${rr.toStringAsFixed(1)}:1', style: const TextStyle(color: AppTheme.textHint, fontSize: 11)),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _adviceChip(String label, String text, Color color) {
    return Container(
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: color.withOpacity(0.05),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withOpacity(0.2)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: color)),
          const SizedBox(height: 2),
          Text(text, style: TextStyle(fontSize: 10, color: AppTheme.textSecondary), maxLines: 2),
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
          child: Text('$label $value', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: color)),
        ),
      ],
    );
  }

  /// 快捷进入卡片（详情页空白时显示PC推荐）
  Widget _buildQuickAccessCard(BuildContext context, dynamic pick, dynamic pcProvider) {
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
          width: 4, height: 40,
          decoration: BoxDecoration(
            color: AppTheme.primaryDark, borderRadius: BorderRadius.circular(2),
          ),
        ),
        title: Text(pick.name, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text('${pick.symbol}  ${pick.ratingLabel}  ${pick.totalScore.toStringAsFixed(1)}分',
            style: const TextStyle(fontSize: 12)),
        trailing: actionText.isNotEmpty
            ? Text(actionText, style: const TextStyle(color: AppTheme.primaryDark, fontSize: 11))
            : null,
        onTap: () {
          final stock = StockInfo(code: pick.symbol, name: pick.name, market: _inferMarket(pick.symbol));
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




