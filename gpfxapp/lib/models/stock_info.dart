class StockInfo {
  final String code; // 股票代码，如 "600519"
  final String name; // 股票名称，如 "贵州茅台"
  final String market; // 市场: "SH" 上交所, "SZ" 深交所, "BJ" 北交所
  final String? type; // 板块类型

  StockInfo({
    required this.code,
    required this.name,
    required this.market,
    this.type,
  });

  String get fullCode {
    if (market == 'SH') return 'sh$code';
    if (market == 'SZ') return 'sz$code';
    if (market == 'BJ') return 'bj$code';
    return code;
  }

  String get exchange => market == 'SH' ? '上交所' : market == 'SZ' ? '深交所' : '北交所';

  Map<String, dynamic> toJson() => {
        'code': code,
        'name': name,
        'market': market,
        'type': type,
      };

  factory StockInfo.fromJson(Map<String, dynamic> json) => StockInfo(
        code: json['code'] as String,
        name: json['name'] as String,
        market: json['market'] as String,
        type: json['type'] as String?,
      );

  @override
  bool operator ==(Object other) =>
      identical(this, other) || other is StockInfo && code == other.code && market == other.market;

  @override
  int get hashCode => code.hashCode ^ market.hashCode;
}
