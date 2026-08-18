import 'dart:math';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:fl_chart/fl_chart.dart';
import '../theme/app_theme.dart';
import '../providers/stock_provider.dart';
import '../models/kline_data.dart';

/// 真K线蜡烛图（红涨绿跌，A股习惯）。
/// 可叠加 MA5/MA10、BOLL 布林带，RSI 作为独立子面板，指标芯片可切换。
class KLineChart extends StatefulWidget {
  const KLineChart({super.key});

  @override
  State<KLineChart> createState() => _KLineChartState();
}

class _KLineChartState extends State<KLineChart> {
  bool _showMa = true;
  bool _showBoll = false;
  bool _showRsi = false;

  @override
  Widget build(BuildContext context) {
    return Consumer<StockProvider>(
      builder: (context, provider, _) {
        final klineData = provider.klineData;
        if (klineData.isEmpty) return const SizedBox();

        final displayData = klineData.length > 60
            ? klineData.sublist(klineData.length - 60)
            : klineData;

        return Container(
          margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
            boxShadow: [
              BoxShadow(color: Colors.black.withOpacity(0.05), blurRadius: 10)
            ],
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Row(
                children: [
                  Icon(Icons.show_chart, size: 16, color: AppTheme.primaryDark),
                  SizedBox(width: 6),
                  Text('K线走势',
                      style:
                          TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                ],
              ),
              const SizedBox(height: 16),
              SizedBox(
                height: 200,
                width: double.infinity,
                child: CustomPaint(
                  painter: _CandlePainter(
                    data: displayData,
                    showMa: _showMa,
                    showBoll: _showBoll,
                  ),
                ),
              ),
              const SizedBox(height: 12),
              SizedBox(height: 60, child: _buildVolumeChart(displayData)),
              if (_showRsi) ...[
                const SizedBox(height: 12),
                SizedBox(height: 50, child: _buildRsiChart(displayData)),
              ],
              const SizedBox(height: 8),
              _buildIndicatorChips(),
            ],
          ),
        );
      },
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
                color: isUp
                    ? AppTheme.upColor.withOpacity(0.5)
                    : AppTheme.downColor.withOpacity(0.5),
                width: data.length > 40 ? 2 : 4,
                borderRadius:
                    const BorderRadius.vertical(top: Radius.circular(1)),
              ),
            ],
          );
        }),
      ),
    );
  }

  Widget _buildRsiChart(List<KLineData> data) {
    final n = data.length;
    if (n < 2) return const SizedBox();
    final spots =
        List.generate(n, (i) => FlSpot(i.toDouble(), _calcRsi(data, i)));

    return LineChart(
      LineChartData(
        gridData: const FlGridData(show: false),
        titlesData: const FlTitlesData(show: false),
        borderData: FlBorderData(show: false),
        minY: 0,
        maxY: 100,
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            isCurved: true,
            color: AppTheme.primaryDark,
            barWidth: 1.2,
            dotData: const FlDotData(show: false),
          ),
          LineChartBarData(
            spots: [FlSpot(0, 30), FlSpot((n - 1).toDouble(), 30)],
            color: AppTheme.downColor.withOpacity(0.35),
            barWidth: 0.8,
            dotData: const FlDotData(show: false),
          ),
          LineChartBarData(
            spots: [FlSpot(0, 70), FlSpot((n - 1).toDouble(), 70)],
            color: AppTheme.upColor.withOpacity(0.35),
            barWidth: 0.8,
            dotData: const FlDotData(show: false),
          ),
        ],
      ),
      duration: const Duration(milliseconds: 200),
    );
  }

  Widget _chip(String label, Color color, bool active, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: active ? color.withOpacity(0.1) : Colors.transparent,
          border: Border.all(
              color: active ? color : AppTheme.textHint.withOpacity(0.3)),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text(
          label,
          style: TextStyle(
              fontSize: 11, color: active ? color : AppTheme.textHint),
        ),
      ),
    );
  }

  Widget _buildIndicatorChips() {
    return Row(
      children: [
        _chip('MA', Colors.blue, _showMa,
            () => setState(() => _showMa = !_showMa)),
        const SizedBox(width: 8),
        _chip('BOLL', Colors.purple, _showBoll,
            () => setState(() => _showBoll = !_showBoll)),
        const SizedBox(width: 8),
        _chip('RSI', Colors.orange, _showRsi,
            () => setState(() => _showRsi = !_showRsi)),
      ],
    );
  }

  static double _calcRsi(List<KLineData> data, int end, [int period = 14]) {
    final start = end - period;
    if (start < 0) return 50;
    double gain = 0, loss = 0;
    for (int i = start + 1; i <= end; i++) {
      final diff = data[i].close - data[i - 1].close;
      if (diff > 0) {
        gain += diff;
      } else {
        loss -= diff;
      }
    }
    if (loss == 0) return 100;
    final rs = gain / loss;
    return 100 - (100 / (1 + rs));
  }
}

