import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import '../models/stock_info.dart';
import '../models/stock_quote.dart';
import '../models/analysis_result.dart';
import '../models/strategy_config.dart';
import '../models/rating_snapshot.dart';
import '../services/stock_api_service.dart';
import '../services/analysis_engine.dart';
import '../services/storage_service.dart';
import '../services/notification_service.dart';
import '../services/pc_alert_service.dart';
import '../services/cos_api_service.dart';

class WatchlistProvider extends ChangeNotifier {
  final StorageService _storage = StorageService();
  final StockApiService _api = StockApiService();
  final CosApiService _cos = CosApiService();

  List<StockInfo> _watchlist = [];
  final Map<String, StockQuote> _quotes = {};
  final Map<String, AnalysisResult> _analysisResults = {};
  bool _isLoading = false;

  /// PC端精选推荐缓存（首页展示，自动同步）
  List<StockInfo> _pcRecommendedStocks = [];
  final Map<String, AnalysisResult> _pcAnalysisOverrides = {};

  // 已买入（持仓） — 定时刷新+通知
  Set<String> _heldStocks = {};
  final Map<String, Timer> _holdTimers = {};

  // 通知防重复标记: { code: {alertType1, alertType2} }
  final Map<String, Set<String>> _notifiedAlerts = {};

  // PC端价格告警服务
  final PcAlertService _pcAlertService = PcAlertService();

  // PC端推荐数据索引（由外部设置，来自 PcDataProvider）
  Map<String, Map<String, dynamic>> _pcRecommendations = {};

  /// 快照保存节流时间
  DateTime? _lastSnapshotSave;

  /// 通知类型常量
  static const String _alertBuyZone = 'buy_zone';
  static const String _alertTakeProfit = 'take_profit';
  static const String _alertStopLoss = 'stop_loss';

  List<StockInfo> get watchlist => _watchlist;
  Map<String, StockQuote> get quotes => _quotes;
  Map<String, AnalysisResult> get analysisResults => _analysisResults;
  bool get isLoading => _isLoading;
  Set<String> get heldStocks => _heldStocks;

  /// PC端推荐股票列表
  List<StockInfo> get pcRecommendedStocks => _pcRecommendedStocks;

  /// 获取股票的综合分析结果（优先返回PC端覆盖数据）
  AnalysisResult? getEffectiveAnalysis(String code) {
    return _analysisResults[code];
  }

  /// 加载自选股（本地优先，本地为空时尝试从云端恢复）
  Future<void> loadWatchlist() async {
    // 加载 COS 凭证（用于自选股云备份）
    await _loadCosAuth();

    _watchlist = await _storage.getWatchlist();
    _heldStocks = await _storage.getHeldStocks();

    // 本地无数据时尝试从 COS 恢复
    if (_watchlist.isEmpty) {
      await _restoreFromCloud();
    }

    notifyListeners();

    // 恢复已买入股票的定时刷新
    for (final stock in _watchlist) {
      if (_heldStocks.contains(stock.code)) {
        _startHoldTimer(stock);
      }
    }

    if (_watchlist.isNotEmpty) {
      await refreshAll();
    }
  }

  /// 从 Hive 加载 COS 凭证并配置到上传服务
  Future<void> _loadCosAuth() async {
    final secretId = await _storage.getCosSecretId();
    final secretKey = await _storage.getCosSecretKey();
    if (secretId.isNotEmpty && secretKey.isNotEmpty) {
      _cos.configureAuth(secretId, secretKey);
    }
  }

  /// 重新加载 COS 凭证（在设置页面修改凭证后调用）
  Future<void> reloadCosAuth() async {
    await _loadCosAuth();
  }

