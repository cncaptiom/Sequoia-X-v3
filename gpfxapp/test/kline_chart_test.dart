import 'dart:math';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:gpfxapp/models/kline_data.dart';
import 'package:gpfxapp/providers/stock_provider.dart';
import 'package:gpfxapp/widgets/kline_chart.dart';

/// 测试用 StockProvider：覆写 klineData 提供本地假数据，避免真实网络请求。
class _FakeStockProvider extends StockProvider {
  List<KLineData> _data = [];

  @override
  List<KLineData> get klineData => _data;

  void setData(List<KLineData> d) => _data = d;
}

List<KLineData> _makeBars(int n) {
  final bars = <KLineData>[];
  var close = 10.0;
  for (var i = 0; i < n; i++) {
    final open = close;
    close += (i % 3 == 0) ? 0.3 : -0.2;
    final high = max(open, close) + 0.15;
    final low = min(open, close) - 0.15;
    bars.add(KLineData(
      date: DateTime(2026, 1, 1).add(Duration(days: i)),
      open: open,
      close: close,
      high: high,
      low: low,
      volume: 10000 + i * 100.0,
      amount: 0,
    ));
  }
  return bars;
}

Widget _wrap(StockProvider provider) => ChangeNotifierProvider<StockProvider>.value(
      value: provider,
      child: const MaterialApp(home: Scaffold(body: KLineChart())),
    );

void main() {
  testWidgets('空K线时不渲染内容', (tester) async {
    final fake = _FakeStockProvider()..setData([]);
    await tester.pumpWidget(_wrap(fake));
    expect(find.byType(KLineChart), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('正常K线渲染蜡烛图与指标芯片', (tester) async {
    final fake = _FakeStockProvider()..setData(_makeBars(60));
    await tester.pumpWidget(_wrap(fake));
    expect(find.text('K线走势'), findsOneWidget);
    expect(find.text('MA'), findsOneWidget);
    expect(find.text('BOLL'), findsOneWidget);
    expect(find.text('RSI'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('切换 BOLL/RSI 芯片不崩溃', (tester) async {
    final fake = _FakeStockProvider()..setData(_makeBars(60));
    await tester.pumpWidget(_wrap(fake));

    await tester.tap(find.text('BOLL'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('RSI'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('MA'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('单根K线（不足20根BOLL样本）不崩溃', (tester) async {
    final fake = _FakeStockProvider()..setData(_makeBars(1));
    await tester.pumpWidget(_wrap(fake));
    await tester.tap(find.text('BOLL'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('全部K线价格相同（无价格区间）不崩溃', (tester) async {
    final bars = List.generate(30, (i) => KLineData(
          date: DateTime(2026, 1, 1).add(Duration(days: i)),
          open: 10,
          close: 10,
          high: 10,
          low: 10,
          volume: 1000,
          amount: 0,
        ));
    final fake = _FakeStockProvider()..setData(bars);
    await tester.pumpWidget(_wrap(fake));
    await tester.tap(find.text('RSI'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });
}
