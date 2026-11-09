/// PC端完整信号数据（COS signals/today.json）
/// 包含每只推荐股票的操作区间、止损止盈、建议仓位、形态分、买入理由
class PcTradeSignal {
  final String symbol;
  final String name;
  final List<String> strategies;
  final int strategyCount;
  final double confidence;
  final String level;
  final String levelLabel;
  final double currentPrice;
  final String lastDate;
  final double entryLow;
  final double entryHigh;
  final double entryMid;
  final String entryCondition;
  final double stopLoss;
  final double target1;
  final double? target2;
  final int maxHoldDays;
  final double riskPct;
  final double rewardPct;
  final double riskRewardRatio;
  final String riskRewardText;
  final String zoneStatus;
  final String zoneReason;
  final double entryRef;
  final double positionPct;
  final double atr;
  final Map<String, dynamic> scoreDetails;
  final String rationale;

  PcTradeSignal({
    required this.symbol,
    required this.name,
    required this.strategies,
    required this.strategyCount,
    required this.confidence,
    required this.level,
    required this.levelLabel,
    required this.currentPrice,
    required this.lastDate,
    required this.entryLow,
    required this.entryHigh,
    required this.entryMid,
    required this.entryCondition,
    required this.stopLoss,
    required this.target1,
    this.target2,
    required this.maxHoldDays,
    required this.riskPct,
    required this.rewardPct,
    required this.riskRewardRatio,
    this.riskRewardText = '—',
    this.zoneStatus = '',
    this.zoneReason = '',
    this.entryRef = 0,
    required this.positionPct,
    required this.atr,
    required this.scoreDetails,
    required this.rationale,
  });

  factory PcTradeSignal.fromJson(Map<String, dynamic> json) {
    final strategies = (json['strategies'] as List?)
            ?.map((e) => e.toString())
            .toList() ??
        <String>[];
    final target2 = json['target_2'];
    return PcTradeSignal(
      symbol: json['symbol'] as String? ?? '',
      name: json['name'] as String? ?? '',
      strategies: strategies,
      strategyCount: (json['strategy_count'] as num?)?.toInt() ?? strategies.length,
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0,
      level: json['level'] as String? ?? '',
      levelLabel: json['level_label'] as String? ?? '',
      currentPrice: (json['current_price'] as num?)?.toDouble() ?? 0,
      lastDate: json['last_date'] as String? ?? '',
      entryLow: (json['entry_low'] as num?)?.toDouble() ?? 0,
      entryHigh: (json['entry_high'] as num?)?.toDouble() ?? 0,
      entryMid: (json['entry_mid'] as num?)?.toDouble() ?? 0,
      entryCondition: json['entry_condition'] as String? ?? '',
      stopLoss: (json['stop_loss'] as num?)?.toDouble() ?? 0,
      target1: (json['target_1'] as num?)?.toDouble() ?? 0,
      target2: target2 is num ? target2.toDouble() : null,
      maxHoldDays: (json['max_hold_days'] as num?)?.toInt() ?? 20,
      riskPct: (json['risk_pct'] as num?)?.toDouble() ?? 0,
      rewardPct: (json['reward_pct'] as num?)?.toDouble() ?? 0,
      riskRewardRatio: (json['risk_reward_ratio'] as num?)?.toDouble() ?? 0,
      riskRewardText: json['risk_reward_text'] as String? ?? '—',
      zoneStatus: json['zone_status'] as String? ?? '',
      zoneReason: json['zone_reason'] as String? ?? '',
      entryRef: (json['entry_ref'] as num?)?.toDouble() ?? 0,
      positionPct: (json['position_pct'] as num?)?.toDouble() ?? 0,
      atr: (json['atr'] as num?)?.toDouble() ?? 0,
      scoreDetails: json['score_details'] as Map<String, dynamic>? ?? {},
      rationale: json['rationale'] as String? ?? '',
    );
  }

  /// 操作区间字符串，如 "12.34 - 13.20"
  String get buyRangeText =>
      (entryLow > 0 && entryHigh > 0) ? '${entryLow.toStringAsFixed(2)} - ${entryHigh.toStringAsFixed(2)}' : '';
}

/// PC端每日信号列表（signals/today.json）
class PcSignalList {
  /// 本次 PC 运行的批次号（见 PcFullRatings.runId）。
  final String runId;
  final String marketRegime;
  final double suggestedExposure;
  final int totalSignals;
  final Map<String, int> ratingSummary;
  final List<PcTradeSignal> signals;

  PcSignalList({
    this.runId = '',
    required this.marketRegime,
    required this.suggestedExposure,
    required this.totalSignals,
    required this.ratingSummary,
    required this.signals,
  });

  factory PcSignalList.fromJson(Map<String, dynamic> json) {
    final rawSummary = json['rating_summary'] as Map<String, dynamic>? ?? {};
    return PcSignalList(
      runId: json['run_id'] as String? ?? '',
      marketRegime: json['market_regime'] as String? ?? '',
      suggestedExposure: (json['suggested_exposure'] as num?)?.toDouble() ?? 0,
      totalSignals: (json['total_signals'] as num?)?.toInt() ?? 0,
      ratingSummary: rawSummary.map((k, v) {
        final n = v is num ? v.toInt() : int.tryParse(v?.toString() ?? '');
        return MapEntry(k, n ?? 0);
      }),
      signals: (json['signals'] as List?)
              ?.map((e) => PcTradeSignal.fromJson(e as Map<String, dynamic>))
              .toList() ??
          [],
    );
  }
}
