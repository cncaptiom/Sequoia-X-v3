import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../theme/app_theme.dart';
import '../models/analysis_result.dart';
import '../models/rating_snapshot.dart';
import '../providers/performance_provider.dart';
import '../providers/watchlist_provider.dart';
import '../services/storage_service.dart';

class PerformancePage extends StatefulWidget {
  const PerformancePage({super.key});

  @override
  State<PerformancePage> createState() => _PerformancePageState();
}

class _PerformancePageState extends State<PerformancePage> {
  @override
  void initState() {
    super.initState();
    // 进入页面时加载数据
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<PerformanceProvider>().loadSnapshots();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('效果跟踪')),
      body: Consumer2<PerformanceProvider, WatchlistProvider>(
        builder: (context, pp, wp, _) {
          if (pp.isLoading) {
            return const Center(child: CircularProgressIndicator());
          }

          if (!pp.hasData) {
            return _buildEmptyState();
          }

          final stats = pp.computeStats(wp.quotes);

          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              // 统计摘要
              _buildStatsCard(stats),
              const SizedBox(height: 16),

              // 历史记录标题
              Row(
                children: [
                  const Icon(Icons.history, size: 18, color: AppTheme.primaryDark),
                  const SizedBox(width: 6),
                  const Text('历史记录', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                  const Spacer(),
                  Text('${pp.snapshotsByDate.length}天', style: const TextStyle(color: AppTheme.textSecondary)),
                ],
              ),
              const SizedBox(height: 8),

              // 每天的快照列表
              ...pp.snapshotsByDate.entries.map((entry) => _buildDateCard(entry.key, entry.value)),
            ],
          );
        },
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Container(
        margin: const EdgeInsets.all(40),
        padding: const EdgeInsets.all(32),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.auto_graph, size: 56, color: AppTheme.textHint.withOpacity(0.4)),
            const SizedBox(height: 16),
            const Text('还未收集到评级数据', style: TextStyle(fontSize: 16, color: AppTheme.textSecondary)),
            const SizedBox(height: 8),
            const Text(
              '刷新自选股后，系统会自动记录今日评级\n使用一周后即可查看效果统计',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 13, color: AppTheme.textHint),
            ),
            const SizedBox(height: 20),
            ElevatedButton.icon(
              onPressed: () {
                context.read<PerformanceProvider>().loadSnapshots();
              },
              icon: const Icon(Icons.refresh),
              label: const Text('重新加载'),
              style: ElevatedButton.styleFrom(
                backgroundColor: AppTheme.primaryDark,
                foregroundColor: Colors.white,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildStatsCard(PerformanceStats stats) {
    return Container(
      padding: const EdgeInsets.all(16),
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
              const Icon(Icons.analytics, size: 20, color: AppTheme.primaryDark),
              const SizedBox(width: 6),
              const Text('效果统计', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            ],
          ),
          const SizedBox(height: 16),

          if (stats.isValid) ...[
            // 总体准确率
            Row(
              children: [
                Text('总体信号准确率 ',
                    style: const TextStyle(color: AppTheme.textSecondary, fontSize: 13)),
                Text('${stats.overallAccuracy.toStringAsFixed(0)}%',
                    style: TextStyle(
                      fontSize: 24,
                      fontWeight: FontWeight.bold,
                      color: stats.overallAccuracy >= 50 ? AppTheme.upColor : AppTheme.downColor,
                    )),
                const SizedBox(width: 4),
                Text(
                  '(${stats.totalCorrect}/${stats.totalEvaluated})',
                  style: const TextStyle(color: AppTheme.textHint, fontSize: 12),
                ),
              ],
            ),
            const SizedBox(height: 12),
            // 买入信号准确率
            _statRow(
              icon: Icons.arrow_upward,
              iconColor: AppTheme.upColor,
              label: '买入信号',
              correct: stats.buyCorrect,
              total: stats.buyTotal,
              accuracy: stats.buyAccuracy,
            ),
            const SizedBox(height: 8),
            // 回避信号准确率
            _statRow(
              icon: Icons.arrow_downward,
              iconColor: AppTheme.downColor,
              label: '回避信号',
              correct: stats.avoidCorrect,
              total: stats.avoidTotal,
              accuracy: stats.avoidAccuracy,
            ),
          ] else ...[
            const Icon(Icons.info_outline, size: 32, color: AppTheme.textHint),
            const SizedBox(height: 8),
            const Text(
              '暂无历史信号统计\n只有过去日期的买入/回避评级才会纳入效果评估',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppTheme.textSecondary, fontSize: 13),
            ),
          ],

          const SizedBox(height: 12),
          // 其他评级数量汇总
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: AppTheme.background,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                _summaryChip('观望', stats.watchTotal, AppTheme.signalHold),
                _summaryChip('关注', stats.attentionTotal, AppTheme.signalWatch),
                _summaryChip('历史信号', stats.totalEvaluated, AppTheme.primaryDark),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _statRow({
    required IconData icon,
    required Color iconColor,
    required String label,
    required int correct,
    required int total,
    required double accuracy,
  }) {
    return Row(
      children: [
        Icon(icon, size: 16, color: iconColor),
        const SizedBox(width: 4),
        SizedBox(
          width: 64,
          child: Text(label, style: const TextStyle(color: AppTheme.textSecondary, fontSize: 13)),
        ),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: total > 0 ? correct / total : 0,
              backgroundColor: Colors.grey[200],
              color: iconColor,
              minHeight: 8,
            ),
          ),
        ),
        const SizedBox(width: 8),
        SizedBox(
          width: 60,
          child: Text(
            '${accuracy.toStringAsFixed(0)}%',
            textAlign: TextAlign.right,
            style: TextStyle(fontWeight: FontWeight.w600, color: iconColor, fontSize: 13),
          ),
        ),
        Text(
          '($correct/$total)',
          style: const TextStyle(color: AppTheme.textHint, fontSize: 11),
        ),
      ],
    );
  }

  Widget _summaryChip(String label, int count, Color color) {
    return Column(
      children: [
        Text(label, style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12)),
        const SizedBox(height: 4),
        Text('$count', style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: color)),
      ],
    );
  }

  Widget _buildDateCard(String dateStr, List<RatingSnapshot> snapshots) {
    if (snapshots.isEmpty) return const SizedBox();

    // 按评级汇总
    final counts = <int, int>{};
    for (final s in snapshots) {
      counts[s.ratingIndex] = (counts[s.ratingIndex] ?? 0) + 1;
    }

    // 判断是否今天
    final isToday = dateStr == StorageService.todayDateStr();

    // 格式化日期显示
    String displayDate = dateStr;
    try {
      final parts = dateStr.split('-');
      if (parts.length == 3) {
        final d = DateTime(int.parse(parts[0]), int.parse(parts[1]), int.parse(parts[2]));
        final weekdays = ['一', '二', '三', '四', '五', '六', '日'];
        final wd = weekdays[d.weekday - 1];
        displayDate = '${parts[0]}/${parts[1]}/${parts[2]} 周$wd';
      }
    } catch (_) {}

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => _showDateDetail(dateStr, displayDate, snapshots),
        child: Container(
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            boxShadow: [BoxShadow(color: Colors.black.withOpacity(0.03), blurRadius: 6)],
          ),
          child: Row(
            children: [
              // 日期
              Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  color: isToday ? AppTheme.primaryDark : AppTheme.background,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Center(
                  child: Text(
                    dateStr.split('-').last,
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.bold,
                      color: isToday ? Colors.white : AppTheme.textPrimary,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(displayDate,
                        style: TextStyle(
                          fontWeight: isToday ? FontWeight.w600 : FontWeight.normal,
                          color: AppTheme.textPrimary,
                          fontSize: 14,
                        )),
                    if (isToday)
                      const Text('今日数据', style: TextStyle(color: AppTheme.primaryDark, fontSize: 11)),
                  ],
                ),
              ),
              // 评级分布
              ...counts.entries.map((e) {
                final rating = Rating.values[e.key];
                final color = AppTheme.ratingColorFromEnum(rating);
                return Padding(
                  padding: const EdgeInsets.only(left: 8),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                      color: color.withOpacity(0.1),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(
                      '${rating.label} ${e.value}',
                      style: TextStyle(fontSize: 10, color: color, fontWeight: FontWeight.w500),
                    ),
                  ),
                );
              }),
              const SizedBox(width: 4),
              Text('${snapshots.length}只',
                  style: const TextStyle(color: AppTheme.textHint, fontSize: 12)),
              const Icon(Icons.chevron_right, size: 18, color: AppTheme.textHint),
            ],
          ),
        ),
      ),
    );
  }

  void _showDateDetail(String dateStr, String displayDate, List<RatingSnapshot> snapshots) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) {
        // 获取当前行情用于比对
        final wp = context.read<WatchlistProvider>();
        return DraggableScrollableSheet(
          initialChildSize: 0.7,
          maxChildSize: 0.9,
          minChildSize: 0.4,
          expand: false,
          builder: (ctx, scrollController) {
            return Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
                  child: Row(
                    children: [
                      Text(displayDate, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                      const Spacer(),
                      Text('${snapshots.length}只股票', style: const TextStyle(color: AppTheme.textSecondary)),
                      const SizedBox(width: 8),
                      IconButton(
                        icon: const Icon(Icons.close, size: 20),
                        onPressed: () => Navigator.of(ctx).pop(),
                      ),
                    ],
                  ),
                ),
                const Divider(),
                Expanded(
                  child: ListView.builder(
                    controller: scrollController,
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    itemCount: snapshots.length,
                    itemBuilder: (ctx, i) {
                      final s = snapshots[i];
                      final ratingColor = AppTheme.ratingColorFromEnum(s.rating);
                      final quote = wp.quotes[s.stockCode];
                      final currentPrice = quote?.currentPrice;
                      final changePercent = quote?.changePercent;
                      final changeColor = AppTheme.priceColor(changePercent ?? 0);

                      return Container(
                        margin: const EdgeInsets.only(bottom: 6),
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                        decoration: BoxDecoration(
                          color: ratingColor.withOpacity(0.05),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: ratingColor.withOpacity(0.2)),
                        ),
                        child: Row(
                          children: [
                            // 评级标识
                            Container(
                              width: 4,
                              height: 36,
                              decoration: BoxDecoration(
                                color: ratingColor,
                                borderRadius: BorderRadius.circular(2),
                              ),
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(s.stockName,
                                      style: const TextStyle(fontWeight: FontWeight.w500, fontSize: 14)),
                                  Text(s.stockCode,
                                      style: const TextStyle(color: AppTheme.textHint, fontSize: 11)),
                                ],
                              ),
                            ),
                            // 当时价格与评级
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.end,
                              children: [
                                Text(s.ratingStars,
                                    style: TextStyle(fontSize: 12, color: ratingColor)),
                                Text('${s.ratingLabel} ${s.totalScore.toStringAsFixed(1)}分',
                                    style: TextStyle(fontSize: 11, color: ratingColor)),
                              ],
                            ),
                            const SizedBox(width: 16),
                            // 当时价格
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.end,
                              children: [
                                Text(s.price.toStringAsFixed(2),
                                    style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500)),
                                Text('当时价', style: const TextStyle(color: AppTheme.textHint, fontSize: 10)),
                              ],
                            ),
                            const SizedBox(width: 12),
                            // 当前价格
                            if (currentPrice != null)
                              Column(
                                crossAxisAlignment: CrossAxisAlignment.end,
                                children: [
                                  Text(currentPrice.toStringAsFixed(2),
                                      style: TextStyle(fontSize: 13, fontWeight: FontWeight.w500, color: changeColor)),
                                  Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      if (changePercent != null)
                                        Text(
                                          '${changePercent >= 0 ? '+' : ''}${changePercent.toStringAsFixed(1)}%',
                                          style: TextStyle(fontSize: 11, color: changeColor),
                                        ),
                                    ],
                                  ),
                                ],
                              )
                            else
                              const Text('-',
                                  style: TextStyle(color: AppTheme.textHint, fontSize: 13)),
                          ],
                        ),
                      );
                    },
                  ),
                ),
              ],
            );
          },
        );
      },
    );
  }
}

extension on Rating {
  String get label {
    switch (this) {
      case Rating.strongBuy:
        return '强烈买入';
      case Rating.buy:
        return '买入';
      case Rating.watch:
        return '观望';
      case Rating.attention:
        return '关注';
      case Rating.avoid:
        return '回避';
    }
  }
}
