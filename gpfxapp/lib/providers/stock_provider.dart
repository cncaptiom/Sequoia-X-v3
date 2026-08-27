import 'dart:async';
import 'package:flutter/material.dart';
import '../models/stock_info.dart';
import '../models/stock_quote.dart';
import '../models/kline_data.dart';
import '../models/analysis_result.dart';
import '../models/strategy_config.dart';
import '../services/stock_api_service.dart';
import '../services/analysis_engine.dart';
import '../services/storage_service.dart';

/// 精选推荐：配对行情+分析结果
class StockAnalysis {
  final StockQuote quote;
  final AnalysisResult analysis;
  StockAnalysis({required this.quote, required this.analysis});
}

class StockProvider extends ChangeNotifier {
  final StockApiService _api = StockApiService();
  final StorageService _storage = StorageService();

  // 行情数据
  StockQuote? _currentQuote;
  List<KLineData> _klineData = [];
  AnalysisResult? _analysisResult;
  StrategyConfig _strategyConfig = const StrategyConfig();
  List<StockQuote> _marketIndices = [];

  // 搜索
  List<StockInfo> _searchResults = [];
  bool _isSearching = false;

  // 发现股票（排行榜+板块）
  List<StockQuote> _topGainers = [];
  List<StockQuote> _topLosers = [];
  List<StockQuote> _volumeLeaders = [];
  List<StockQuote> _sectors = [];
  List<StockQuote> _sectorStocks = [];
  String? _selectedSectorCode;
  String? _selectedSectorName;
  bool _isLoadingDiscovery = false;
  bool _isLoadingSectorStocks = false;
  bool _sectorLoadFailed = false;

  // 精选推荐
  List<StockAnalysis> _topPicks = [];
  bool _isLoadingTopPicks = false;
  bool _topPicksAnalysisDone = false;

  // 加载状态
  bool _isLoading = false;
  String? _error;

  // dispose 保护
  bool _disposed = false;

  // Getters
  StockInfo? get currentStock => _currentStock;
  StockQuote? get currentQuote => _currentQuote;
  List<KLineData> get klineData => _klineData;
  AnalysisResult? get analysisResult => _analysisResult;
  StrategyConfig get strategyConfig => _strategyConfig;
  List<StockQuote> get marketIndices => _marketIndices;
  List<StockInfo> get searchResults => _searchResults;
  bool get isSearching => _isSearching;
  List<StockQuote> get topGainers => _topGainers;
  List<StockQuote> get topLosers => _topLosers;
  List<StockQuote> get volumeLeaders => _volumeLeaders;
  List<StockQuote> get sectors => _sectors;
  List<StockQuote> get sectorStocks => _sectorStocks;
  String? get selectedSectorCode => _selectedSectorCode;
  String? get selectedSectorName => _selectedSectorName;
  bool get isLoadingDiscovery => _isLoadingDiscovery;
  bool get isLoadingSectorStocks => _isLoadingSectorStocks;
  bool get sectorLoadFailed => _sectorLoadFailed;
  List<StockAnalysis> get topPicks => _topPicks;
  bool get isLoadingTopPicks => _isLoadingTopPicks;
  bool get topPicksAnalysisDone => _topPicksAnalysisDone;
  bool get isLoading => _isLoading;
  String? get error => _error;

  // 定时刷新
  Timer? _refreshTimer;
  Timer? _detailRefreshTimer;
  StockInfo? _currentStock;

  /// 初始化
  Future<void> init() async {
    _strategyConfig = await _storage.getStrategyConfig();
    await loadMarketIndices();
  }

  /// 搜索股票
  Future<void> search(String keyword) async {
    if (keyword.isEmpty) {
      _searchResults = [];
      _isSearching = false;
      notifyListeners();
      return;
    }

    _isSearching = true;
    _error = null;
    notifyListeners();

    try {
      _searchResults = await _api.searchStock(keyword);
    } catch (e) {
      _error = '搜索失败: $e';
      _searchResults = [];
    }

    _isSearching = false;
    notifyListeners();
  }

  // ==================== 发现股票 ====================

  /// 加载涨幅榜
  Future<void> loadTopGainers() async {
    _isLoadingDiscovery = true;
    notifyListeners();
    _topGainers = await _api.getTopGainers();
    _isLoadingDiscovery = false;
    notifyListeners();
  }

