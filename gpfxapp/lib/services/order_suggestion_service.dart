import '../models/analysis_result.dart';
import '../models/order_suggestion.dart';
import '../models/strategy_config.dart';

/// 条件单建议生成服务
/// 根据分析结果（评级/趋势/支撑压力/止损止盈）生成招商证券条件单参数
class OrderSuggestionService {
  /// 默认每笔股数
  static const int _defaultShares = 100;

  /// 生成条件单建议列表（按优先级排序）
  static List<OrderSuggestion> generate({
    required AnalysisResult analysis,
    required double currentPrice,
    required Rating rating,
    bool isHeld = false,
    int? positionShares,
    TradingStyle style = TradingStyle.aggressive,
  }) {
    final suggestions = <OrderSuggestion>[];
    final shares = (positionShares ?? _defaultShares);

    // ── 买入类建议（未持仓或可加仓） ──
    if (!isHeld || rating == Rating.strongBuy || rating == Rating.buy) {
      _addBuySuggestions(suggestions, analysis, currentPrice, rating, shares, style);
    }

    // ── 卖出类建议（已持仓） ──
    if (isHeld) {
      _addSellSuggestions(suggestions, analysis, currentPrice, shares);
    }

    // 按优先级排序
    suggestions.sort((a, b) => a.priority.compareTo(b.priority));
    return suggestions;
  }

  /// 生成买入条件单建议（按风格调整逻辑）
  static void _addBuySuggestions(
    List<OrderSuggestion> suggestions,
    AnalysisResult analysis,
    double currentPrice,
    Rating rating,
    int shares,
    TradingStyle style,
  ) {
    if (!analysis.trendPassed) return; // 下降趋势不推荐买入

    // 从 buyRange 解析买入区间
    double? buyZoneLow, buyZoneHigh;
    if (analysis.buyRange != null) {
      final parts = analysis.buyRange!.split(' - ');
      if (parts.length == 2) {
        buyZoneLow = double.tryParse(parts[0]);
        buyZoneHigh = double.tryParse(parts[1]);
      }
    }

    switch (style) {
      case TradingStyle.aggressive:
        _addAggressiveBuys(suggestions, analysis, currentPrice, rating, shares, buyZoneLow);
      case TradingStyle.conservative:
        _addConservativeBuys(suggestions, analysis, currentPrice, rating, shares, buyZoneLow, buyZoneHigh);
      case TradingStyle.signal:
        _addSignalBuys(suggestions, analysis, currentPrice, rating, shares, buyZoneLow);
    }
  }

  /// 激进型买入：趋势确认即追入，突破优先
  static void _addAggressiveBuys(
    List<OrderSuggestion> suggestions,
    AnalysisResult analysis,
    double currentPrice,
    Rating rating,
    int shares,
    double? buyZoneLow,
  ) {
    // 1. 评分高+趋势强 → 现价附近直接买入（追突破）
    if (rating == Rating.strongBuy || rating == Rating.buy) {
      final buyPrice = currentPrice * 0.99;
      suggestions.add(OrderSuggestion(
        type: OrderType.fixedBuy,
        triggerPrice: buyPrice,
        condition: '成交价 ≤',
        shares: shares,
        description: '趋势强劲，建议现价附近追入',
        priority: 5,
      ));
    }

    // 2. 价格接近支撑位 → 突破确认买入
    if (buyZoneLow != null && buyZoneLow < currentPrice) {
      final triggerPrice = buyZoneLow * 1.05; // 支撑上方5%，突破确认
      if (triggerPrice < currentPrice) {
        suggestions.add(OrderSuggestion(
          type: OrderType.reboundBuy,
          triggerPrice: buyZoneLow * 1.02,
          triggerPrice2: triggerPrice,
          condition: '现价≤触发价后反弹至',
          shares: shares,
          description: '价格回踩支撑后反弹确认，趋势中继买入',
          priority: 15,
        ));
      }
    }

    // 3. 强烈买入无支撑 → 定价现价
    if (rating == Rating.strongBuy && currentPrice > 0) {
      suggestions.add(OrderSuggestion(
        type: OrderType.fixedBuy,
        triggerPrice: currentPrice * 0.98,
        condition: '成交价 ≤',
        shares: shares,
        description: '强烈买入评级，定价买入',
        priority: 10,
      ));
    }
  }

