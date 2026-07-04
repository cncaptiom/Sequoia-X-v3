import 'dart:async';
import 'package:flutter/material.dart';
import '../models/pc_recommendation.dart';
import '../models/strategy_config.dart';
import '../services/cos_api_service.dart';
import '../services/storage_service.dart';

/// PC端 COS 数据 Provider
/// 定时从腾讯云拉取 PC端 Sequoia-X 的计算结果
class PcDataProvider extends ChangeNotifier {
  final CosApiService _cos = CosApiService();
  final StorageService _storage = StorageService();

  /// 可选：关联WatchlistProvider，自动同步PC推荐股票
  dynamic _watchlistProvider;

  PcPortfolioSummary? _summary;
  PcFullRatings? _fullRatings;
  Map<String, PcRatingResult> _ratingsMap = {};
  Map<String, dynamic>? _strategyBacktest;

  bool _isLoading = false;
  String? _error;
  DateTime? _lastFetch;
  // 上次拉取日期，用于判断是否已拉取过

  // Getters
  PcPortfolioSummary? get summary => _summary;
  PcFullRatings? get fullRatings => _fullRatings;
  bool get isLoading => _isLoading;
  String? get error => _error;
  DateTime? get lastFetch => _lastFetch;

  /// 获取指定股票的PC端评级
  PcRatingResult? getRating(String symbol) => _ratingsMap[symbol];

  /// PC端是否可用（有数据）
  bool get isAvailable => _summary != null || _fullRatings != null;

  /// 取精选推荐列表
  List<PcTopPick> get topPicks => _summary?.topPicks ?? [];

  /// 市场状态标签
  String get marketRegimeLabel => _summary?.marketRegime ?? '';

  /// 建议仓位
  double get suggestedExposure => _summary?.suggestedExposure ?? 0;

  /// PC端评级数据是否已加载
  bool get hasRatings => _ratingsMap.isNotEmpty;

  /// 策略收益率回测数据
  Map<String, dynamic>? get strategyBacktest => _strategyBacktest;

  /// 将全量评级数据转为 {symbol: {entry_low, stop_loss, ...}} 格式供告警服务使用
  Map<String, Map<String, dynamic>> buildRecommendationsMap() {
    final map = <String, Map<String, dynamic>>{};
    for (final r in _ratingsMap.values) {
      Map<String, dynamic> rec = {};
      if (r.buyRange.isNotEmpty) {
        final parts = r.buyRange.split(' - ');
        if (parts.length == 2) {
          rec['entry_low'] = double.tryParse(parts[0]) ?? 0;
          rec['entry_high'] = double.tryParse(parts[1]) ?? 0;
        }
      }
      if (r.stopLossPrice != null) rec['stop_loss'] = r.stopLossPrice;
      if (r.takeProfitPrice != null) rec['target_1'] = r.takeProfitPrice;
      map[r.symbol] = rec;
    }
    return map;
  }

  /// 评级分布
  Map<String, int> get ratingSummary => _summary?.ratingSummary ?? {};

  // ── 初始化 ──

  /// 关联WatchlistProvider（自动同步PC推荐股票）
  void linkWatchlistProvider(dynamic provider) {
    _watchlistProvider = provider;
  }

  Future<void> init() async {
    // 从本地缓存读取 COS 配置
    final savedBucket = await _storage.getCosBucket();
    final savedRegion = await _storage.getCosRegion();
    final savedEnabled = await _storage.getCosEnabled();
    if (savedBucket.isNotEmpty) _cos.configure(savedBucket, savedRegion);
    _cos.enabled = savedEnabled;

    // 先加载本地缓存（确保页面立即有数据显示）
    await _loadFromCache();

    // 每次init都尝试拉取最新数据（网络请求在后台进行，不阻塞UI）
    // 原日期检查机制改为：成功拉取后记录日期，但不再阻止拉取
    refresh();
  }

  /// 从Hive本地缓存加载PC数据
  Future<void> _loadFromCache() async {
    try {
      final cachedSummary = await _storage.getPcCachedSummary();
      if (cachedSummary != null && cachedSummary is Map) {
        _summary = PcPortfolioSummary.fromJson(Map<String, dynamic>.from(cachedSummary as Map));
      }
      final cachedRatings = await _storage.getPcCachedRatings();
      if (cachedRatings != null && cachedRatings is Map) {
        _fullRatings = PcFullRatings.fromJson(Map<String, dynamic>.from(cachedRatings as Map));
        _ratingsMap = {};
        for (final r in _fullRatings!.ratings) {
          _ratingsMap[r.symbol] = r;
        }
      }
      if (_summary != null || _fullRatings != null) {
        _lastFetch = DateTime.now();
        _syncAutoRecommend();
      }
    } catch (_) {}
  }