  /// 加载跌幅榜
  Future<void> loadTopLosers() async {
    _isLoadingDiscovery = true;
    notifyListeners();
    _topLosers = await _api.getTopLosers();
    _isLoadingDiscovery = false;
    notifyListeners();
  }

  /// 加载成交额榜
  Future<void> loadVolumeLeaders() async {
    _isLoadingDiscovery = true;
    notifyListeners();
    _volumeLeaders = await _api.getVolumeLeaders();
    _isLoadingDiscovery = false;
    notifyListeners();
  }

  /// 加载行业板块列表
  Future<void> loadSectors() async {
    _sectorLoadFailed = false;
    _sectors = await _api.getIndustrySectors();
    if (_sectors.isEmpty) _sectorLoadFailed = true;
    notifyListeners();
  }

  /// 从涨幅榜和成交额榜混合分析精选推荐（异步后台执行）
  Future<void> analyzeTopPicks() async {
    // 合并涨幅榜(30) + 成交额榜(10)，去重后取40支
    final seen = <String>{};
    final candidates = <StockQuote>[];
    for (final q in _topGainers.take(30)) {
      if (seen.add(q.code)) candidates.add(q);
    }
    for (final q in _volumeLeaders.take(10)) {
      if (seen.add(q.code)) candidates.add(q);
    }
    // 按涨跌幅排序，最强势的优先分析
    candidates.sort((a, b) => b.changePercent.compareTo(a.changePercent));
    final pool = candidates.take(40).toList();

    if (pool.isEmpty) return;
    // 已有结果且候选池足够大 → 跳过（避免重复分析）
    if (_topPicks.isNotEmpty && pool.length >= 40) return;

    _isLoadingTopPicks = true;
    _topPicksAnalysisDone = false;
    _topPicks = [];
    notifyListeners();

    final engine = AnalysisEngine(config: _strategyConfig);

    // 分批并行，每批5支，避免瞬间太多请求
    // 收集全部分析结果（不限评级），最后统一筛选
    final allResults = <StockAnalysis>[];
    for (var i = 0; i < pool.length; i += 5) {
      final batch = pool.skip(i).take(5).toList();
      final futures = batch.map((quote) async {
        try {
          final market = quote.market.isNotEmpty
              ? quote.market
              : (quote.code.startsWith('6') || quote.code.startsWith('9') ? 'SH' : 'SZ');
          final kline = await _api.getKLineData(quote.code, market, count: 120);
          if (kline.length >= 60) {
            final analysis = engine.analyze(quote: quote, klineData: kline);
            return StockAnalysis(quote: quote, analysis: analysis);
          }
        } catch (_) {}
        return null;
      }).toList();

      final batchResults = await Future.wait(futures);
      for (final r in batchResults) {
        if (r != null) allResults.add(r);
      }

      // 逐批更新UI：先展示已分析出的优选结果
      allResults.sort((a, b) => b.analysis.totalScore.compareTo(a.analysis.totalScore));
      _topPicks = _pickTopResults(allResults);
      notifyListeners();
    }

    _isLoadingTopPicks = false;
    _topPicksAnalysisDone = true;
    notifyListeners();
  }

  /// 从全部分析结果中筛选精选推荐
  /// 优先选"买入/强烈买入"，若没有则选评分最高的潜力股
  List<StockAnalysis> _pickTopResults(List<StockAnalysis> all) {
    // 第一优先：评级为买入/强烈买入的
    final buyResults = all.where((r) =>
        r.analysis.rating == Rating.strongBuy || r.analysis.rating == Rating.buy).toList();
    if (buyResults.isNotEmpty) {
      buyResults.sort((a, b) => b.analysis.totalScore.compareTo(a.analysis.totalScore));
      return buyResults.take(10).toList();
    }
    // 第二优先：趋势通过 + 评分≥2 的，取评分最高5支
    final watchResults = all.where((r) =>
        r.analysis.trendPassed && r.analysis.totalScore >= 2).toList();
    if (watchResults.isNotEmpty) {
      watchResults.sort((a, b) => b.analysis.totalScore.compareTo(a.analysis.totalScore));
      return watchResults.take(5).toList();
    }
    // 第三优先：评分最高的（即使趋势未通过），取最高3支
    all.sort((a, b) => b.analysis.totalScore.compareTo(a.analysis.totalScore));
    return all.take(3).toList();
  }