  /// 从 COS 恢复自选股（自动恢复 + 设置页手动触发），返回恢复数量。
  /// 重试 3 次并记录日志：重装后首次启动网络可能未就绪，避免一次性失败静默丢数据。
  Future<int> _restoreFromCloud() async {
    for (var attempt = 0; attempt < 3; attempt++) {
      try {
        final backup = await _cos.fetchWatchlistBackup();
        if (backup == null) {
          debugPrint('云端无自选股备份');
          return 0;
        }
        final stocks = backup['stocks'] as List?;
        if (stocks == null || stocks.isEmpty) {
          debugPrint('云端自选股备份为空');
          return 0;
        }
        int restored = 0;
        for (final s in stocks) {
          final stock = StockInfo(
            code: s['code'] as String? ?? '',
            name: s['name'] as String? ?? '',
            market: s['market'] as String? ?? 'SH',
          );
          if (stock.code.isNotEmpty &&
              !_watchlist.any((w) => w.code == stock.code)) {
            await _storage.addToWatchlist(stock);
            _watchlist.add(stock);
            restored++;
          }
        }
        if (restored > 0) notifyListeners();
        debugPrint('已从云端恢复 $restored 只自选股');
        return restored;
      } catch (e) {
        debugPrint('从云端恢复自选股失败(第${attempt + 1}/3次): $e');
        if (attempt < 2) {
          await Future.delayed(Duration(seconds: 2 * (attempt + 1)));
        }
      }
    }
    return 0;
  }

  /// 供设置页手动触发云端恢复自选股
  Future<int> restoreFromCloud() => _restoreFromCloud();

  /// 备份自选股到 COS
  Future<void> _backupToCloud() async {
    if (_watchlist.isEmpty) return;
    // 无 COS 凭证时无法签名 PUT，备份必然失败，直接提示（避免静默丢数据）
    if (!_cos.hasAuth) {
      debugPrint('自选股云备份未生效：未配置 COS SecretId/SecretKey');
      return;
    }
    try {
      final ok = await _cos.uploadWatchlistBackup({
        'version': 1,
        'stocks': _watchlist.map((s) {
          return {'code': s.code, 'name': s.name, 'market': s.market};
        }).toList(),
      });
      if (!ok) debugPrint('自选股云备份上传失败');
    } catch (e) {
      debugPrint('自选股云备份异常: $e');
    }
  }

  /// 添加自选
  Future<void> add(StockInfo stock) async {
    await _storage.addToWatchlist(stock);
    _watchlist.add(stock);
    notifyListeners();
    _backupToCloud();

    // 立即拉取行情和分析
    try {
      final quote = await _api.getQuote(stock.code, stock.market);
      _quotes[stock.code] = quote;
      _loadAnalysisForStock(quote);
    } catch (_) {}
    notifyListeners();
  }

  /// 删除自选
  Future<void> remove(StockInfo stock) async {
    await _storage.removeFromWatchlist(stock);
    _watchlist
        .removeWhere((s) => s.code == stock.code && s.market == stock.market);
    _quotes.remove(stock.code);
    _analysisResults.remove(stock.code);
    _notifiedAlerts.remove(stock.code);
    _pcAlertService.clearCooldown(stock.code);
    _unholdStock(stock.code);
    notifyListeners();
    _backupToCloud();
  }

  /// 检查是否已自选
  bool isWatch(String code) {
    return _watchlist.any((s) => s.code == code);
  }

  /// 使用新策略配置重新分析所有已加载的自选股
  Future<void> reanalyzeAll(StrategyConfig config) async {
    if (_quotes.isEmpty) return;
    final engine = AnalysisEngine(config: config);
    final stocksToAnalyze =
        _watchlist.where((s) => _quotes.containsKey(s.code)).toList();
    if (stocksToAnalyze.isEmpty) return;

    _analysisResults.clear();
    notifyListeners();

    for (var i = 0; i < stocksToAnalyze.length; i += 5) {
      final batch = stocksToAnalyze.skip(i).take(5).toList();
      final futures = batch.map((stock) async {
        try {
          final kline =
              await _api.getKLineData(stock.code, stock.market, count: 60);
          if (kline.length >= 20 && _quotes.containsKey(stock.code)) {
            return MapEntry(stock.code,
                engine.analyze(quote: _quotes[stock.code]!, klineData: kline));
          }
        } catch (_) {}
        return null;
      }).toList();

      final results = await Future.wait(futures);
      for (final r in results) {
        if (r != null) _analysisResults[r.key] = r.value;
      }
      notifyListeners();
    }
  }

