class AnalysisResult {
  /// 综合评级
  final Rating rating;

  /// 总分
  final double totalScore;

  /// 各策略得分
  final Map<String, double> strategyScores;

  /// 趋势是否通过过滤
  final bool trendPassed;

  /// 趋势说明
  final String trendDescription;

  /// 盈亏比
  final double riskRewardRatio;

  /// 止损位
  final double? stopLossPrice;

  /// 止盈位
  final double? takeProfitPrice;

  /// 买入区间
  final String? buyRange;

  /// 详细信号列表
  final List<Signal> signals;

  /// 分析时间
  final DateTime time;

  /// 这份数据是**手机端离线估算**的，还是 PC 同步来的。
  ///
  /// 本地 totalScore 是「原始整数分 × 权重」的加权和，PC 是截面 Z 分，两者不是同一把
  /// 尺子（见 docs/mobile-datalayer-review-2026-10-15.md 第 1 条），所以下游凡是按
  /// PC 刻度做的判定都要先看这个标记。`mergePcData` 合并后置为 false。
  final bool isLocalEstimate;

  AnalysisResult({
    required this.rating,
    required this.totalScore,
    required this.strategyScores,
    required this.trendPassed,
    required this.trendDescription,
    required this.riskRewardRatio,
    this.stopLossPrice,
    this.takeProfitPrice,
    this.buyRange,
    required this.signals,
    required this.time,
    this.isLocalEstimate = true,
  });

  String get ratingLabel {
    switch (rating) {
      case Rating.strongBuy:
        return '强烈买入';
      case Rating.buy:
        return '买入';
      case Rating.watch:
        return '观望';
      case Rating.attention:
        return '关注';
      case Rating.avoid:
        return '回避';
    }
  }

  String get ratingStars {
    switch (rating) {
      case Rating.strongBuy:
        return '★★★★★';
      case Rating.buy:
        return '★★★★';
      case Rating.watch:
        return '★★★';
      case Rating.attention:
        return '★★';
      case Rating.avoid:
        return '★';
    }
  }
}

enum Rating {
  strongBuy,
  buy,
  watch,
  attention,
  avoid,
}

class Signal {
  final String name;
  final SignalType type;
  final String description;
  final double score;

  Signal({
    required this.name,
    required this.type,
    required this.description,
    required this.score,
  });
}

enum SignalType {
  bullish, // 看多
  bearish, // 看空
  neutral, // 中性
}