  /// 加载板块成分股
  Future<void> loadSectorStocks(String sectorCode, String sectorName) async {
    _selectedSectorCode = sectorCode;
    _selectedSectorName = sectorName;
    _isLoadingSectorStocks = true;
    _sectorStocks = [];
    notifyListeners();
    _sectorStocks = await _api.getSectorStocks(sectorCode);
    _isLoadingSectorStocks = false;
    notifyListeners();
  }

  /// 查询股票详情（行情+K线+分析）
  Future<void> loadStockDetail(StockInfo stock) async {
    _currentStock = stock;
    _isLoading = true;
    _error = null;
    notifyListeners();

    try {
      // 先拉行情（腾讯API快速返回）
      _currentQuote = await _api.getQuote(stock.code, stock.market);
      notifyListeners();

      // 补充量比、PE、PB（腾讯API不提供这些字段，东方财富补充，短超时）
      _currentQuote = await _api.supplementQuote(_currentQuote!, stock.market);
      notifyListeners();
    } catch (e) {
      _error = '加载行情失败: $e';
      _isLoading = false;
      notifyListeners();
      return;
    }

    // 再拉K线（独立，失败不影响行情显示）
    try {
      _klineData = await _api.getKLineData(stock.code, stock.market);
    } catch (e) {
      _klineData = [];
    }

    // 运行分析引擎
    if (_klineData.length >= 20) {
      final engine = AnalysisEngine(config: _strategyConfig);
      _analysisResult = engine.analyze(quote: _currentQuote!, klineData: _klineData);
    } else {
      _analysisResult = null;
    }

    _isLoading = false;
    // 开启盘中自动刷新
    _startDetailRefresh();
    notifyListeners();
  }

  /// 盘中自动刷新（交易时段每15秒更新行情）
  void _startDetailRefresh() {
    _detailRefreshTimer?.cancel();
    _detailRefreshTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (_disposed) return;
      if (_isMarketOpen() && _currentStock != null) {
        refreshCurrentQuote();
      }
    });
  }

  /// 停止详情页自动刷新
  void stopDetailRefresh() {
    _detailRefreshTimer?.cancel();
    _detailRefreshTimer = null;
  }

  /// A股交易时间判断
  bool _isMarketOpen() {
    final now = DateTime.now();
    if (now.weekday == DateTime.saturday || now.weekday == DateTime.sunday) return false;
    final time = now.hour * 100 + now.minute;
    return (time >= 930 && time < 1130) || (time >= 1300 && time < 1500);
  }

  /// 强制刷新分析（独立于行情刷新）
  Future<void> refreshAnalysis() async {
    if (_currentStock == null || _currentQuote == null) return;
    try {
      _klineData = await _api.getKLineData(_currentStock!.code, _currentStock!.market);
    } catch (_) {
      _klineData = [];
    }

    if (_klineData.length >= 20) {
      final engine = AnalysisEngine(config: _strategyConfig);
      _analysisResult = engine.analyze(quote: _currentQuote!, klineData: _klineData);
    }
    notifyListeners();
  }

  /// 加载大盘指数
  Future<void> loadMarketIndices() async {
    try {
      _marketIndices = await _api.getMarketIndices();
      notifyListeners();
    } catch (_) {}
  }

  /// 开启定时刷新（首页自选股）
  void startAutoRefresh(void Function() onRefresh) {
    _refreshTimer?.cancel();
    _refreshTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      onRefresh();
    });
  }

  /// 停止定时刷新
  void stopAutoRefresh() {
    _refreshTimer?.cancel();
    _refreshTimer = null;
  }

  /// 刷新当前股票行情
  Future<void> refreshCurrentQuote() async {
    if (_currentStock == null) return;
    try {
      _currentQuote = await _api.getQuote(_currentStock!.code, _currentStock!.market);
      if (_klineData.isNotEmpty) {
        final engine = AnalysisEngine(config: _strategyConfig);
        _analysisResult = engine.analyze(quote: _currentQuote!, klineData: _klineData);
      }
      notifyListeners();
    } catch (_) {}
  }

  /// 更新策略配置
  Future<void> updateStrategy(StrategyConfig config) async {
    _strategyConfig = config;
    await _storage.saveStrategyConfig(config);
    notifyListeners();

    // 如果有当前股票数据，重新分析
    if (_currentQuote != null && _klineData.isNotEmpty) {
      final engine = AnalysisEngine(config: _strategyConfig);
      _analysisResult = engine.analyze(quote: _currentQuote!, klineData: _klineData);
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    stopAutoRefresh();
    stopDetailRefresh();
    super.dispose();
  }
}