  /// 刷新所有自选股行情
  Future<void> refreshAll() async {
    if (_watchlist.isEmpty) return;
    _isLoading = true;
    notifyListeners();

    try {
      final quotes = await _api.getQuotes(_watchlist);
      for (final q in quotes) {
        _quotes[q.code] = q;
        if (!_analysisResults.containsKey(q.code)) {
          _loadAnalysisForStock(q);
        }
      }
    } catch (_) {}

    _isLoading = false;
    notifyListeners();

    // 保存今日评级快照（用于效果跟踪）
    _archiveTodaySnapshot();
  }

  /// 将当前自选股分析结果保存为今日快照（覆盖当天已有数据）
  /// 节流：每分钟最多保存一次
  Future<void> _archiveTodaySnapshot() async {
    if (_watchlist.isEmpty || _analysisResults.isEmpty) return;
    if (_lastSnapshotSave != null &&
        DateTime.now().difference(_lastSnapshotSave!).inSeconds < 60) return;
    _lastSnapshotSave = DateTime.now();
    final snapshots = <RatingSnapshot>[];
    for (final stock in _watchlist) {
      final result = _analysisResults[stock.code];
      final quote = _quotes[stock.code];
      if (result == null || quote == null) continue;
      snapshots.add(RatingSnapshot(
        stockCode: stock.code,
        stockName: stock.name,
        ratingIndex: result.rating.index,
        totalScore: result.totalScore,
        price: quote.currentPrice,
        trendPassed: result.trendPassed,
        time: DateTime.now(),
      ));
    }
    if (snapshots.isNotEmpty) {
      await _storage.saveTodaySnapshots(snapshots);
    }
  }

  // ==================== 已买入（持仓 + 自动刷新 + 通知） ====================

  /// 设置PC端推荐数据索引（由 PcDataProvider 调用）
  void setPcRecommendations(Map<String, Map<String, dynamic>> recs) {
    _pcRecommendations = recs;
  }

  /// 更新PC端推荐股票列表（由PcDataProvider调用）
  /// 自动将PC端评级为strong_buy/buy的股票添加到推荐列表
  void updatePcRecommendedStocks(List<Map<String, dynamic>> topPicks) {
    final stocks = <StockInfo>[];
    for (final pick in topPicks) {
      final code = pick['symbol'] as String? ?? '';
      final name = pick['name'] as String? ?? '';
      if (code.isEmpty) continue;
      String market;
      if (code.startsWith('6') || code.startsWith('9')) {
        market = 'SH';
      } else if (code.startsWith('0') || code.startsWith('3')) {
        market = 'SZ';
      } else if (code.startsWith('8') || code.startsWith('4')) {
        market = 'BJ';
      } else {
        market = 'SH';
      }
      stocks.add(StockInfo(code: code, name: name, market: market));
    }
    _pcRecommendedStocks = stocks;
    notifyListeners();
  }

  /// 获取PC端推荐数据
  Map<String, dynamic>? getPcRecommendation(String code) {
    return _pcRecommendations[code];
  }

  /// 切换已买入状态
  Future<void> toggleHold(StockInfo stock) async {
    if (_heldStocks.contains(stock.code)) {
      _unholdStock(stock.code);
    } else {
      _heldStocks.add(stock.code);
      _startHoldTimer(stock);
      // 立即刷新一次
      await _refreshSingleStock(stock);
    }
    await _storage.setHeldStocks(_heldStocks);
    notifyListeners();
  }

  /// 检查股票是否已买入
  bool isHeld(String code) => _heldStocks.contains(code);

  /// 从 JSON 批量导入自选股（来自 Sequoia-X 评级结果）
  /// json 格式: [{"code":"600519","name":"贵州茅台","rating":"buy",...}]
  Future<int> importFromJsonString(String jsonString) async {
    try {
      // 解析 JSON
      final decoded = const JsonDecoder().convert(jsonString);
      if (decoded is! List) return -1;
      final List<dynamic> items = decoded;
      final stocks = <StockInfo>[];
      for (final item in items) {
        final code = item['code'] as String;
        final name = item['name'] as String;
        // 根据代码前缀判断市场
        String market;
        if (code.startsWith('6') || code.startsWith('9')) {
          market = 'SH';
        } else if (code.startsWith('4') || code.startsWith('8')) {
          market = 'BJ';
        } else {
          market = 'SZ';
        }
        stocks.add(StockInfo(code: code, name: name, market: market));
      }

      if (stocks.isEmpty) return 0;

      final added = await _storage.importStocks(stocks);
      _watchlist = await _storage.getWatchlist();
      notifyListeners();

      // 刷新新加入股票的行情
      for (final stock in stocks.take(10)) {
        try {
          final quote = await _api.getQuote(stock.code, stock.market);
          _quotes[stock.code] = quote;
        } catch (_) {}
      }
      notifyListeners();
      return added;
    } catch (e) {
      return -1;
    }
  }

