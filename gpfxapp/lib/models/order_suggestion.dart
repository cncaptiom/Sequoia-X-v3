/// 条件单建议模型：将分析结果转化为招商证券条件单参数
class OrderSuggestion {
  /// 条件单类型
  final OrderType type;

  /// 触发价格（第一触发价）
  final double triggerPrice;

  /// 第二触发价（反弹买入/回落卖出需要两个价格）
  final double? triggerPrice2;

  /// 触发条件描述（如 "≤" / "≥" / "反弹至"）
  final String condition;

  /// 建议数量（股）
  final int shares;

  /// 建议说明
  final String description;

  /// 优先级（数值越小越优先）
  final int priority;

  const OrderSuggestion({
    required this.type,
    required this.triggerPrice,
    this.triggerPrice2,
    required this.condition,
    required this.shares,
    required this.description,
    required this.priority,
  });

  /// 复制到剪贴板的文本（便于粘贴到交易APP）
  String get clipboardText {
    final buf = StringBuffer('【${type.label}】');
    buf.write(' ${type.action} ');
    if (triggerPrice2 != null) {
      buf.write('触发${triggerPrice.toStringAsFixed(2)} 确认${triggerPrice2!.toStringAsFixed(2)}');
    } else {
      buf.write('${condition}${triggerPrice.toStringAsFixed(2)}');
    }
    buf.write(' ${shares}股');
    return buf.toString();
  }
}

enum OrderType {
  fixedBuy('定价买入', '买入', '价格条件单'),
  reboundBuy('反弹买入', '买入', '反弹条件单'),
  stopLoss('止损卖出', '卖出', '止损条件单'),
  takeProfit('止盈卖出', '卖出', '止盈条件单'),
  pullbackSell('回落卖出', '卖出', '回落条件单'),
  batchBuy1('分批买入①', '买入', '分批条件单'),
  batchBuy2('分批买入②', '买入', '分批条件单');

  final String label;
  final String action;
  final String category;

  const OrderType(this.label, this.action, this.category);
}
