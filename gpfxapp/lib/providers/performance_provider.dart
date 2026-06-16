import 'package:flutter/material.dart';
import '../models/rating_snapshot.dart';
import '../models/stock_quote.dart';
import '../models/analysis_result.dart';
import '../services/storage_service.dart';

class PerformanceProvider extends ChangeNotifier {
  final StorageService _storage = StorageService();

  Map<String, List<RatingSnapshot>> _snapshotsByDate = {};
  bool _isLoading = false;

  Map<String, List<RatingSnapshot>> get snapshotsByDate => _snapshotsByDate;
  bool get isLoading => _isLoading;
  bool get hasData => _snapshotsByDate.isNotEmpty;

  /// 加载所有历史快照
  Future<void> loadSnapshots() async {
    _isLoading = true;
    notifyListeners();
    _snapshotsByDate = await _storage.getAllSnapshotsGrouped();
    _isLoading = false;
    notifyListeners();
  }

  /// 计算截至当前的效果统计
  /// [currentQuotes] 当前自选股行情，用于比对价格
  PerformanceStats computeStats(Map<String, StockQuote> currentQuotes) {
    int buyCorrect = 0, buyTotal = 0;
    int avoidCorrect = 0, avoidTotal = 0;
    int watchTotal = 0, attentionTotal = 0;
    final today = StorageService.todayDateStr();

    for (final entry in _snapshotsByDate.entries) {
      if (entry.key == today) continue; // 今天的数据不纳入效果统计

      for (final snap in entry.value) {
        final quote = currentQuotes[snap.stockCode];
        if (quote == null) continue;

        final currentPrice = quote.currentPrice;

        if (snap.rating == Rating.strongBuy || snap.rating == Rating.buy) {
          buyTotal++;
          if (currentPrice >= snap.price) buyCorrect++;
        } else if (snap.rating == Rating.avoid) {
          avoidTotal++;
          if (currentPrice <= snap.price) avoidCorrect++;
        } else if (snap.rating == Rating.watch) {
          watchTotal++;
        } else if (snap.rating == Rating.attention) {
          attentionTotal++;
        }
      }
    }

    return PerformanceStats(
      buyCorrect: buyCorrect,
      buyTotal: buyTotal,
      avoidCorrect: avoidCorrect,
      avoidTotal: avoidTotal,
      watchTotal: watchTotal,
      attentionTotal: attentionTotal,
    );
  }

  /// 获取指定日期的快照列表
  List<RatingSnapshot>? getSnapshotsByDate(String date) => _snapshotsByDate[date];
}

class PerformanceStats {
  final int buyCorrect;
  final int buyTotal;
  final int avoidCorrect;
  final int avoidTotal;
  final int watchTotal;
  final int attentionTotal;

  const PerformanceStats({
    required this.buyCorrect,
    required this.buyTotal,
    required this.avoidCorrect,
    required this.avoidTotal,
    required this.watchTotal,
    required this.attentionTotal,
  });

  double get buyAccuracy => buyTotal > 0 ? buyCorrect / buyTotal * 100 : 0;
  double get avoidAccuracy => avoidTotal > 0 ? avoidCorrect / avoidTotal * 100 : 0;
  int get totalEvaluated => buyTotal + avoidTotal;
  int get totalCorrect => buyCorrect + avoidCorrect;
  double get overallAccuracy => totalEvaluated > 0 ? totalCorrect / totalEvaluated * 100 : 0;

  /// 是否有效评估（至少有一条买入或回避记录）
  bool get isValid => totalEvaluated > 0;
}
