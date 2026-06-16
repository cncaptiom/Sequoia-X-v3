import 'analysis_result.dart';

/// 评级快照：记录每天自选股的分析结果，用于一周后效果跟踪
class RatingSnapshot {
  final String stockCode;
  final String stockName;
  final int ratingIndex;
  final double totalScore;
  final double price;
  final bool trendPassed;
  final DateTime time;

  Rating get rating => Rating.values[ratingIndex];

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

  const RatingSnapshot({
    required this.stockCode,
    required this.stockName,
    required this.ratingIndex,
    required this.totalScore,
    required this.price,
    required this.trendPassed,
    required this.time,
  });

  String get dateKey =>
      '${time.year}-${time.month.toString().padLeft(2, '0')}-${time.day.toString().padLeft(2, '0')}';

  Map<String, dynamic> toJson() => {
        'stockCode': stockCode,
        'stockName': stockName,
        'ratingIndex': ratingIndex,
        'totalScore': totalScore,
        'price': price,
        'trendPassed': trendPassed,
        'time': time.toIso8601String(),
      };

  factory RatingSnapshot.fromJson(Map<String, dynamic> json) => RatingSnapshot(
        stockCode: json['stockCode'] as String,
        stockName: json['stockName'] as String,
        ratingIndex: (json['ratingIndex'] as num).toInt(),
        totalScore: (json['totalScore'] as num).toDouble(),
        price: (json['price'] as num).toDouble(),
        trendPassed: json['trendPassed'] as bool,
        time: DateTime.parse(json['time'] as String),
      );
}
