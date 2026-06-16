import 'dart:math';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:fl_chart/fl_chart.dart';
import '../theme/app_theme.dart';
import '../providers/stock_provider.dart';
import '../models/kline_data.dart';

class KLineChart extends StatelessWidget {
  const KLineChart({super.key});

  @override
  Widget build(BuildContext context) {
    return Consumer<StockProvider>(
      builder: (context, provider, _) {
        final klineData = provider.klineData;
        if (klineData.isEmpty) return const SizedBox();

        final displayData = klineData.length > 60 ? klineData.sublist(klineData.length - 60) : klineData;

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
                  Icon(Icons.show_chart, size: 16, color: AppTheme.primaryDark),
                  SizedBox(width: 6),
                  Text('K线走势', style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                ],
              ),
              const SizedBox(height: 16),
              SizedBox(
                height: 200,
                child: _buildPriceLineChart(displayData),
              ),
              const SizedBox(height: 12),
              SizedBox(
                height: 60,
                child: _buildVolumeChart(displayData),
              ),
              const SizedBox(height: 8),
              // 技术指标标签（纯UI，不需要provider）
              _buildIndicatorChips(),
            ],
          ),
        );
      },
    );
  }

  Widget _buildPriceLineChart(List<KLineData> data) {
    final maxPrice = data.map((d) => d.high).reduce(max);
    final minPrice = data.map((d) => d.low).reduce(min);
    final priceRange = maxPrice - minPrice;
    final padding = priceRange * 0.05;

    return LineChart(
      LineChartData(
        gridData: FlGridData(
          show: true,
          horizontalInterval: (priceRange + 2 * padding) / 4,
          getDrawingHorizontalLine: (value) => FlLine(
            color: AppTheme.textHint.withOpacity(0.1),
            strokeWidth: 1,
          ),
          getDrawingVerticalLine: (_) => FlLine(
            color: AppTheme.textHint.withOpacity(0.1),
            strokeWidth: 1,
          ),
        ),
        titlesData: FlTitlesData(
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 50,
              getTitlesWidget: (value, meta) {
                return Text(
                  value.toStringAsFixed(0),
                  style: const TextStyle(fontSize: 10, color: AppTheme.textHint),
                );
              },
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 24,
              interval: (data.length / 5).ceilToDouble(),
              getTitlesWidget: (value, meta) {
                final idx = value.toInt();
                if (idx < 0 || idx >= data.length) return const SizedBox();
                return Text(
                  '${data[idx].date.month}/${data[idx].date.day}',
                  style: const TextStyle(fontSize: 10, color: AppTheme.textHint),
                );
              },
            ),
          ),
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
        ),
        borderData: FlBorderData(show: false),
        minY: minPrice - padding,
        maxY: maxPrice + padding,
        lineBarsData: [
          LineChartBarData(
            spots: List.generate(data.length, (i) => FlSpot(i.toDouble(), data[i].close)),
            isCurved: false,
            color: AppTheme.primaryDark,
            barWidth: 2,
            dotData: const FlDotData(show: false),
            belowBarData: BarAreaData(
              show: true,
              color: AppTheme.primaryDark.withOpacity(0.05),
            ),
          ),
          _buildMALine(data, 5, Colors.orange),
          _buildMALine(data, 10, Colors.blue),
        ],
      ),
      duration: const Duration(milliseconds: 300),
    );
  }

  LineChartBarData _buildMALine(List<KLineData> data, int period, Color color) {
    final spots = <FlSpot>[];
    for (int i = 0; i < data.length; i++) {
      if (i < period - 1) continue;
      double sum = 0;
      for (int j = i - period + 1; j <= i; j++) {
        sum += data[j].close;
      }
      spots.add(FlSpot(i.toDouble(), sum / period));
    }

    return LineChartBarData(
      spots: spots,
      isCurved: true,
      color: color,
      barWidth: 1,
      dotData: const FlDotData(show: false),
    );
  }

  Widget _buildVolumeChart(List<KLineData> data) {
    final maxVol = data.map((d) => d.volume).reduce(max);

    return BarChart(
      BarChartData(
        gridData: const FlGridData(show: false),
        titlesData: const FlTitlesData(show: false),
        borderData: FlBorderData(show: false),
        barGroups: List.generate(data.length, (i) {
          final isUp = data[i].isUp;
          return BarChartGroupData(
            x: i,
            barRods: [
              BarChartRodData(
                toY: data[i].volume / maxVol * 100,
                color: isUp ? AppTheme.upColor.withOpacity(0.5) : AppTheme.downColor.withOpacity(0.5),
                width: data.length > 40 ? 2 : 4,
                borderRadius: const BorderRadius.vertical(top: Radius.circular(1)),
              ),
            ],
          );
        }),
      ),
    );
  }

  Widget _chip(String label, Color color, bool active, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: active ? color.withOpacity(0.1) : Colors.transparent,
          border: Border.all(color: active ? color : AppTheme.textHint.withOpacity(0.3)),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text(
          label,
          style: TextStyle(fontSize: 11, color: active ? color : AppTheme.textHint),
        ),
      ),
    );
  }

  Widget _buildIndicatorChips() {
    return Row(
      children: [
        _chip('MA', Colors.blue, true, () {}),
        const SizedBox(width: 8),
        _chip('BOLL', Colors.purple, false, () {}),
        const SizedBox(width: 8),
        _chip('RSI', Colors.orange, false, () {}),
      ],
    );
  }
}