  /// 保存PC数据到Hive本地缓存
  Future<void> _saveToCache() async {
    try {
      // PcPortfolioSummary没有toJson，手动拼装
      if (_summary != null) {
        final s = _summary!;
        await _storage.savePcCachedSummary({
          'date': s.date,
          'generated_at': s.generatedAt,
          'market_regime': s.marketRegime,
          'suggested_exposure': s.suggestedExposure,
          'rating_summary': s.ratingSummary,
          'top_picks': s.topPicks.map((p) => {
            'symbol': p.symbol, 'name': p.name,
            'rating': p.rating, 'rating_label': p.ratingLabel,
            'rating_stars': p.ratingStars,
            'total_score': p.totalScore,
            'current_price': p.currentPrice,
          }).toList(),
        });
      }
      // PcFullRatings也没有toJson，手动拼装
      if (_fullRatings != null) {
        final r = _fullRatings!;
        await _storage.savePcCachedRatings({
          'version': r.version,
          'date': r.date,
          'generated_at': r.generatedAt,
          'strategy_config': r.strategyConfig,
          'market_regime': r.marketRegime,
          'total_scanned': r.totalScanned,
          'ratings': r.ratings.map((rr) => {
            'symbol': rr.symbol, 'name': rr.name,
            'rating': rr.rating, 'rating_label': rr.ratingLabel,
            'rating_stars': rr.ratingStars,
            'total_score': rr.totalScore,
            'trend_passed': rr.trendPassed,
            'trend_description': rr.trendDescription,
            'strategy_scores': rr.strategyScores,
            'category_scores': rr.categoryScores,
            'signals': rr.signals,
            'risk_reward_ratio': rr.riskRewardRatio,
            'stop_loss_price': rr.stopLossPrice,
            'take_profit_price': rr.takeProfitPrice,
            'buy_range': rr.buyRange,
            'current_price': rr.currentPrice,
          }).toList(),
        });
      }
    } catch (_) {}
  }

  // ── 刷新 ──

  Future<void> refresh() async {
    _isLoading = true;
    notifyListeners();

    try {
      // 拉取摘要（首页展示用）— 仅在获取到有效数据时更新
      final newSummary = await _cos.fetchPortfolioSummary();
      if (newSummary != null) {
        _summary = newSummary;
      }

      // 拉取全量评级
      final newRatings = await _cos.fetchFullRatings();
      if (newRatings != null) {
        _fullRatings = newRatings;
        // 构建快速查询索引
        _ratingsMap = {};
        for (final r in _fullRatings!.ratings) {
          _ratingsMap[r.symbol] = r;
        }
      }

      // 拉取策略收益率回测数据
      final bt = await _cos.fetchStrategyBacktest();
      if (bt != null) _strategyBacktest = bt;

      // 自动同步PC推荐股票到WatchlistProvider
      _syncAutoRecommend();

      // 保存到Hive本地缓存（确保Provider重建后仍可恢复）
      _saveToCache();

      _error = null;
      _lastFetch = DateTime.now();

      // 如果拉到了策略配置且用户未自定义，则同步
      if (_fullRatings != null && _fullRatings!.strategyConfig.isNotEmpty) {
        final config = await _storage.getStrategyConfig();
        // 只有用户未自定义时才同步PC端配置
        if (!await _storage.hasCustomizedStrategy()) {
          final pcConfig = StrategyConfig.fromJson({
            ..._fullRatings!.strategyConfig,
            // 保留用户的通知偏好
            'notificationEnabled': config.notificationEnabled,
            'notifyGoldenCross': config.notifyGoldenCross,
            'notifyOversold': config.notifyOversold,
            'notifyBreakout': config.notifyBreakout,
            'notifyBuyZone': config.notifyBuyZone,
            'notifyStopLoss': config.notifyStopLoss,
            'notifyTakeProfit': config.notifyTakeProfit,
          });
          await _storage.saveStrategyConfig(pcConfig);
        }
      }
    } catch (e) {
      _error = e.toString();
    }

    _isLoading = false;
    notifyListeners();
  }

  // ── COS 配置管理 ──

  Future<void> updateCosConfig({
    required String bucket,
    required String region,
    required bool enabled,
  }) async {
    _cos.configure(bucket, region);
    _cos.enabled = enabled;
    await _storage.setCosBucket(bucket);
    await _storage.setCosRegion(region);
    await _storage.setCosEnabled(enabled);
    await refresh();
  }

  /// 自动同步PC推荐股票到WatchlistProvider展示
  void _syncAutoRecommend() {
    if (_watchlistProvider == null) return;
    try {
      final topPicks = _summary?.topPicks ?? [];
      if (topPicks.isNotEmpty) {
        final pickData = topPicks.map((p) => {
          'symbol': p.symbol,
          'name': p.name,
          'rating': p.rating,
          'ratingLabel': p.ratingLabel,
          'totalScore': p.totalScore,
          'currentPrice': p.currentPrice,
        }).toList();
        _watchlistProvider.updatePcRecommendedStocks(pickData);
      }
    } catch (_) {}
  }

  @override
  void dispose() {
    super.dispose();
  }
}