  /// 保守型买入：安全边际优先，等回调
  static void _addConservativeBuys(
    List<OrderSuggestion> suggestions,
    AnalysisResult analysis,
    double currentPrice,
    Rating rating,
    int shares,
    double? buyZoneLow,
    double? buyZoneHigh,
  ) {
    // 1. 定价买入：支撑上方3%，接近支撑即买
    if (buyZoneLow != null && buyZoneLow < currentPrice) {
      final buyPrice = buyZoneLow * 1.03;
      if (buyPrice < currentPrice) {
        suggestions.add(OrderSuggestion(
          type: OrderType.fixedBuy,
          triggerPrice: buyPrice,
          condition: '成交价 ≤',
          shares: shares,
          description: '价格接近支撑位(${buyZoneLow.toStringAsFixed(2)})，安全边际充足',
          priority: 10,
        ));
      }
    }

    // 2. 反弹买入：回踩支撑确认
    if (buyZoneLow != null && buyZoneLow < currentPrice) {
      final triggerPrice = buyZoneLow * 1.03;
      if (triggerPrice < currentPrice) {
        suggestions.add(OrderSuggestion(
          type: OrderType.reboundBuy,
          triggerPrice: triggerPrice,
          triggerPrice2: triggerPrice * 1.02,
          condition: '现价≤触发价后反弹至',
          shares: shares,
          description: '价格回落到支撑附近，反弹确认后买入',
          priority: 20,
        ));
      }
    }

    // 3. 分批买入：跌破支撑加仓
    if (buyZoneLow != null && buyZoneHigh != null) {
      final batchPrice = buyZoneLow * 0.97;
      suggestions.add(OrderSuggestion(
        type: OrderType.batchBuy2,
        triggerPrice: batchPrice,
        condition: '成交价 ≤',
        shares: shares,
        description: '价格跌破支撑，分批加仓摊低成本',
        priority: 30,
      ));
    }

    // 4. 评分高且便宜 → 定价现价附近
    if (rating == Rating.strongBuy && currentPrice > 0) {
      suggestions.add(OrderSuggestion(
        type: OrderType.fixedBuy,
        triggerPrice: currentPrice * 0.98,
        condition: '成交价 ≤',
        shares: shares,
        description: '强烈买入评级，建议轻仓入场',
        priority: 5,
      ));
    }
  }

  /// 信号型买入：指标共振确认，不追高
  static void _addSignalBuys(
    List<OrderSuggestion> suggestions,
    AnalysisResult analysis,
    double currentPrice,
    Rating rating,
    int shares,
    double? buyZoneLow,
  ) {
    // 检查是否有 MACD/指标类买入信号
    final hasSignalBuy = analysis.signals.any((s) =>
        s.name.contains('金叉') ||
        s.name.contains('底背离') ||
        s.name.contains('共振'));

    // 1. 反弹买入：回踩+指标确认
    if (buyZoneLow != null && buyZoneLow < currentPrice && hasSignalBuy) {
      final triggerPrice = buyZoneLow * 1.03;
      if (triggerPrice < currentPrice) {
        final confirmPrice = triggerPrice * 1.02;
        suggestions.add(OrderSuggestion(
          type: OrderType.reboundBuy,
          triggerPrice: triggerPrice,
          triggerPrice2: confirmPrice,
          condition: '现价≤触发价后反弹至',
          shares: shares,
          description: '指标金叉确认+回踩支撑，共振买入',
          priority: 10,
        ));
      }
    }

    // 2. 有信号无支撑 → 定价现价
    if (hasSignalBuy && rating != Rating.avoid && currentPrice > 0) {
      suggestions.add(OrderSuggestion(
        type: OrderType.fixedBuy,
        triggerPrice: currentPrice * 0.98,
        condition: '成交价 ≤',
        shares: shares,
        description: '多指标发出买入信号，建议入场',
        priority: 15,
      ));
    }

    // 3. 强烈买入且无信号异常 → 轻仓入场
    if (rating == Rating.strongBuy && currentPrice > 0) {
      suggestions.add(OrderSuggestion(
        type: OrderType.fixedBuy,
        triggerPrice: currentPrice * 0.98,
        condition: '成交价 ≤',
        shares: shares,
        description: '强烈买入评级，指标配合，建议入场',
        priority: 5,
      ));
    }
  }

  /// 生成卖出条件单建议
  static void _addSellSuggestions(
    List<OrderSuggestion> suggestions,
    AnalysisResult analysis,
    double currentPrice,
    int shares,
  ) {
    // 1. 止损卖出
    if (analysis.stopLossPrice != null && analysis.stopLossPrice! < currentPrice) {
      suggestions.add(OrderSuggestion(
        type: OrderType.stopLoss,
        triggerPrice: analysis.stopLossPrice!,
        condition: '成交价 ≤',
        shares: shares,
        description: '价格触及止损位，建议严格止损',
        priority: 1, // 最高优先级
      ));
    }

    // 2. 止盈卖出
    if (analysis.takeProfitPrice != null && analysis.takeProfitPrice! > currentPrice) {
      suggestions.add(OrderSuggestion(
        type: OrderType.takeProfit,
        triggerPrice: analysis.takeProfitPrice!,
        condition: '成交价 ≥',
        shares: shares,
        description: '价格触及止盈位，建议落袋为安',
        priority: 2,
      ));
    }

    // 3. 回落卖出：价格涨到止盈位后回落时卖出（吃更多利润）
    if (analysis.takeProfitPrice != null && analysis.takeProfitPrice! > currentPrice) {
      final pullbackPrice = analysis.takeProfitPrice! * 0.97;
      suggestions.add(OrderSuggestion(
        type: OrderType.pullbackSell,
        triggerPrice: analysis.takeProfitPrice!,
        condition: '现价≥触发价后回落至 $pullbackPrice',
        triggerPrice2: pullbackPrice,
        shares: shares,
        description: '价格先达止盈位再回落，建议回落卖出锁定利润',
        priority: 15,
      ));
    }

    // 4. 如果评级变为回避，建议立即卖出
    if (analysis.rating == Rating.avoid) {
      suggestions.add(OrderSuggestion(
        type: OrderType.stopLoss,
        triggerPrice: currentPrice * 0.99,
        condition: '成交价 ≤',
        shares: shares,
        description: '评级转为回避，建议市价卖出',
        priority: 3,
      ));
    }
  }
}