/// 蜡烛图绘制器：蜡烛 + MA + BOLL + 网格 + 坐标标签。
class _CandlePainter extends CustomPainter {
  final List<KLineData> data;
  final bool showMa;
  final bool showBoll;

  _CandlePainter({
    required this.data,
    required this.showMa,
    required this.showBoll,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final n = data.length;
    if (n == 0 || size.width <= 0 || size.height <= 0) return;

    const leftMargin = 52.0;
    const rightMargin = 8.0;
    const topPad = 8.0;
    const bottomPad = 16.0;
    final chartWidth = size.width - leftMargin - rightMargin;
    final chartHeight = size.height - topPad - bottomPad;
    if (chartWidth <= 0 || chartHeight <= 0) return;

    // ── 价格范围（含 BOLL 上下轨） ──
    var maxY = data.map((d) => d.high).reduce(max);
    var minY = data.map((d) => d.low).reduce(min);
    if (showBoll) {
      for (var i = 0; i < n; i++) {
        final u = _boll(data, i).$1;
        final l = _boll(data, i).$2;
        if (u != null) maxY = max(maxY, u);
        if (l != null) minY = min(minY, l);
      }
    }
    final rawRange = maxY - minY;
    if (rawRange <= 0) return;
    maxY += rawRange * 0.05;
    minY -= rawRange * 0.05;
    final fullRange = maxY - minY;

    double yFor(double v) => topPad + (maxY - v) / fullRange * chartHeight;
    final slot = chartWidth / n;
    final bodyW = max(1.0, min(slot * 0.7, 14.0));
    double xCenter(int i) => leftMargin + i * slot + slot / 2;

    // ── 网格（4条水平线） ──
    final gridPaint = Paint()
      ..color = AppTheme.textHint.withOpacity(0.12)
      ..strokeWidth = 0.5;
    for (var g = 0; g <= 4; g++) {
      final y = topPad + chartHeight * g / 4;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }

    // ── 蜡烛（红涨绿跌） ──
    for (var i = 0; i < n; i++) {
      final k = data[i];
      final color = k.isUp ? AppTheme.upColor : AppTheme.downColor;
      final cx = xCenter(i);
      final wickPaint = Paint()
        ..color = color
        ..strokeWidth = 1.0;

      // 影线
      canvas.drawLine(
          Offset(cx, yFor(k.high)), Offset(cx, yFor(k.low)), wickPaint);

      // 实体
      final yOpen = yFor(k.open);
      final yClose = yFor(k.close);
      if ((yOpen - yClose).abs() < 0.5) {
        // 十字星
        final crossPaint = Paint()
          ..color = color
          ..strokeWidth = bodyW;
        canvas.drawLine(Offset(cx, yOpen), Offset(cx, yClose), crossPaint);
      } else {
        canvas.drawRect(
          Rect.fromLTRB(cx - bodyW / 2, min(yOpen, yClose), cx + bodyW / 2,
              max(yOpen, yClose)),
          Paint()..color = color,
        );
      }
    }

    // ── MA5 / MA10 ──
    if (showMa) {
      _drawSeries(canvas, data, 5, Colors.orange, xCenter, yFor);
      _drawSeries(canvas, data, 10, Colors.blue, xCenter, yFor);
    }

    // ── BOLL 布林带（MA20 ± 2σ） ──
    if (showBoll) {
      _drawBollBands(canvas, data, xCenter, yFor);
    }

    // ── Y轴价格标签 ──
    final labelPainter = TextPainter(textDirection: TextDirection.ltr);
    for (var g = 0; g <= 4; g++) {
      final y = topPad + chartHeight * g / 4;
      final v = maxY - fullRange * g / 4;
      labelPainter.text = TextSpan(
        text: v.toStringAsFixed(2),
        style: const TextStyle(fontSize: 9, color: AppTheme.textHint),
      );
      labelPainter.layout();
      labelPainter.paint(canvas, Offset(4, y - labelPainter.height / 2));
    }

    // ── X轴日期标签（约5个） ──
    final datePainter = TextPainter(textDirection: TextDirection.ltr);
    final step = max(1, (n / 5).round());
    for (var i = 0; i < n; i += step) {
      final k = data[i];
      datePainter.text = TextSpan(
        text: '${k.date.month}/${k.date.day}',
        style: const TextStyle(fontSize: 9, color: AppTheme.textHint),
      );
      datePainter.layout();
      datePainter.paint(
          canvas,
          Offset(xCenter(i) - datePainter.width / 2,
              size.height - datePainter.height));
    }
  }

  void _drawSeries(Canvas canvas, List<KLineData> data, int period, Color color,
      double Function(int) xCenter, double Function(double) yFor) {
    final path = Path();
    var started = false;
    for (var i = period - 1; i < data.length; i++) {
      double sum = 0;
      for (var j = i - period + 1; j <= i; j++) {
        sum += data[j].close;
      }
      final pt = Offset(xCenter(i), yFor(sum / period));
      if (!started) {
        path.moveTo(pt.dx, pt.dy);
        started = true;
      } else {
        path.lineTo(pt.dx, pt.dy);
      }
    }
    canvas.drawPath(
        path,
        Paint()
          ..color = color
          ..strokeWidth = 1.0
          ..style = PaintingStyle.stroke);
  }

  void _drawBollBands(Canvas canvas, List<KLineData> data,
      double Function(int) xCenter, double Function(double) yFor) {
    final upper = Path();
    final lower = Path();
    var started = false;
    for (var i = 19; i < data.length; i++) {
      final (u, l) = _boll(data, i);
      if (u == null || l == null) continue;
      final up = Offset(xCenter(i), yFor(u));
      final low = Offset(xCenter(i), yFor(l));
      if (!started) {
        upper.moveTo(up.dx, up.dy);
        lower.moveTo(low.dx, low.dy);
        started = true;
      } else {
        upper.lineTo(up.dx, up.dy);
        lower.lineTo(low.dx, low.dy);
      }
    }
    final paint = Paint()
      ..color = Colors.purple.withOpacity(0.55)
      ..strokeWidth = 1.0
      ..style = PaintingStyle.stroke;
    canvas.drawPath(upper, paint);
    canvas.drawPath(lower, paint);
  }

  /// 返回第 i 根的 BOLL 上下轨 (upper, lower)，数据不足时返回 null。
  (double?, double?) _boll(List<KLineData> data, int i) {
    if (i < 19) return (null, null);
    double sum = 0;
    for (var j = i - 19; j <= i; j++) {
      sum += data[j].close;
    }
    final ma = sum / 20;
    double variance = 0;
    for (var j = i - 19; j <= i; j++) {
      variance += pow(data[j].close - ma, 2);
    }
    final sigma = sqrt(variance / 20);
    return (ma + 2 * sigma, ma - 2 * sigma);
  }

  @override
  bool shouldRepaint(covariant _CandlePainter old) =>
      old.data != data || old.showMa != showMa || old.showBoll != showBoll;
}
