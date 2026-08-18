import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gpfxapp/theme/app_theme.dart';

void main() {
  testWidgets('AppTheme 亮色主题可正常渲染', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: AppTheme.theme,
        home: const Scaffold(body: Center(child: Text('股票分析'))),
      ),
    );
    expect(find.text('股票分析'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