  /// 为已买入股票启动定时刷新（每15秒）
  void _startHoldTimer(StockInfo stock) {
    _holdTimers[stock.code]?.cancel();
    _holdTimers[stock.code] =
        Timer.periodic(const Duration(seconds: 15), (_) async {
      await _refreshSingleStock(stock);
    });
  }

  /// 取消买入标记
  void _unholdStock(String code) {
    _heldStocks.remove(code);
    _holdTimers[code]?.cancel();
    _holdTimers.remove(code);
    _notifiedAlerts.remove(code);
  }

  /// 刷新单支股票行情+分析+通知（非交易时间跳过）
  Future<void> _refreshSingleStock(StockInfo stock) async {
    if (!_isMarketOpen()) return;
    try {
      final quote = await _api.getQuote(stock.code, stock.market);
      _quotes[stock.code] = quote;
      notifyListeners();

      // PC端价格区间告警检查
      try {
        final config = await _storage.getStrategyConfig();
        await _pcAlertService.checkPriceAlerts(
          code: stock.code,
          name: stock.name,
          quote: quote,
          config: config,
          pcRecommendation: _pcRecommendations[stock.code],
          isHeld: _heldStocks.contains(stock.code),
        );
      } catch (_) {}

      // 无论PC端是否有覆盖，都运行本地分析引擎保持分析结果新鲜
      // 这样 StockCard 展示的分析数据（评级、信号等）始终是最新的
      try {
        final klineData =
            await _api.getKLineData(stock.code, stock.market, count: 60);
        if (klineData.length >= 20) {
          final config = await _storage.getStrategyConfig();
          final engine = AnalysisEngine(config: config);
          final result = engine.analyze(quote: quote, klineData: klineData);
          _analysisResults[stock.code] = result;
        }
      } catch (_) {}

      // 当PC端有该股票的完整评级数据时，同步PC评级到分析结果
      if (_pcRecommendations.containsKey(stock.code)) {
        final pcRec = _pcRecommendations[stock.code]!;
        if (_heldStocks.contains(stock.code)) {
          _checkPCPriceAlert(stock.name, stock.code, quote.currentPrice, pcRec);
        }
        _archiveTodaySnapshot();
        notifyListeners();
        return;
      }

      // 无PC数据时，使用分析引擎结果做信号通知
      if (_analysisResults.containsKey(stock.code)) {
        final result = _analysisResults[stock.code]!;
        final config = await _storage.getStrategyConfig();
        // 发送信号通知（仅已买入股票）
        _checkNotification(stock.name, stock.code, result, config);
        // 发送价格触达通知（已买入：止损止盈；不发送买入区间）
        _checkPriceAlert(
            stock.name, stock.code, quote.currentPrice, result, config);
      }
      _archiveTodaySnapshot();
      notifyListeners();
    } catch (_) {}
  }

  /// PC端数据价格触达检查（替代本地引擎的止损止盈检查）
  void _checkPCPriceAlert(
      String name, String code, double price, Map<String, dynamic> pcRec) {
    if (!_heldStocks.contains(code)) return;
    final alerts = _notifiedAlerts.putIfAbsent(code, () => {});

    final stopLoss = (pcRec['stop_loss'] as num?)?.toDouble();
    final target1 = (pcRec['target_1'] as num?)?.toDouble();

    if (stopLoss != null && price <= stopLoss) {
      if (!alerts.contains(_alertStopLoss)) {
        alerts.add(_alertStopLoss);
        NotificationService().sendSignalNotification(
          stockName: name,
          stockCode: code,
          signalType: 'PC端止损触达',
          message: '现价 $price 触及PC端止损位 $stopLoss，建议执行止损',
        );
      }
    } else {
      alerts.remove(_alertStopLoss);
    }

    if (target1 != null && price >= target1) {
      if (!alerts.contains(_alertTakeProfit)) {
        alerts.add(_alertTakeProfit);
        NotificationService().sendSignalNotification(
          stockName: name,
          stockCode: code,
          signalType: 'PC端止盈触达',
          message: '现价 $price 达到PC端目标价 $target1，建议执行止盈',
        );
      }
    } else {
      alerts.remove(_alertTakeProfit);
    }
  }

