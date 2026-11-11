import 'package:flutter_test/flutter_test.dart';
import 'package:gpfxapp/models/pc_recommendation.dart';
import 'package:gpfxapp/models/trade_signal.dart';
import 'package:gpfxapp/providers/pc_data_provider.dart';

/// 批次一致性校验（2026-11-09 审查 #8）。
void main() {
  PcPortfolioSummary summary(String runId) => PcPortfolioSummary(
        runId: runId,
        date: '2026-11-06',
        generatedAt: '2026-11-06 21:00:00',
        marketRegime: '震荡',
        suggestedExposure: 0.5,
        ratingSummary: const {},
        topPicks: const [],
      );

  PcFullRatings ratings(String runId) =>
      PcFullRatings(runId: runId, version: '3.1.0', date: '2026-11-06',
          generatedAt: '', strategyConfig: const {}, marketRegime: const {},
          totalScanned: 0, ratings: const []);

  PcSignalList signals(String runId) => PcSignalList(
        runId: runId,
        marketRegime: '震荡',
        suggestedExposure: 0.5,
        totalSignals: 0,
        ratingSummary: const {},
        signals: const [],
      );

  const listed = {'summary/today.json', 'ratings/today.json', 'signals/today.json'};

  test('没有 manifest 时不做批次强校验（兼容旧版 PC）', () {
    final err = PcDataProvider.checkBatch(
      expectedRunId: '',
      listed: const {},
      summary: summary(''),
      ratings: ratings(''),
      signals: signals(''),
    );
    expect(err, isNull);
  });

  test('同一批次齐全时通过', () {
    final err = PcDataProvider.checkBatch(
      expectedRunId: 'run-1',
      listed: listed,
      summary: summary('run-1'),
      ratings: ratings('run-1'),
      signals: signals('run-1'),
    );
    expect(err, isNull);
  });

  test('manifest 列出的文件没取到时判为不完整', () {
    final err = PcDataProvider.checkBatch(
      expectedRunId: 'run-1',
      listed: listed,
      summary: summary('run-1'),
      ratings: null,
      signals: signals('run-1'),
    );
    expect(err, contains('不完整'));
  });

  test('run_id 不一致时判为混合批次', () {
    final err = PcDataProvider.checkBatch(
      expectedRunId: 'run-1',
      listed: listed,
      summary: summary('run-1'),
      ratings: ratings('run-2'),
      signals: signals('run-1'),
    );
    expect(err, contains('批次不一致'));
  });


  test('manifest 未列出的文件被识别为 absent（旧状态必须清空）', () {
    final absent = PcDataProvider.absentFiles({'summary/today.json'});
    expect(absent, {'ratings/today.json', 'signals/today.json'});
  });

  test('manifest 列出全部文件时没有 absent', () {
    expect(PcDataProvider.absentFiles(listed), isEmpty);
  });

  test('旧版文件缺 run_id 也算不一致（不能和旧文件拼批）', () {
    final err = PcDataProvider.checkBatch(
      expectedRunId: 'run-1',
      listed: listed,
      summary: summary('run-1'),
      ratings: ratings(''),
      signals: signals('run-1'),
    );
    expect(err, contains('批次不一致'));
  });
}
