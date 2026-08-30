import 'package:hive_flutter/hive_flutter.dart';
import '../models/stock_info.dart';
import '../models/strategy_config.dart';
import '../models/position.dart';
import '../models/rating_snapshot.dart';

class StorageService {
  static const String _watchlistBox = 'watchlist';
  static const String _strategyBox = 'strategy';
  static const String _settingsBox = 'settings';
  static const String _ratingBox = 'rating_history';

  static Future<void> init() async {
    await Hive.initFlutter();
    await Hive.openBox(_watchlistBox);
    await Hive.openBox(_strategyBox);
    await Hive.openBox(_settingsBox);
    await Hive.openBox(_ratingBox);
  }

  // ==================== 自选股管理 ====================

  Future<List<StockInfo>> getWatchlist() async {
    final box = Hive.box(_watchlistBox);
    final data = box.get('stocks', defaultValue: <Map>[]) as List;
    return data.map((e) => StockInfo.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  Future<void> addToWatchlist(StockInfo stock) async {
    final list = await getWatchlist();
    if (list.any((s) => s.code == stock.code && s.market == stock.market)) return;
    list.add(stock);
    await Hive.box(_watchlistBox).put('stocks', list.map((e) => e.toJson()).toList());
  }

  Future<void> removeFromWatchlist(StockInfo stock) async {
    final list = await getWatchlist();
    list.removeWhere((s) => s.code == stock.code && s.market == stock.market);
    await Hive.box(_watchlistBox).put('stocks', list.map((e) => e.toJson()).toList());
  }

  Future<bool> isInWatchlist(String code, String market) async {
    final list = await getWatchlist();
    return list.any((s) => s.code == code && s.market == market);
  }

  // ==================== 策略配置 ====================

  Future<StrategyConfig> getStrategyConfig() async {
    final box = Hive.box(_strategyBox);
    final data = box.get('config') as Map?;
    if (data == null) return const StrategyConfig();
    return StrategyConfig.fromJson(Map<String, dynamic>.from(data));
  }

  Future<void> saveStrategyConfig(StrategyConfig config) async {
    await Hive.box(_strategyBox).put('config', config.toJson());
  }

  // ==================== 设置 ====================

  Future<bool> getNotificationEnabled() async {
    return Hive.box(_settingsBox).get('notificationEnabled', defaultValue: true) as bool;
  }

  Future<void> setNotificationEnabled(bool enabled) async {
    await Hive.box(_settingsBox).put('notificationEnabled', enabled);
  }

  Future<int> getRefreshInterval() async {
    return Hive.box(_settingsBox).get('refreshInterval', defaultValue: 30) as int;
  }

  Future<void> setRefreshInterval(int seconds) async {
    await Hive.box(_settingsBox).put('refreshInterval', seconds);
  }

  // ==================== 已买入（持仓） ====================

  Future<Set<String>> getHeldStocks() async {
    final box = Hive.box(_settingsBox);
    // 先读新key
    var list = box.get('heldStocks') as List?;
    if (list == null) {
      // 兼容旧版本 focusedStocks → 迁移到 heldStocks
      list = box.get('focusedStocks', defaultValue: <String>[]) as List;
      if (list.isNotEmpty) {
        await box.put('heldStocks', list);
      }
      try { await box.delete('focusedStocks'); } catch (_) {}
    }
    return Set<String>.from(list.map((e) => e.toString()));
  }

  Future<void> setHeldStocks(Set<String> codes) async {
    await Hive.box(_settingsBox).put('heldStocks', codes.toList());
  }

  // ==================== 持仓管理 ====================

  Future<List<Position>> getPositions() async {
    final box = Hive.box(_settingsBox);
    final data = box.get('positions', defaultValue: <Map>[]) as List;
    return data.map((e) => Position.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  Future<void> savePositions(List<Position> positions) async {
    await Hive.box(_settingsBox).put('positions', positions.map((e) => e.toJson()).toList());
  }

  // ==================== 批量导入自选股 ====================

  Future<int> importStocks(List<StockInfo> stocks) async {
    final list = await getWatchlist();
    int added = 0;
    for (final stock in stocks) {
      if (!list.any((s) => s.code == stock.code && s.market == stock.market)) {
        list.add(stock);
        added++;
      }
    }
    await Hive.box(_watchlistBox).put('stocks', list.map((e) => e.toJson()).toList());
    return added;
  }

  // ==================== 评级效果跟踪 ====================

  static String todayDateStr() {
    final now = DateTime.now();
    return '${now.year}-${now.month.toString().padLeft(2, '0')}-${now.day.toString().padLeft(2, '0')}';
  }

  /// 保存今日评级快照（覆盖当天已有数据）
  Future<void> saveTodaySnapshots(List<RatingSnapshot> snapshots) async {
    final key = todayDateStr();
    await Hive.box(_ratingBox).put(key, snapshots.map((s) => s.toJson()).toList());
  }

  /// 获取今日评级快照
  Future<List<RatingSnapshot>> getTodaySnapshots() async {
    final key = todayDateStr();
    final box = Hive.box(_ratingBox);
    final data = box.get(key) as List?;
    if (data == null) return [];
    return data.map((e) => RatingSnapshot.fromJson(Map<String, dynamic>.from(e))).toList();
  }

  /// 获取全部历史快照（按日期分组，最新在前）
  Future<Map<String, List<RatingSnapshot>>> getAllSnapshotsGrouped() async {
    final box = Hive.box(_ratingBox);
    final result = <String, List<RatingSnapshot>>{};
    for (final key in box.keys) {
      final data = box.get(key) as List?;
      if (data != null && data.isNotEmpty) {
        result[key.toString()] =
            data.map((e) => RatingSnapshot.fromJson(Map<String, dynamic>.from(e))).toList();
      }
    }
    // 按日期降序
    final sorted = result.entries.toList()..sort((a, b) => b.key.compareTo(a.key));
    return Map.fromEntries(sorted);
  }

  /// 获取所有已记录的日期列表（降序）
  Future<List<String>> getSnapshotDates() async {
    final box = Hive.box(_ratingBox);
    final dates = box.keys.map((e) => e.toString()).toList();
    dates.sort((a, b) => b.compareTo(a));
    return dates;
  }
  // ==================== COS cloud sync config ====================

  Future<String> getCosBucket() async {
    return Hive.box(_settingsBox).get('cosBucket', defaultValue: 'sequoia-recommend-1442452595') as String;
  }

  Future<void> setCosBucket(String bucket) async {
    await Hive.box(_settingsBox).put('cosBucket', bucket);
  }

  Future<String> getCosRegion() async {
    return Hive.box(_settingsBox).get('cosRegion', defaultValue: 'ap-beijing') as String;
  }

  Future<void> setCosRegion(String region) async {
    await Hive.box(_settingsBox).put('cosRegion', region);
  }

  Future<String> getCosSecretId() async {
    return Hive.box(_settingsBox).get('cosSecretId', defaultValue: '') as String;
  }

  Future<void> setCosSecretId(String id) async {
    await Hive.box(_settingsBox).put('cosSecretId', id);
  }

  Future<String> getCosSecretKey() async {
    return Hive.box(_settingsBox).get('cosSecretKey', defaultValue: '') as String;
  }

  Future<void> setCosSecretKey(String key) async {
    await Hive.box(_settingsBox).put('cosSecretKey', key);
  }

  Future<bool> getCosEnabled() async {
    return Hive.box(_settingsBox).get('cosEnabled', defaultValue: true) as bool;
  }

  Future<void> setCosEnabled(bool enabled) async {
    await Hive.box(_settingsBox).put('cosEnabled', enabled);
  }

  Future<bool> hasCustomizedStrategy() async {
    return Hive.box(_settingsBox).get('customizedStrategy', defaultValue: false) as bool;
  }

  Future<void> setCustomizedStrategy(bool customized) async {
    await Hive.box(_settingsBox).put('customizedStrategy', customized);
  }


  // ==================== PC数据拉取日期记录 ====================

  Future<String> getPcDataLastFetchDate() async {
    return Hive.box(_settingsBox).get('pcDataLastFetch', defaultValue: '') as String;
  }

  Future<void> setPcDataLastFetchDate(String date) async {
    await Hive.box(_settingsBox).put('pcDataLastFetch', date);
  }

  // ==================== PC数据本地缓存（Provider重建后恢复用） ====================

  Future<dynamic> getPcCachedSummary() async {
    final data = Hive.box(_settingsBox).get('pcCachedSummary');
    if (data == null) return null;
    return data;
  }

  Future<void> savePcCachedSummary(dynamic summary) async {
    await Hive.box(_settingsBox).put('pcCachedSummary', summary);
  }

  Future<dynamic> getPcCachedRatings() async {
    final data = Hive.box(_settingsBox).get('pcCachedRatings');
    if (data == null) return null;
    return data;
  }

  Future<void> savePcCachedRatings(dynamic ratings) async {
    await Hive.box(_settingsBox).put('pcCachedRatings', ratings);
  }

  Future<dynamic> getPcCachedSignals() async {
    final data = Hive.box(_settingsBox).get('pcCachedSignals');
    if (data == null) return null;
    return data;
  }

  Future<void> savePcCachedSignals(dynamic signals) async {
    await Hive.box(_settingsBox).put('pcCachedSignals', signals);
  }

  Future<String?> getPcCacheTimestamp() async {
    return Hive.box(_settingsBox).get('pcCacheTimestamp') as String?;
  }

  Future<void> savePcCacheTimestamp(String iso) async {
    await Hive.box(_settingsBox).put('pcCacheTimestamp', iso);
  }

}
