import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../theme/app_theme.dart';
import '../providers/stock_provider.dart';
import '../providers/watchlist_provider.dart';
import '../providers/pc_data_provider.dart';
import '../models/strategy_config.dart';
import '../services/storage_service.dart';

class StrategyPage extends StatelessWidget {
  const StrategyPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('策略配置'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // 当前市场状态卡片
          Consumer<PcDataProvider>(
            builder: (context, pcProvider, _) {
              final regime = pcProvider.marketRegimeLabel;
              final exposure = pcProvider.suggestedExposure;
              if (regime.isEmpty) return const SizedBox.shrink();

              Color regimeColor;
              if (regime.contains('强牛') || regime.contains('弱牛')) {
                regimeColor = AppTheme.signalBuy;
              } else if (regime.contains('震荡')) {
                regimeColor = AppTheme.signalHold;
              } else {
                regimeColor = AppTheme.signalSell;
              }

              return Container(
                margin: const EdgeInsets.only(bottom: 12),
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    colors: [regimeColor.withOpacity(0.1), Colors.white],
                    begin: Alignment.centerLeft,
                    end: Alignment.centerRight,
                  ),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: regimeColor.withOpacity(0.3)),
                ),
                child: Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                      decoration: BoxDecoration(
                        color: regimeColor.withOpacity(0.15),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(regime,
                        style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: regimeColor)),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('当前市场状态', style: TextStyle(fontSize: 12, color: AppTheme.textHint)),
                          const SizedBox(height: 2),
                          Text('建议仓位 ${(exposure * 100).toInt()}%',
                            style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: regimeColor)),
                        ],
                      ),
                    ),
                  ],
                ),
              );
            },
          ),

          // 策略权重说明（Regime动态调整，非固定值）
          Consumer<PcDataProvider>(
            builder: (context, pcProvider, _) {
              final regime = pcProvider.marketRegimeLabel;
              String hint;
              if (regime.contains('强牛') || regime.contains('弱牛')) {
                hint = '当前$regime，建议顺势而为，趋势策略优先。';
              } else if (regime.contains('震荡')) {
                hint = '当前$regime，建议高抛低吸，反转策略优先。';
              } else if (regime.contains('熊')) {
                hint = '当前$regime，以防守为主，降低趋势策略权重。';
              } else {
                hint = '策略权重由PC端根据市场状态动态分配，当前$regime。';
              }
              return Container(
                margin: const EdgeInsets.only(bottom: 12),
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: AppTheme.primaryDark.withOpacity(0.05),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: AppTheme.primaryDark.withOpacity(0.2)),
                ),
                child: Row(
                  children: [
                    Icon(Icons.info_outline, size: 18, color: AppTheme.primaryDark),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(hint,
                        style: TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                    ),
                  ],
                ),
              );
            },
          ),

          // 交易风格
          _buildSection(
            title: '操盘风格',
            icon: Icons.person_outline,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
                child: Consumer<StockProvider>(
                  builder: (context, provider, _) {
                    final config = provider.strategyConfig;
                    return Row(
                      children: TradingStyle.values.map((style) {
                        final selected = config.defaultStyle == style;
                        return Expanded(
                          child: Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 4),
                            child: GestureDetector(
                              onTap: () => _update(context, config.copyWith(defaultStyle: style)),
                              child: Container(
                                padding: const EdgeInsets.symmetric(vertical: 10),
                                decoration: BoxDecoration(
                                  color: selected ? AppTheme.primaryDark : Colors.transparent,
                                  borderRadius: BorderRadius.circular(8),
                                  border: Border.all(
                                    color: selected ? AppTheme.primaryDark : AppTheme.textHint.withOpacity(0.3),
                                  ),
                                ),
                                child: Column(
                                  children: [
                                    Text(style.label,
                                      style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600,
                                        color: selected ? Colors.white : AppTheme.textPrimary)),
                                    const SizedBox(height: 2),
                                    Text(style.description,
                                      style: TextStyle(fontSize: 10,
                                        color: selected ? Colors.white70 : AppTheme.textHint),
                                      textAlign: TextAlign.center),
                                  ],
                                ),
                              ),
                            ),
                          ),
                        );
                      }).toList(),
                    );
                  },
                ),
              ),
            ],
          ),

          const SizedBox(height: 12),

          // 趋势过滤
          _buildSection(
            title: '趋势过滤',
            icon: Icons.filter_tilt_shift,
            children: [
              Consumer<StockProvider>(
                builder: (context, provider, _) {
                  final config = provider.strategyConfig;
                  return _SwitchTile(
                    title: '启用趋势过滤',
                    subtitle: '只分析上升趋势股票，下降趋势一律回避',
                    value: config.trendFilterEnabled,
                    onChanged: (v) => _update(context, config.copyWith(trendFilterEnabled: v)),
                  );
                },
              ),
            ],
          ),

          const SizedBox(height: 12),

          // PC端优先监控列表
          Consumer<PcDataProvider>(
            builder: (context, pcProvider, _) {
              if (!pcProvider.hasRatings) return const SizedBox.shrink();
              return _buildSection(
                title: '重点监控（Serenity 瓶颈标的）',
                icon: Icons.priority_high,
                children: [
                  const Padding(
                    padding: EdgeInsets.fromLTRB(16, 0, 16, 4),
                    child: Text('基于供应链卡脖子框架筛选的标的，PC端每晚自动跟踪',
                      style: TextStyle(fontSize: 12, color: AppTheme.textHint)),
                  ),
                  // 显示前5只监控标的
                  ...pcProvider.topPicks.take(5).map((pick) => ListTile(
                    dense: true,
                    leading: Container(
                      width: 4, height: 24,
                      decoration: BoxDecoration(
                        color: AppTheme.primaryDark, borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                    title: Text('${pick.name} (${pick.symbol})',
                      style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500)),
                    subtitle: Text('${pick.ratingLabel}  ${pick.totalScore.toStringAsFixed(1)}分',
                      style: const TextStyle(fontSize: 11)),
                  )),
                ],
              );
            },
          ),

          const SizedBox(height: 12),

          // 策略近一年收益率（来自PC端回测）
          Consumer<PcDataProvider>(
            builder: (context, pcProvider, _) {
              final bt = pcProvider.strategyBacktest;
              if (bt == null) return const SizedBox.shrink();
              final strategies = bt['strategies'] as Map<String, dynamic>?;
              final meta = bt['meta'] as Map<String, dynamic>?;
              if (strategies == null || meta == null) return const SizedBox.shrink();

              // 按类别分组
              final categories = <String, List<MapEntry<String, dynamic>>>{};
              for (final entry in strategies.entries) {
                final m = meta[entry.key] as Map<String, dynamic>?;
                final cat = (m?['category'] as String?) ?? 'unknown';
                categories.putIfAbsent(cat, () => []).add(entry);
              }

              final catLabels = {
                'trend': '趋势型', 'reversal': '反转型', 'momentum': '动量型',
              };
              final catColors = {
                'trend': const Color(0xFF2196F3),
                'reversal': const Color(0xFFFF6B35),
                'momentum': const Color(0xFF4CAF50),
              };

              return _buildSection(
                title: '策略近一年收益率',
                subtitle: '信号触发后20日平均收益 | IC: 评分与收益秩相关',
                icon: Icons.bar_chart,
                children: [
                  // 表头
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 2),
                    child: Row(
                      children: [
                        const SizedBox(width: 150, child: Text('策略', style: TextStyle(fontSize: 10, color: AppTheme.textHint))),
                        const Expanded(child: Text('20日收益', style: TextStyle(fontSize: 10, color: AppTheme.textHint))),
                        const SizedBox(width: 40, child: Text('IC', style: TextStyle(fontSize: 10, color: AppTheme.textHint))),
                        const SizedBox(width: 36, child: Text('IR', style: TextStyle(fontSize: 10, color: AppTheme.textHint))),
                        const SizedBox(width: 36, child: Text('价差', style: TextStyle(fontSize: 10, color: AppTheme.textHint))),
                      ],
                    ),
                  ),
                  for (final cat in ['trend', 'momentum', 'reversal'])
                    if (categories.containsKey(cat)) ...[
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
                        child: Row(
                          children: [
                            Container(width: 10, height: 10,
                              decoration: BoxDecoration(
                                color: (catColors[cat] ?? AppTheme.textHint).withOpacity(0.5),
                                borderRadius: BorderRadius.circular(2),
                              ),
                            ),
                            const SizedBox(width: 6),
                            Text(catLabels[cat] ?? cat,
                              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: AppTheme.textSecondary)),
                          ],
                        ),
                      ),
                      for (final entry in categories[cat]!) ...[
                        _buildStrategyReturnTile(
                          key: entry.key,
                          label: (meta[entry.key]?['label'] as String?) ?? entry.key,
                          stats: entry.value as Map<String, dynamic>,
                        ),
                      ],
                    ],
                  // IC 统计摘要
                  _buildIcSummary(strategies, meta),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                    child: Text('数据基于 ${bt['config']?['sample_size'] ?? '?'} 只股票 ${bt['config']?['date_range'] ?? ''} 回测',
                      style: const TextStyle(fontSize: 10, color: AppTheme.textHint)),
                  ),
                ],
              );
            },
          ),

          const SizedBox(height: 12),

          // 7大策略开关（简化版，只保留开关，参数由PC端控制）
          _buildSection(
            title: '策略开关',
            subtitle: 'PC端根据市场状态动态分配权重',
            icon: Icons.tune,
            children: [
              Consumer<StockProvider>(
                builder: (context, provider, _) {
                  final config = provider.strategyConfig;
                  return Column(
                    children: [
                      _SwitchTile(title: 'MaVolume 均线金叉放量', value: config.maVolumeEnabled,
                        onChanged: (v) => _update(context, config.copyWith(maVolumeEnabled: v))),
                      _SwitchTile(title: 'TurtleTrade 趋势突破', value: config.turtleTradeEnabled,
                        onChanged: (v) => _update(context, config.copyWith(turtleTradeEnabled: v))),
                      _SwitchTile(title: '均值回归 超卖反转', value: config.meanReversionEnabled,
                        onChanged: (v) => _update(context, config.copyWith(meanReversionEnabled: v))),
                      _SwitchTile(title: '量价关系 确认信号', value: config.volumePriceEnabled,
                        onChanged: (v) => _update(context, config.copyWith(volumePriceEnabled: v))),
                      _SwitchTile(title: 'ATR通道突破 进攻型', value: config.atrChannelEnabled,
                        onChanged: (v) => _update(context, config.copyWith(atrChannelEnabled: v))),
                      _SwitchTile(title: 'MACD+RSI共振 顺势确认', value: config.macdRsiEnabled,
                        onChanged: (v) => _update(context, config.copyWith(macdRsiEnabled: v))),
                      _SwitchTile(title: 'MA20/60金叉死叉 趋势判断', value: config.maCrossEnabled,
                        onChanged: (v) => _update(context, config.copyWith(maCrossEnabled: v))),
                    ],
                  );
                },
              ),
            ],
          ),

          const SizedBox(height: 16),

          // 恢复默认
          Consumer<StockProvider>(
            builder: (context, provider, _) {
              return Center(
                child: TextButton.icon(
                  onPressed: () => _update(context, const StrategyConfig()),
                  icon: const Icon(Icons.restore),
                  label: const Text('恢复默认设置'),
                  style: TextButton.styleFrom(foregroundColor: AppTheme.textSecondary),
                ),
              );
            },
          ),

          const SizedBox(height: 20),
        ],
      ),
    );
  }

  /// 构建策略收益率行（含 IC 指标）
  Widget _buildStrategyReturnTile({
    required String key,
    required String label,
    required Map<String, dynamic> stats,
  }) {
    final ret20 = (stats['return_20d'] as num?)?.toDouble() ?? 0;
    final count = (stats['signal_count'] as num?)?.toInt() ?? 0;
    final insufficient = stats['insufficient_data'] as bool? ?? false;

    // IC 分析数据
    final icAnalysis = stats['ic_analysis'] as Map<String, dynamic>?;
    final meanIc = (icAnalysis?['mean_ic'] as num?)?.toDouble();
    final icIr = (icAnalysis?['ic_ir'] as num?)?.toDouble();
    final quantileReturns = icAnalysis?['avg_quantile_returns'] as Map<String, dynamic>?;
    final spread = (quantileReturns?['spread'] as num?)?.toDouble();

    final retColor = ret20 >= 1.0 ? AppTheme.upColor : ret20 > 0 ? AppTheme.signalHold : AppTheme.downColor;
    final icColor = (meanIc != null) ? (meanIc > 0 ? AppTheme.upColor : AppTheme.downColor) : AppTheme.textHint;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 3),
      child: Row(
        children: [
          SizedBox(
            width: 150,
            child: Text(label,
              style: const TextStyle(fontSize: 12, color: AppTheme.textPrimary),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (insufficient)
            const Expanded(child: Text('数据不足', style: TextStyle(fontSize: 11, color: AppTheme.textHint)))
          else ...[
            Expanded(
              child: Text(
                '${ret20 >= 0 ? "+" : ""}${ret20.toStringAsFixed(1)}%',
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: retColor),
              ),
            ),
            SizedBox(
              width: 40,
              child: Text(meanIc != null ? meanIc.toStringAsFixed(3) : '-',
                style: TextStyle(fontSize: 10, color: icColor)),
            ),
            SizedBox(
              width: 36,
              child: Text(icIr != null ? icIr.toStringAsFixed(2) : '-',
                style: const TextStyle(fontSize: 10, color: AppTheme.textHint)),
            ),
            SizedBox(
              width: 36,
              child: Text(spread != null ? '${spread >= 0 ? "+" : ""}${spread.toStringAsFixed(1)}%' : '-',
                style: TextStyle(fontSize: 10,
                  color: spread != null ? (spread > 0 ? AppTheme.upColor : AppTheme.downColor) : AppTheme.textHint)),
            ),
          ],
        ],
      ),
    );
  }

  /// IC 统计摘要行：显示最佳/最差 IC、衰减策略
  Widget _buildIcSummary(Map<String, dynamic> strategies, Map<String, dynamic> meta) {
    // 提取各策略 IC 数据
    final icList = <Map<String, dynamic>>[];
    for (final entry in strategies.entries) {
      final stats = entry.value as Map<String, dynamic>?;
      final icAnalysis = stats?['ic_analysis'] as Map<String, dynamic>?;
      final m = meta[entry.key] as Map<String, dynamic>?;
      final label = (m?['label'] as String?) ?? entry.key;
      if (icAnalysis != null) {
        final meanIc = (icAnalysis['mean_ic'] as num?)?.toDouble() ?? 0;
        final spread = ((icAnalysis['avg_quantile_returns'] as Map<String, dynamic>?)?
            ?['spread'] as num?)?.toDouble() ?? 0;
        icList.add({'name': label, 'ic': meanIc, 'spread': spread, 'key': entry.key});
      }
    }
    if (icList.isEmpty) return const SizedBox.shrink();

    icList.sort((a, b) => (b['ic'] as double).compareTo(a['ic'] as double));
    final best = icList.first;
    final worst = icList.last;

    // 检查是否有衰减策略
    final decayed = strategies.entries.where((e) {
      final icA = (e.value as Map<String, dynamic>?)?['ic_analysis'] as Map<String, dynamic>?;
      final avgIc = (icA?['mean_ic'] as num?)?.toDouble() ?? 0;
      return avgIc < 0;
    }).toList();

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 2, 16, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // IC 排名
          Text('IC排名: ${best['name']} ${(best['ic'] as double).toStringAsFixed(3)}',
            style: const TextStyle(fontSize: 10, color: AppTheme.upColor)),
          if (decayed.isNotEmpty)
            Text('IC为负: ${decayed.map((e) {
              final m = meta[e.key] as Map<String, dynamic>?;
              return (m?['label'] as String?) ?? e.key;
            }).join(', ')}（权重已降低）',
              style: const TextStyle(fontSize: 10, color: AppTheme.signalSell)),
          // 最佳价差
          Text('最佳价差: ${best['name']} ${(best['spread'] as double).toStringAsFixed(1)}%',
            style: const TextStyle(fontSize: 10, color: AppTheme.textHint)),
        ],
      ),
    );
  }

  Widget _buildSection({
    required String title,
    String? subtitle,
    required IconData icon,
    required List<Widget> children,
  }) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Row(
              children: [
                Icon(icon, size: 20, color: AppTheme.primaryDark),
                const SizedBox(width: 8),
                Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600, color: AppTheme.textPrimary)),
                if (subtitle != null) ...[
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(subtitle, style: const TextStyle(color: AppTheme.textHint, fontSize: 11), overflow: TextOverflow.ellipsis),
                  ),
                ],
              ],
            ),
          ),
          ...children,
          const SizedBox(height: 8),
        ],
      ),
    );
  }

  void _update(BuildContext context, StrategyConfig config) {
    StorageService().setCustomizedStrategy(true);
    context.read<StockProvider>().updateStrategy(config);
    context.read<WatchlistProvider>().reanalyzeAll(config);
  }
}

class _SwitchTile extends StatelessWidget {
  final String title;
  final String? subtitle;
  final bool value;
  final ValueChanged<bool> onChanged;

  const _SwitchTile({
    required this.title,
    this.subtitle,
    required this.value,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: SwitchListTile(
        title: Text(title, style: const TextStyle(fontSize: 14)),
        subtitle: subtitle != null
            ? Text(subtitle!, style: const TextStyle(fontSize: 12, color: AppTheme.textHint))
            : null,
        value: value,
        activeColor: AppTheme.primaryDark,
        onChanged: onChanged,
        dense: true,
      ),
    );
  }
}
