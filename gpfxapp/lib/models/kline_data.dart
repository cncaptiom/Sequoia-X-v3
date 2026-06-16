class KLineData {
  final DateTime date;
  final double open;
  final double close;
  final double high;
  final double low;
  final double volume;
  final double amount;

  KLineData({
    required this.date,
    required this.open,
    required this.close,
    required this.high,
    required this.low,
    required this.volume,
    required this.amount,
  });

  bool get isUp => close >= open;
  bool get isDown => close < open;

  factory KLineData.fromEastMoney(List<dynamic> item) {
    // 东方财富K线格式: [日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率]
    return KLineData(
      date: DateTime.parse(item[0].toString()),
      open: double.tryParse(item[1]?.toString() ?? '0') ?? 0,
      close: double.tryParse(item[2]?.toString() ?? '0') ?? 0,
      high: double.tryParse(item[3]?.toString() ?? '0') ?? 0,
      low: double.tryParse(item[4]?.toString() ?? '0') ?? 0,
      volume: double.tryParse(item[5]?.toString() ?? '0') ?? 0,
      amount: double.tryParse(item[6]?.toString() ?? '0') ?? 0,
    );
  }

  /// 新浪财经K线API (vip.stock.finance.sina.com.cn)
  /// 格式: {"day":"2026-05-27","open":"1287.000","high":"1304.790","low":"1277.000","close":"1285.880","volume":"4635276"}
  factory KLineData.fromSina(Map<String, dynamic> data) {
    return KLineData(
      date: DateTime.parse(data['day']?.toString() ?? ''),
      open: double.tryParse(data['open']?.toString() ?? '0') ?? 0,
      high: double.tryParse(data['high']?.toString() ?? '0') ?? 0,
      low: double.tryParse(data['low']?.toString() ?? '0') ?? 0,
      close: double.tryParse(data['close']?.toString() ?? '0') ?? 0,
      volume: double.tryParse(data['volume']?.toString() ?? '0') ?? 0,
      amount: 0,
    );
  }
}

/// K线周期
enum KLinePeriod {
  daily('101', '日线'),
  weekly('102', '周线'),
  monthly('103', '月线');

  final String code;
  final String label;
  const KLinePeriod(this.code, this.label);
}
