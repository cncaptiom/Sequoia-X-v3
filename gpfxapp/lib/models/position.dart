class Position {
  final String code;
  final String name;
  final String market;
  final double buyPrice;
  final int quantity;
  final DateTime buyDate;

  const Position({
    required this.code,
    required this.name,
    required this.market,
    required this.buyPrice,
    required this.quantity,
    required this.buyDate,
  });

  double get totalCost => buyPrice * quantity;

  double profitLoss(double currentPrice) => (currentPrice - buyPrice) * quantity;

  double profitLossPercent(double currentPrice) =>
      buyPrice > 0 ? ((currentPrice - buyPrice) / buyPrice * 100) : 0;

  Map<String, dynamic> toJson() => {
        'code': code,
        'name': name,
        'market': market,
        'buyPrice': buyPrice,
        'quantity': quantity,
        'buyDate': buyDate.toIso8601String(),
      };

  factory Position.fromJson(Map<String, dynamic> json) => Position(
        code: json['code'] as String,
        name: json['name'] as String,
        market: json['market'] as String,
        buyPrice: (json['buyPrice'] as num).toDouble(),
        quantity: (json['quantity'] as num).toInt(),
        buyDate: DateTime.parse(json['buyDate'] as String),
      );
}
