import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:gpfxapp/models/pc_recommendation.dart';
import 'package:gpfxapp/models/trade_signal.dart';
import 'package:gpfxapp/providers/pc_data_provider.dart';
import 'package:gpfxapp/services/cos_api_service.dart';

/// 刷新竞态（2026-11-09 审查 #13）：旧请求晚返回不能覆盖新请求的数据。
class _FakeCos extends CosApiService {
  _FakeCos(this.summaryResponses);

  final List<Future<PcPortfolioSummary?>> summaryResponses;
  int _calls = 0;

  @override
  Future<Map<String, dynamic>?> fetchManifest() async => null;

  @override
  Future<PcPortfolioSummary?> fetchPortfolioSummary() {
    final future = summaryResponses[_calls];
    _calls++;
    return future;
  }

  @override
  Future<PcFullRatings?> fetchFullRatings() async => null;

  @override
  Future<Map<String, dynamic>?> fetchStrategyBacktest() async => null;

  @override
  Future<PcSignalList?> fetchSignals() async => null;
}

PcPortfolioSummary _summary(String runId, String date) => PcPortfolioSummary(
      runId: runId,
      date: date,
      generatedAt: '$date 21:00:00',
      marketRegime: '震荡',
      suggestedExposure: 0.5,
      ratingSummary: const {},
      topPicks: const [],
    );

void main() {
  test('先发出的慢请求晚返回时不得覆盖后发出的新数据', () async {
    final slow = Completer<PcPortfolioSummary?>();
    final fast = Completer<PcPortfolioSummary?>();
    final provider = PcDataProvider(cos: _FakeCos([slow.future, fast.future]));

    // 让第一次刷新先跑到等待摘要的位置，再发起第二次
    final first = provider.refresh();
    await Future<void>.delayed(Duration.zero);
    final second = provider.refresh();
    await Future<void>.delayed(Duration.zero);

    fast.complete(_summary('run-new', '2026-11-06'));
    await second;
    expect(provider.summary?.runId, 'run-new');

    // 旧请求这时才返回：序号已过期，必须整份丢弃
    slow.complete(_summary('run-old', '2026-11-05'));
    await first;

    expect(provider.summary?.runId, 'run-new');
    expect(provider.summary?.date, '2026-11-06');
    expect(provider.isLoading, isFalse);
  });

  test('manifest 请求异常时不得降级为无校验模式（R2-02）', () async {
    final provider = PcDataProvider(cos: _ManifestFailCos());
    await provider.refresh();

    expect(provider.summary, isNull);          // 没有把新数据落地
    expect(provider.error, contains('批次清单'));  // 明确报错
    expect(provider.isLoading, isFalse);
  });

  test('只有一次刷新时结果正常落地', () async {
    final only = Completer<PcPortfolioSummary?>();
    final provider = PcDataProvider(cos: _FakeCos([only.future]));

    final running = provider.refresh();
    await Future<void>.delayed(Duration.zero);
    only.complete(_summary('run-1', '2026-11-06'));
    await running;

    expect(provider.summary?.runId, 'run-1');
    expect(provider.lastFetch, isNotNull);
    expect(provider.isLoading, isFalse);
  });
}

class _ManifestFailCos extends CosApiService {
  @override
  Future<Map<String, dynamic>?> fetchManifest() async {
    throw Exception('network down');
  }

  @override
  Future<PcPortfolioSummary?> fetchPortfolioSummary() async =>
      _summary('run-new', '2026-11-06');
}