  /// 为股票加载分析
  Future<void> _loadAnalysisForStock(StockQuote quote) async {
    try {
      final stock = _watchlist.firstWhere(
        (s) => s.code == quote.code,
        orElse: () =>
            StockInfo(code: quote.code, name: quote.name, market: 'SH'),
      );

      final klineData =
          await _api.getKLineData(stock.code, stock.market, count: 60);
      if (klineData.length >= 20) {
        final config = await _storage.getStrategyConfig();
        final engine = AnalysisEngine(config: config);
        _analysisResults[quote.code] =
            engine.analyze(quote: quote, klineData: klineData);
      }
    } catch (_) {}
  }

  /// 检查并发送信号通知（仅已买入股票）
  void _checkNotification(
      String name, String code, AnalysisResult result, StrategyConfig config) {
    if (!_heldStocks.contains(code)) return;
    if (!config.notificationEnabled) return;

    if (result.rating == Rating.strongBuy || result.rating == Rating.buy) {
      if (!config.notifyGoldenCross) return;
      NotificationService().sendSignalNotification(
        stockName: name,
        stockCode: code,
        signalType: '买入信号',
        message: '综合评级: ${result.ratingStars} ${result.ratingLabel}，建议关注',
      );
    } else if (result.rating == Rating.avoid) {
      if (!config.notifyOversold) return;
      NotificationService().sendSignalNotification(
        stockName: name,
        stockCode: code,
        signalType: '风险提示',
        message: '综合评级: ${result.ratingStars} ${result.ratingLabel}，注意风险',
      );
    }
  }

  /// 检查价格触达通知（含防重复）
  /// 已买入股票：触发止损、止盈通知；不发送买入区间通知
  void _checkPriceAlert(String name, String code, double price,
      AnalysisResult result, StrategyConfig config) {
    if (!_heldStocks.contains(code)) return;
    if (!config.notificationEnabled) return;
    if (result.riskRewardRatio <= 0) return;

    final alerts = _notifiedAlerts.putIfAbsent(code, () => {});
    final stopLoss = result.stopLossPrice;
    final takeProfit = result.takeProfitPrice;

    // 触及止损
    if (stopLoss != null && price <= stopLoss) {
      if (!alerts.contains(_alertStopLoss)) {
        alerts.add(_alertStopLoss);
        if (config.notifyStopLoss) {
          NotificationService().sendSignalNotification(
            stockName: name,
            stockCode: code,
            signalType: '止损触达',
            message: '现价 $price 触及止损位 $stopLoss，建议执行止损',
          );
        }
      }
    } else {
      alerts.remove(_alertStopLoss);
    }

    // 触及止盈
    if (takeProfit != null && price >= takeProfit) {
      if (!alerts.contains(_alertTakeProfit)) {
        alerts.add(_alertTakeProfit);
        if (config.notifyTakeProfit) {
          NotificationService().sendSignalNotification(
            stockName: name,
            stockCode: code,
            signalType: '止盈触达',
            message: '现价 $price 触及止盈位 $takeProfit，建议执行止盈',
          );
        }
      }
    } else {
      alerts.remove(_alertTakeProfit);
    }

    // 已买入股票不发送买入区间通知
  }

  /// A股交易时间判断（周一至周五 9:30-11:30, 13:00-15:00）
  bool _isMarketOpen() {
    final now = DateTime.now();
    if (now.weekday == DateTime.saturday || now.weekday == DateTime.sunday)
      return false;
    final hour = now.hour;
    final minute = now.minute;
    final time = hour * 100 + minute;
    if (time >= 930 && time < 1130) return true;
    if (time >= 1300 && time < 1500) return true;
    return false;
  }

  @override
  void dispose() {
    for (final timer in _holdTimers.values) {
      timer.cancel();
    }
    _holdTimers.clear();
    _notifiedAlerts.clear();
    super.dispose();
  }
}
