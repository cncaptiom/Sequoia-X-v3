/// 来自PC端 Sequoia-X 的远程评级分析结果
class PcRatingResult {
  final String symbol;
  final String name;
  final String rating;
  final String ratingLabel;
  final String ratingStars;
  final double totalScore;
  final bool trendPassed;
  final String trendDescription;
  final Map<String, double> strategyScores;
  /// 各策略标准化分（截面 Z-score，非类内平均），展示策略间真实差异；空则回退 strategyScores
  final Map<String, double> perStrategyScores;
  final Map<String, double> categoryScores;
  final List<String> signals;
  final double riskRewardRatio;
  final double? stopLossPrice;
  final double? takeProfitPrice;
  final String buyRange;
  final double currentPrice;

  PcRatingResult({
    required this.symbol,
    required this.name,
    required this.rating,
    required this.ratingLabel,
    required this.ratingStars,
    required this.totalScore,
    required this.trendPassed,
    required this.trendDescription,
    required this.strategyScores,
    required this.perStrategyScores,
    required this.categoryScores,
    required this.signals,
    required this.riskRewardRatio,
    this.stopLossPrice,
    this.takeProfitPrice,
    required this.buyRange,
    required this.currentPrice,
  });

  factory PcRatingResult.fromJson(Map<String, dynamic> json) {
    return PcRatingResult(
      symbol: json['symbol'] as String? ?? '',
      name: json['name'] as String? ?? '',
      rating: json['rating'] as String? ?? 'avoid',
      ratingLabel: json['rating_label'] as String? ?? '回避',
      ratingStars: json['rating_stars'] as String? ?? '★',
      totalScore: (json['total_score'] as num?)?.toDouble() ?? 0,
      trendPassed: json['trend_passed'] as bool? ?? false,
      trendDescription: json['trend_description'] as String? ?? '',
      strategyScores: _parseStringDoubleMap(json['strategy_scores']),
      perStrategyScores: _parseStringDoubleMap(json['per_strategy_scores']),
      categoryScores: _parseStringDoubleMap(json['category_scores']),
      signals:
          (json['signals'] as List?)?.map((e) => e.toString()).toList() ?? [],
      riskRewardRatio: (json['risk_reward_ratio'] as num?)?.toDouble() ?? 0,
      stopLossPrice: (json['stop_loss_price'] as num?)?.toDouble(),
      takeProfitPrice: (json['take_profit_price'] as num?)?.toDouble(),
      buyRange: json['buy_range'] as String? ?? '',
      currentPrice: (json['current_price'] as num?)?.toDouble() ?? 0,
    );
  }

  static Map<String, double> _parseStringDoubleMap(dynamic raw) {
    if (raw is Map) {
      return raw.map((k, v) {
        // 容错：单条脏数据（null/字符串）不拖垮整个评级文件
        final n = v is num ? v.toDouble() : double.tryParse(v?.toString() ?? '');
        return MapEntry(k.toString(), n ?? 0);
      });
    }
    return {};
  }
}

/// PC端全市场评级快照（完整数据）
class PcFullRatings {
  final String version;
  final String date;
  final String generatedAt;
  final Map<String, dynamic> strategyConfig;
  final Map<String, dynamic> marketRegime;
  final int totalScanned;
  final List<PcRatingResult> ratings;

  PcFullRatings({
    required this.version,
    required this.date,
    required this.generatedAt,
    required this.strategyConfig,
    required this.marketRegime,
    required this.totalScanned,
    required this.ratings,
  });

  factory PcFullRatings.fromJson(Map<String, dynamic> json) {
    return PcFullRatings(
      version: json['version'] as String? ?? '',
      date: json['date'] as String? ?? '',
      generatedAt: json['generated_at'] as String? ?? '',
      strategyConfig: json['strategy_config'] as Map<String, dynamic>? ?? {},
      marketRegime: json['market_regime'] as Map<String, dynamic>? ?? {},
      totalScanned: json['total_scanned'] as int? ?? 0,
      ratings: (json['ratings'] as List?)
              ?.map((e) => PcRatingResult.fromJson(e as Map<String, dynamic>))
              .toList() ??
          [],
    );
  }
}

/// PC端每日精选摘要（首页展示用）
class PcPortfolioSummary {
  final String date;
  final String generatedAt;
  final String marketRegime;
  final double suggestedExposure;
  final Map<String, int> ratingSummary;
  final List<PcTopPick> topPicks;

  PcPortfolioSummary({
    required this.date,
    required this.generatedAt,
    required this.marketRegime,
    required this.suggestedExposure,
    required this.ratingSummary,
    required this.topPicks,
  });

  factory PcPortfolioSummary.fromJson(Map<String, dynamic> json) {
    final rawSummary = json['rating_summary'] as Map<String, dynamic>? ?? {};
    return PcPortfolioSummary(
      date: json['date'] as String? ?? '',
      generatedAt: json['generated_at'] as String? ?? '',
      marketRegime: json['market_regime'] as String? ?? '',
      suggestedExposure: (json['suggested_exposure'] as num?)?.toDouble() ?? 0,
      ratingSummary: rawSummary.map((k, v) {
        // 容错：单条脏数据（null/字符串）不拖垮整个评级文件
        final n = v is num ? v.toInt() : int.tryParse(v?.toString() ?? '');
        return MapEntry(k, n ?? 0);
      }),
      topPicks: (json['top_picks'] as List?)
              ?.map((e) => PcTopPick.fromJson(e as Map<String, dynamic>))
              .toList() ??
          [],
    );
  }
}

/// PC端精选推荐（首页展示）
class PcTopPick {
  final String symbol;
  final String name;
  final String rating;
  final String ratingLabel;
  final String ratingStars;
  final double totalScore;
  final double currentPrice;
  final int consecutiveDays;
  final double confidence;

  PcTopPick({
    required this.symbol,
    required this.name,
    required this.rating,
    required this.ratingLabel,
    required this.ratingStars,
    required this.totalScore,
    required this.currentPrice,
    this.consecutiveDays = 1,
    this.confidence = 0,
  });

  factory PcTopPick.fromJson(Map<String, dynamic> json) {
    return PcTopPick(
      symbol: json['symbol'] as String? ?? '',
      name: json['name'] as String? ?? '',
      rating: json['rating'] as String? ?? 'avoid',
      ratingLabel: json['rating_label'] as String? ?? '回避',
      ratingStars: json['rating_stars'] as String? ?? '★',
      totalScore: (json['total_score'] as num?)?.toDouble() ?? 0,
      currentPrice: (json['current_price'] as num?)?.toDouble() ?? 0,
      consecutiveDays: (json['consecutive_days'] as num?)?.toInt() ?? 1,
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0,
    );
  }
}
