import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:provider/provider.dart';
import '../theme/app_theme.dart';
import '../providers/watchlist_provider.dart';
import '../providers/stock_provider.dart';
import '../providers/position_provider.dart';
import '../providers/pc_data_provider.dart';
import '../models/stock_quote.dart';
import '../models/analysis_result.dart';
import '../models/stock_info.dart';
import '../models/pc_recommendation.dart';
import '../widgets/market_overview.dart';
import '../widgets/stock_card.dart';

class HomePage extends StatelessWidget {
  final VoidCallback? onNavigateToDetail;
  final VoidCallback? onNavigateToSearch;

  const HomePage({super.key, this.onNavigateToDetail, this.onNavigateToSearch});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('股票分析'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () => context.read<WatchlistProvider>().refreshAll(),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          await Future.wait([
            context.read<WatchlistProvider>().refreshAll(),
            context.read<PcDataProvider>().refresh(),
          ]);
        },
        child: ListView(
          padding: const EdgeInsets.only(top: 8),
          children: [
            // 大盘指数看板
            const MarketOverview(),
            const SizedBox(height: 8),

            Consumer<PcDataProvider>(
              builder: (context, pcProvider, _) {
                // 即使PC端数据未加载完成，也显示标题区域
                final picks = pcProvider.topPicks;
                final regimeLabel = pcProvider.marketRegimeLabel;
                final exposure = pcProvider.suggestedExposure;
                final isLoading = pcProvider.isLoading;
                final hasError = pcProvider.error != null;

                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 8),
                      child: Row(
                        children: [
                          const Icon(Icons.computer,
                              color: AppTheme.primaryDark, size: 20),
                          const SizedBox(width: 6),
                          const Text('PC端精选推荐',
                              style: TextStyle(
                                  fontSize: 16,
                                  fontWeight: FontWeight.w600,
                                  color: AppTheme.textPrimary)),
                          const Spacer(),
                          if (isLoading)
                            const SizedBox(
                              width: 14,
                              height: 14,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          else if (regimeLabel.isNotEmpty)
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 8, vertical: 2),
                              decoration: BoxDecoration(
                                color: AppTheme.primaryDark.withOpacity(0.1),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: Text(regimeLabel,
                                  style: const TextStyle(
                                      fontSize: 11,
                                      color: AppTheme.primaryDark)),
                            ),
                        ],
                      ),
                    ),
                    if (exposure > 0)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        child: Text('建议仓位: ${(exposure * 100).toInt()}%',
                            style: const TextStyle(
                                color: AppTheme.textHint, fontSize: 12)),
                      ),
                    // 推荐统计行
                    if (picks.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        child: _buildPicksSummary(picks),
                      ),
                    // 数据新鲜度
                    if (pcProvider.lastFetch != null)
                      Padding(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 16, vertical: 2),
                        child: Text(
                            '数据更新于 ${pcProvider.lastFetch!.toLocal().toString().substring(0, 16)}',
                            style: const TextStyle(
                                fontSize: 10, color: AppTheme.textHint)),
                      ),
                    if (picks.isEmpty && !isLoading)
                      // 无数据时引导提示
                      Container(
                        margin: const EdgeInsets.symmetric(horizontal: 16),
                        padding: const EdgeInsets.all(16),
                        decoration: BoxDecoration(
                          color: Colors.white,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Row(
                          children: [
                            Icon(Icons.cloud_off,
                                size: 20,
                                color: AppTheme.textHint.withOpacity(0.5)),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                hasError
                                    ? 'PC端数据加载失败，请检查网络'
                                    : 'PC端数据将在每日21:00选股后自动更新',
                                style: const TextStyle(
                                    color: AppTheme.textHint, fontSize: 13),
                              ),
                            ),
                          ],
                        ),
                      ),
                    if (picks.isNotEmpty)
                      ...picks.map((pick) =>
                          _buildPcStockCard(context, pick, pcProvider)),
                    const SizedBox(height: 8),
                  ],
                );
              },
            ),

            // 自选股标题
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                children: [
                  const Icon(Icons.star, color: AppTheme.primaryDark, size: 20),
                  const SizedBox(width: 6),
                  const Text('自选股',
                      style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                          color: AppTheme.textPrimary)),
                  const Spacer(),
                  Consumer<WatchlistProvider>(
                    builder: (_, provider, __) {
                      return Text('${provider.watchlist.length}只',
                          style:
                              const TextStyle(color: AppTheme.textSecondary));
                    },
                  ),
                ],
              ),
            ),

            // 自选股列表
            Consumer<WatchlistProvider>(
              builder: (context, provider, _) {
                if (provider.watchlist.isEmpty) {
                  return _buildEmptyWatchlist(context);
                }

                if (provider.isLoading && provider.quotes.isEmpty) {
                  return const Center(
                      child: Padding(
                    padding: EdgeInsets.all(40),
                    child: CircularProgressIndicator(),
                  ));
                }

                return Column(
                  children: provider.watchlist.map((stock) {
                    final quote = provider.quotes[stock.code];
                    final analysis = provider.analysisResults[stock.code];
                    final position = context
                        .watch<PositionProvider>()
                        .getPosition(stock.code);
                    return StockCard(
                      stock: stock,
                      quote: quote,
                      analysis: analysis,
                      position: position,
                      isHeld: provider.isHeld(stock.code),
                      onTap: () {
                        context.read<StockProvider>().loadStockDetail(stock);
                        onNavigateToDetail?.call();
                      },
                      onRemove: () => _confirmRemove(context, stock, provider),
                    );
                  }).toList(),
                );
              },
            ),

            const SizedBox(height: 20),
          ],
        ),
      ),
    );
  }

  Widget _buildPicksSummary(List<dynamic> picks) {
    int s = 0, a = 0, b = 0, sustained = 0;
    for (final p in picks) {
      final sc = (p.totalScore as num?)?.toDouble() ?? 0;
      if (sc >= 1.5)
        s++;
      else if (sc >= 1.0)
        a++;
      else if (sc >= 0.5) b++;
      final cd = (p.consecutiveDays as num?)?.toInt() ?? 1;
      if (cd >= 3) sustained++;
    }
    return Row(children: [
      if (s > 0) _summaryChip('S $s', AppTheme.signalBuy),
      if (a > 0) ...[
        const SizedBox(width: 6),
        _summaryChip('A $a', const Color(0xFFFF6B35))
      ],
      if (b > 0) ...[
        const SizedBox(width: 6),
        _summaryChip('B $b', AppTheme.signalHold)
      ],
      if (sustained > 0) ...[
        const SizedBox(width: 6),
        _summaryChip('持续$sustained', AppTheme.signalBuy)
      ],
    ]);
  }

  Widget _summaryChip(String text, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withOpacity(0.1),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(text,
          style: TextStyle(
              fontSize: 10, fontWeight: FontWeight.w600, color: color)),
    );
  }

  Widget _buildEmptyWatchlist(BuildContext context) {
    return Container(
      margin: const EdgeInsets.all(16),
      padding: const EdgeInsets.all(32),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        children: [
          Icon(Icons.star_border,
              size: 48, color: AppTheme.textHint.withOpacity(0.5)),
          const SizedBox(height: 12),
          const Text('还没有自选股',
              style: TextStyle(color: AppTheme.textSecondary, fontSize: 16)),
          const SizedBox(height: 8),
          const Text('去搜索页面添加你关注的股票',
              style: TextStyle(color: AppTheme.textHint, fontSize: 14)),
          const SizedBox(height: 16),
          ElevatedButton.icon(
            onPressed: () => onNavigateToSearch?.call(),
            icon: const Icon(Icons.search),
            label: const Text('搜索股票'),
            style: ElevatedButton.styleFrom(
              backgroundColor: AppTheme.primaryDark,
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(8)),
            ),
          ),
        ],
      ),
    );
  }

  String _inferMarket(String code) {
    if (code.startsWith('6') || code.startsWith('9')) return 'SH';
    if (code.startsWith('0') || code.startsWith('3')) return 'SZ';
    if (code.startsWith('8') || code.startsWith('4')) return 'BJ';
    return 'SH';
  }

  void _confirmRemove(
      BuildContext context, StockInfo stock, WatchlistProvider provider) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('确认删除'),
        content: Text('确定将 ${stock.name}(${stock.code}) 从自选股中移除？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () {
              Navigator.of(ctx).pop();
              provider.remove(stock);
            },
            style: TextButton.styleFrom(foregroundColor: AppTheme.downColor),
            child: const Text('删除'),
          ),
        ],
      ),
    );
  }

  /// PC端推荐卡片（含实时操作信号 + 详细数据）
  Widget _buildPcStockCard(
      BuildContext context, dynamic pick, dynamic pcProvider) {
    final score = (pick.totalScore as num?)?.toDouble() ?? 0;
    final labelColor = score >= 1.5
        ? AppTheme.signalBuy
        : score >= 1.0
            ? AppTheme.gradeA
            : AppTheme.textSecondary;

    // 置信度等级（阈值基于全市场评分分布校准）
    // S: 前0.2% (>=1.5分), A: 前7% (>=1.0分), B: 前28% (>=0.5分), C: 其余
    String level;
    Color levelColor;
    if (pick.totalScore >= 1.5) {
      level = "S";
      levelColor = AppTheme.signalBuy;
    } else if (pick.totalScore >= 1.0) {
      level = "A";
      levelColor = AppTheme.gradeA;
    } else if (pick.totalScore >= 0.5) {
      level = "B";
      levelColor = AppTheme.signalHold;
    } else {
      level = "C";
      levelColor = AppTheme.textHint;
    }

    // 获取完整PC评级数据
    String actionText = '';
    Color actionColor = AppTheme.textHint;
    String priceInfo = '';
    String stopLossText = '';
    String takeProfitText = '';
    String rrText = '';
    String buyZoneText = '';
    double entryLow = 0, entryHigh = 0;

    try {
      final pcRating = pcProvider.getRating(pick.symbol);
      if (pcRating != null) {
        final buyRange = (pcRating.buyRange as String?) ?? '';
        final currentPrice = (pcRating.currentPrice as num?)?.toDouble() ?? 0;
        final stopLoss = (pcRating.stopLossPrice as num?)?.toDouble();
        final takeProfit = (pcRating.takeProfitPrice as num?)?.toDouble();
        final rr = (pcRating.riskRewardRatio as num?)?.toDouble() ?? 0;

        buyZoneText = buyRange;

        if (buyRange.isNotEmpty) {
          final parts = buyRange.split(' - ');
          if (parts.length == 2) {
            entryLow = double.tryParse(parts[0]) ?? 0;
            entryHigh = double.tryParse(parts[1]) ?? 0;

            if (currentPrice > 0 && entryLow > 0) {
              final regime = pcProvider.marketRegimeLabel;
              final isTop = score >= 1.0;
              final allowBuy = regime.contains('强牛') || regime.contains('弱牛');
              final allowAttention = !regime.contains('强熊');

              if (currentPrice <= entryHigh) {
                if (isTop && allowBuy) {
                  actionText = '关注';
                  actionColor = AppTheme.signalHold;
                } else if (allowAttention) {
                  actionText = '关注';
                  actionColor = AppTheme.signalHold;
                } else {
                  actionText = '等待';
                  actionColor = AppTheme.textHint;
                }
              } else {
                actionText = '等待';
                actionColor = AppTheme.textHint;
              }
              final midPrice = (entryLow + entryHigh) / 2;
              final deviation = midPrice > 0
                  ? ((currentPrice - midPrice) / midPrice * 100)
                  : 0;
              priceInfo =
                  '${currentPrice.toStringAsFixed(1)} ${deviation >= 0 ? "+" : ""}${deviation.toStringAsFixed(1)}%';
            }
          }
        }

        if (stopLoss != null) stopLossText = stopLoss.toStringAsFixed(2);
        if (takeProfit != null) takeProfitText = takeProfit.toStringAsFixed(2);
        if (rr > 0) rrText = '盈亏比 ${rr.toStringAsFixed(1)}:1';
      }
    } catch (_) {}

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () {
          final stock = StockInfo(
              code: pick.symbol,
              name: pick.name,
              market: _inferMarket(pick.symbol));
          context.read<StockProvider>().loadStockDetail(stock);
          onNavigateToDetail?.call();
        },
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // 第一行：名称 + 等级 + 操作信号
              Row(
                children: [
                  // 操作信号色条+标签
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: actionColor.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(actionText,
                        style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.bold,
                            color: actionColor)),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(pick.name,
                        style: const TextStyle(
                            fontWeight: FontWeight.w600, fontSize: 15),
                        overflow: TextOverflow.ellipsis),
                  ),
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                    decoration: BoxDecoration(
                      color: levelColor.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(level,
                        style: TextStyle(
                            fontSize: 10,
                            fontWeight: FontWeight.bold,
                            color: levelColor)),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              // 第二行：价格 + 偏离度
              Row(
                children: [
                  Text(pick.symbol,
                      style: const TextStyle(
                          color: AppTheme.textHint, fontSize: 11)),
                  if (priceInfo.isNotEmpty) ...[
                    const SizedBox(width: 12),
                    Text('现价 ',
                        style:
                            TextStyle(color: AppTheme.textHint, fontSize: 11)),
                    Text(priceInfo,
                        style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: actionColor)),
                  ],
                ],
              ),
              // 第三行：买入区间 + 止损止盈 + 盈亏比
              if (buyZoneText.isNotEmpty || stopLossText.isNotEmpty) ...[
                const SizedBox(height: 4),
                Row(
                  children: [
                    if (buyZoneText.isNotEmpty)
                      Flexible(
                        child: Text('买入 $buyZoneText',
                            style: const TextStyle(
                                fontSize: 11, color: AppTheme.primaryDark),
                            overflow: TextOverflow.ellipsis),
                      ),
                  ],
                ),
              ],
              if (stopLossText.isNotEmpty ||
                  takeProfitText.isNotEmpty ||
                  rrText.isNotEmpty) ...[
                const SizedBox(height: 2),
                Row(
                  children: [
                    if (stopLossText.isNotEmpty)
                      Text('止损 $stopLossText',
                          style: const TextStyle(
                              fontSize: 10, color: AppTheme.downColor)),
                    if (stopLossText.isNotEmpty && takeProfitText.isNotEmpty)
                      const SizedBox(width: 10),
                    if (takeProfitText.isNotEmpty)
                      Text('止盈 $takeProfitText',
                          style: const TextStyle(
                              fontSize: 10, color: AppTheme.upColor)),
                    if (rrText.isNotEmpty) ...[
                      const Spacer(),
                      Text(rrText,
                          style: const TextStyle(
                              fontSize: 10, color: AppTheme.textHint)),
                    ],
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
