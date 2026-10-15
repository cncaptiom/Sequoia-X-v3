import '../models/analysis_result.dart';
import '../models/pc_recommendation.dart';

/// 用 PC 数据覆盖本地分析结果（评分 + 评级 + 止损止盈 + 买入区间）。
///
/// PC 是权威评级源，本地仅离线兜底；首页自选卡与详情页共用本函数，
/// 保证同一股票在两处的评级一致（避免"首页关注 / 详情页回避"矛盾）。
AnalysisResult mergePcData(AnalysisResult original, PcRatingResult pcRating) {
  // 1. 合并策略评分（PC 端优先，缺失的用本地补）
  //    优先用每策略标准化分（展示策略间真实差异），空则回退类内平均分
  final pcScores = pcRating.perStrategyScores.isNotEmpty
      ? pcRating.perStrategyScores
      : pcRating.strategyScores;
  final mergedScores = Map<String, double>.from(pcScores);
  original.strategyScores.forEach((k, v) {
    mergedScores.putIfAbsent(k, () => v);
  });

  // 2. PC 评级覆盖本地（决策卡与条件单建议应显示同一评级）
  final pcRatingEnum = _parsePcRating(pcRating.rating);

  return AnalysisResult(
    rating: pcRatingEnum ?? original.rating,
    totalScore: pcRating.totalScore,
    strategyScores: mergedScores,
    trendPassed: original.trendPassed,
    trendDescription: pcRating.trendDescription,
    riskRewardRatio: pcRating.riskRewardRatio,
    stopLossPrice: pcRating.stopLossPrice,
    takeProfitPrice: pcRating.takeProfitPrice,
    buyRange: pcRating.buyRange,
    signals: original.signals,
    time: original.time,
    // 分数/评级已经换成 PC 口径，下游按 PC 刻度做的判定要靠这个标记区分
    isLocalEstimate: false,
  );
}

Rating? _parsePcRating(String? rating) {
  switch (rating) {
    case 'strong_buy':
      return Rating.strongBuy;
    case 'buy':
      return Rating.buy;
    case 'watch':
      return Rating.watch;
    case 'attention':
      return Rating.attention;
    case 'avoid':
      return Rating.avoid;
    default:
      return null;
  }
}
