import 'package:flutter/material.dart';
import '../theme/app_theme.dart';
import '../models/analysis_result.dart';

class AnalysisSection extends StatelessWidget {
  final AnalysisResult analysis;

  const AnalysisSection({super.key, required this.analysis});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(16),
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
              Icon(Icons.analytics, size: 16, color: AppTheme.primaryDark),
              SizedBox(width: 6),
              Text('技术分析详情', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
            ],
          ),
          const SizedBox(height: 16),

          // 各策略得分
          ...analysis.strategyScores.entries.map((entry) => _buildStrategyScore(entry.key, entry.value)),

          const SizedBox(height: 12),

          // 趋势信息
          _buildInfoRow('趋势判断', analysis.trendDescription),

          // 盈亏比
          if (analysis.riskRewardRatio > 0) ...[
            const SizedBox(height: 6),
            _buildInfoRow('盈亏比', '${analysis.riskRewardRatio.toStringAsFixed(2)} : 1'),
          ],

          // 止损止盈
          if (analysis.stopLossPrice != null) ...[
            const SizedBox(height: 6),
            _buildInfoRow('止损位', analysis.stopLossPrice!.toStringAsFixed(2)),
          ],
          if (analysis.takeProfitPrice != null) ...[
            const SizedBox(height: 6),
            _buildInfoRow('止盈位', analysis.takeProfitPrice!.toStringAsFixed(2)),
          ],
          if (analysis.buyRange != null) ...[
            const SizedBox(height: 6),
            _buildInfoRow('买入区间', analysis.buyRange!),
          ],

          const SizedBox(height: 12),

          // 信号列表
          const Text('信号明细', style: TextStyle(color: AppTheme.textSecondary, fontSize: 13, fontWeight: FontWeight.w500)),
          const SizedBox(height: 8),
          ...analysis.signals.map((signal) => _buildSignalItem(signal)),
        ],
      ),
    );
  }

  Widget _buildStrategyScore(String name, double score) {
    String label;
    switch (name) {
      case 'maVolume':
        label = 'MaVolume(均线放量)';
        break;
      case 'turtleTrade':
        label = 'TurtleTrade(突破)';
        break;
      case 'meanReversion':
        label = '均值回归';
        break;
      case 'volumePrice':
        label = '量价关系';
        break;
      case 'atrChannel':
        label = 'ATR通道突破';
        break;
      case 'macdRsi':
        label = 'MACD+RSI共振';
        break;
      case 'maCross':
        label = 'MA20/60金叉死叉';
        break;
      default:
        label = name;
    }

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          SizedBox(
            width: 140,
            child: Text(label, style: const TextStyle(color: AppTheme.textSecondary, fontSize: 13)),
          ),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: (score + 10) / 20,
                backgroundColor: AppTheme.textHint.withOpacity(0.1),
                color: score >= 0 ? AppTheme.upColor : AppTheme.downColor,
                minHeight: 6,
              ),
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(
            width: 36,
            child: Text(
              score >= 0 ? '+${score.toStringAsFixed(0)}' : score.toStringAsFixed(0),
              style: TextStyle(
                fontWeight: FontWeight.bold,
                color: score >= 0 ? AppTheme.upColor : AppTheme.downColor,
              ),
              textAlign: TextAlign.right,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildInfoRow(String label, String value) {
    return Row(
      children: [
        SizedBox(
          width: 80,
          child: Text(label, style: const TextStyle(color: AppTheme.textSecondary, fontSize: 13)),
        ),
        Expanded(
          child: Text(value, style: const TextStyle(color: AppTheme.textPrimary, fontSize: 13)),
        ),
      ],
    );
  }

  Widget _buildSignalItem(Signal signal) {
    final color = signal.type == SignalType.bullish
        ? AppTheme.upColor
        : signal.type == SignalType.bearish
            ? AppTheme.downColor
            : AppTheme.textHint;

    return Container(
      margin: const EdgeInsets.only(bottom: 4),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withOpacity(0.05),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        children: [
          Icon(
            signal.type == SignalType.bullish
                ? Icons.arrow_upward
                : signal.type == SignalType.bearish
                    ? Icons.arrow_downward
                    : Icons.remove,
            size: 14,
            color: color,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(signal.name, style: TextStyle(fontWeight: FontWeight.w500, fontSize: 12, color: AppTheme.textPrimary)),
                Text(signal.description, style: const TextStyle(fontSize: 11, color: AppTheme.textHint), maxLines: 2),
              ],
            ),
          ),
          Text(
            signal.score >= 0 ? '+${signal.score.toStringAsFixed(0)}' : signal.score.toStringAsFixed(0),
            style: TextStyle(fontWeight: FontWeight.bold, color: color, fontSize: 13),
          ),
        ],
      ),
    );
  }
}
