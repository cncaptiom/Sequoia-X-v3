class StockQuote {
  final String code;
  final String name;
  final double currentPrice; // 当前价
  final double yesterdayClose; // 昨收
  final double openPrice; // 今开
  final double highPrice; // 最高
  final double lowPrice; // 最低
  final double volume; // 成交量（手）
  final double amount; // 成交额（万）
  final double changePercent; // 涨跌幅%
  final double changeAmount; // 涨跌额
  final double turnoverRate; // 换手率%
  final double amplitude; // 振幅%
  final double pe; // 市盈率
  final double pb; // 市净率
  final double marketCap; // 总市值（亿）
  final double circulatingMarketCap; // 流通市值（亿）
  final int volumeRatio; // 量比
  final DateTime time; // 数据时间
  final String market; // "SH"/"SZ"/"BJ" - 用于排行等场景

  StockQuote({
    required this.code,
    required this.name,
    required this.currentPrice,
    required this.yesterdayClose,
    required this.openPrice,
    required this.highPrice,
    required this.lowPrice,
    required this.volume,
    required this.amount,
    required this.changePercent,
    required this.changeAmount,
    required this.turnoverRate,
    required this.amplitude,
    required this.pe,
    required this.pb,
    required this.marketCap,
    required this.circulatingMarketCap,
    required this.volumeRatio,
    required this.time,
    this.market = '',
  });

  /// 是否为上涨（中国股市红色涨）
  bool get isUp => currentPrice >= yesterdayClose;

  /// 是否为下跌
  bool get isDown => currentPrice < yesterdayClose;

  /// 创建副本（用于补充腾讯API缺失的字段）
  StockQuote copyWith({
    double? pe,
    double? pb,
    int? volumeRatio,
  }) {
    return StockQuote(
      code: code,
      name: name,
      currentPrice: currentPrice,
      yesterdayClose: yesterdayClose,
      openPrice: openPrice,
      highPrice: highPrice,
      lowPrice: lowPrice,
      volume: volume,
      amount: amount,
      changePercent: changePercent,
      changeAmount: changeAmount,
      turnoverRate: turnoverRate,
      amplitude: amplitude,
      pe: pe ?? this.pe,
      pb: pb ?? this.pb,
      marketCap: marketCap,
      circulatingMarketCap: circulatingMarketCap,
      volumeRatio: volumeRatio ?? this.volumeRatio,
      time: time,
      market: market,
    );
  }

  /// 格式化价格
  String get priceText => currentPrice.toStringAsFixed(2);
  String get changePercentText => '${changePercent >= 0 ? "+" : ""}${changePercent.toStringAsFixed(2)}%';
  String get changeAmountText => '${changeAmount >= 0 ? "+" : ""}${changeAmount.toStringAsFixed(2)}';

  /// 腾讯行情API (qt.gtimg.cn) — 格式稳定、无需token
  /// 字段顺序(0-based): 1=名称,3=现价,4=昨收,5=今开,6=成交量(手),31=PE,32=涨跌幅%,33=最高,34=最低,
  /// 37=成交额(万),38=换手率%,39=振幅%,44=总市值(亿),45=流通市值(亿)
  factory StockQuote.fromTencent(String raw, String code) {
    // 提取引号内的内容: v_xxx="DATA" → DATA
    final start = raw.indexOf('"');
    final end = raw.lastIndexOf('"');
    final body = (start >= 0 && end > start) ? raw.substring(start + 1, end) : raw;

    final parts = body.split('~');
    if (parts.length < 46) throw Exception('Invalid tencent data');

    final name = parts[1];
    final stockCode = code.isNotEmpty ? code : parts[2];
    final current = double.tryParse(parts[3]) ?? 0;
    final preClose = double.tryParse(parts[4]) ?? 0;
    final open = double.tryParse(parts[5]) ?? 0;
    final volume = (double.tryParse(parts[6]) ?? 0) * 100; // 手→股
    final high = double.tryParse(parts[33]) ?? 0;
    final low = double.tryParse(parts[34]) ?? 0;
    final changePercent = double.tryParse(parts[32]) ?? 0;
    final changeAmount = double.tryParse(parts[31]) ?? 0;
    final amount = double.tryParse(parts[37]) ?? 0; // 万元
    final turnoverRate = double.tryParse(parts[38]) ?? 0;
    final amplitude = double.tryParse(parts[39]) ?? 0;
    final mCap = double.tryParse(parts[44]) ?? 0; // 亿元

    return StockQuote(
      code: stockCode,
      name: name,
      currentPrice: current,
      yesterdayClose: preClose,
      openPrice: open,
      highPrice: high,
      lowPrice: low,
      volume: volume,
      amount: amount,
      changePercent: changePercent,
      changeAmount: changeAmount,
      turnoverRate: turnoverRate,
      amplitude: amplitude,
      pe: 0, // 腾讯API无统一PE字段
      pb: 0,
      marketCap: mCap,
      circulatingMarketCap: 0,
      volumeRatio: 0,
      time: DateTime.now(),
    );
  }

  /// 东方财富个股行情API (push2.eastmoney.com/api/qt/stock/get)
  /// 返回字段为 f43, f44, f45... 等编码格式，价格单位为分
  factory StockQuote.fromEastMoneySingle(Map<String, dynamic> data, String code, String name) {
    // 价格字段单位为分(÷100 → 元)
    final current = (double.tryParse(data['f43']?.toString() ?? '0') ?? 0) / 100;
    final high = (double.tryParse(data['f44']?.toString() ?? '0') ?? 0) / 100;
    final low = (double.tryParse(data['f45']?.toString() ?? '0') ?? 0) / 100;
    final open = (double.tryParse(data['f46']?.toString() ?? '0') ?? 0) / 100;
    final volumeVal = double.tryParse(data['f47']?.toString() ?? '0') ?? 0;
    final amountVal = double.tryParse(data['f48']?.toString() ?? '0') ?? 0;
    final changeAmt = (double.tryParse(data['f49']?.toString() ?? '0') ?? 0) / 100;
    final changePct = (double.tryParse(data['f50']?.toString() ?? '0') ?? 0) / 100;
    final turnover = (double.tryParse(data['f168']?.toString() ?? '0') ?? 0) / 100;
    final amplitude = double.tryParse(data['f152']?.toString() ?? '0') ?? 0;
    final peVal = double.tryParse(data['f52']?.toString() ?? '0') ?? 0;
    final pbVal = (double.tryParse(data['f162']?.toString() ?? '0') ?? 0) / 100;
    final volumeRatio = double.tryParse(data['f53']?.toString() ?? '0')?.toInt() ?? 0;
    // 市值单位为万(÷10000 → 亿)
    final mCap = (double.tryParse(data['f84']?.toString() ?? '0') ?? 0) / 10000;
    final circMCap = (double.tryParse(data['f85']?.toString() ?? '0') ?? 0) / 10000;

    return StockQuote(
      code: code,
      name: name.isEmpty ? (data['f58']?.toString() ?? code) : name,
      currentPrice: current,
      yesterdayClose: (current - changeAmt).abs() > 0.01 ? current - changeAmt : current,
      openPrice: open,
      highPrice: high,
      lowPrice: low,
      volume: volumeVal,
      amount: amountVal / 10000,
      changePercent: changePct,
      changeAmount: changeAmt,
      turnoverRate: turnover,
      amplitude: amplitude,
      pe: peVal,
      pb: pbVal,
      marketCap: mCap,
      circulatingMarketCap: circMCap,
      volumeRatio: volumeRatio,
      time: DateTime.now(),
    );
  }

  /// 东方财富列表行情API (push2.eastmoney.com/api/qt/clist/get)
  /// 返回字段为 f2, f3, f12... 等编码格式
  factory StockQuote.fromEastMoneyList(Map<String, dynamic> data, String code, String name) {
    final current = double.tryParse(data['f2']?.toString() ?? '0') ?? 0;
    final changePct = double.tryParse(data['f3']?.toString() ?? '0') ?? 0;
    final changeAmt = double.tryParse(data['f4']?.toString() ?? '0') ?? 0;
    final volumeVal = double.tryParse(data['f5']?.toString() ?? '0') ?? 0;
    final amountVal = double.tryParse(data['f6']?.toString() ?? '0') ?? 0;
    final high = double.tryParse(data['f15']?.toString() ?? '0') ?? 0;
    final low = double.tryParse(data['f16']?.toString() ?? '0') ?? 0;
    final open = double.tryParse(data['f17']?.toString() ?? '0') ?? 0;
    final preClose = double.tryParse(data['f18']?.toString() ?? '0') ?? 0;
    final mCap = double.tryParse(data['f20']?.toString() ?? '0') ?? 0;
    final circMCap = double.tryParse(data['f21']?.toString() ?? '0') ?? 0;

    return StockQuote(
      code: code,
      name: name,
      currentPrice: current,
      yesterdayClose: preClose,
      openPrice: open,
      highPrice: high,
      lowPrice: low,
      volume: volumeVal,
      amount: amountVal / 10000,
      changePercent: changePct,
      changeAmount: changeAmt,
      turnoverRate: 0,
      amplitude: 0,
      pe: 0,
      pb: 0,
      marketCap: mCap,
      circulatingMarketCap: circMCap,
      volumeRatio: 0,
      time: DateTime.now(),
    );
  }

  factory StockQuote.fromSina(String raw, String code) {
    // 新浪财经API格式: "var hq_str_sh600519="名称,今开,昨收,当前价,最高,最低,..."
    final parts = raw.split(',');
    if (parts.length < 30) throw Exception('Invalid sina data');

    final name = parts[0].split('="').last;
    final open = double.tryParse(parts[1]) ?? 0;
    final preClose = double.tryParse(parts[2]) ?? 0;
    final current = double.tryParse(parts[3]) ?? 0;
    final high = double.tryParse(parts[4]) ?? 0;
    final low = double.tryParse(parts[5]) ?? 0;
    final volumeVal = double.tryParse(parts[8]) ?? 0;
    final amountVal = double.tryParse(parts[9]) ?? 0;

    return StockQuote(
      code: code,
      name: name,
      currentPrice: current,
      yesterdayClose: preClose,
      openPrice: open,
      highPrice: high,
      lowPrice: low,
      volume: volumeVal / 100,
      amount: amountVal / 10000,
      changePercent: preClose > 0 ? ((current - preClose) / preClose * 100) : 0,
      changeAmount: current - preClose,
      turnoverRate: 0,
      amplitude: 0,
      pe: 0,
      pb: 0,
      marketCap: 0,
      circulatingMarketCap: 0,
      volumeRatio: 0,
      time: DateTime.now(),
    );
  }
}
