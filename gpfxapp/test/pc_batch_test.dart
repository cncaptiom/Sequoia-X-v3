import 'package:flutter_test/flutter_test.dart';
import 'package:gpfxapp/models/pc_recommendation.dart';
import 'package:gpfxapp/models/trade_signal.dart';

/// 批次一致性依赖各文件都能解析出 run_id（2026-11-09 审查 #8）。
void main() {
  test('summary 解析 run_id 与数据日期', () {
    final s = PcPortfolioSummary.fromJson({
      'run_id': 'run-abc',
      'date': '2026-11-06',
      'generated_at': '2026-11-06 21:00:00',
    });
    expect(s.runId, 'run-abc');
    expect(s.date, '2026-11-06');
  });

  test('全量评级解析 run_id', () {
    final r = PcFullRatings.fromJson({
      'run_id': 'run-abc',
      'date': '2026-11-06',
      'ratings': [],
    });
    expect(r.runId, 'run-abc');
  });

  test('信号文件解析 run_id', () {
    final s = PcSignalList.fromJson({
      'run_id': 'run-abc',
      'signals': [],
    });
    expect(s.runId, 'run-abc');
  });

  test('旧版文件没有 run_id 时为空串，不做强校验', () {
    final s = PcPortfolioSummary.fromJson({'date': '2026-11-06'});
    expect(s.runId, '');
  });
}
