import 'package:dio/dio.dart';
import 'package:enough_convert/gbk.dart';
import '../models/stock_info.dart';
import '../models/stock_quote.dart';
import '../models/kline_data.dart';

class StockApiService {
  final Dio _dio;

  StockApiService()
      : _dio = Dio(BaseOptions(
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 15),
          headers: {
            'User-Agent':
                'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
            'Referer': 'https://quote.eastmoney.com/',
          },
        ));

  // 东方财富API基础地址
  static const String _eastMoneyBase = 'https://push2.eastmoney.com/api/qt/stock/get';
  static const String _eastMoneyKLine = 'https://push2his.eastmoney.com/api/qt/stock/kline/get';
  static const String _eastMoneySearch = 'https://searchadapter.eastmoney.com/api/suggest/get';
  static const String _tencentQuote = 'https://qt.gtimg.cn/q';

  /// 将市场代码转为东方财富API的数字格式
  /// SH → 1 (上海), SZ → 0 (深圳), BJ → 2 (北京)
  static String _marketToId(String market) {
    if (market == 'SH') return '1';
    if (market == 'SZ') return '0';
    if (market == 'BJ') return '2';
    return '1';
  }

  /// 构建东方财富secid
  static String _secId(String code, String market) => '${_marketToId(market)}.$code';

  /// 将市场代码转为腾讯API前缀 (sh/sz)
  static String _tencentPrefix(String market) {
    if (market == 'SH') return 'sh';
    if (market == 'SZ') return 'sz';
    if (market == 'BJ') return 'bj';
    return 'sh';
  }

  /// 搜索股票（支持代码和名称模糊搜索）
  Future<List<StockInfo>> searchStock(String keyword) async {
    if (keyword.isEmpty) return [];

    try {
      final response = await _dio.get(
        _eastMoneySearch,
        queryParameters: {
          'input': keyword,
          'type': 14,
          'token': 'D43BF722C8E33BDC906FB84D85E326E8',
          'count': 20,
        },
      );

      final data = response.data;
      if (data == null || data['QuotationCodeTable'] == null) return [];

      final stocks = <StockInfo>[];
      final list = data['QuotationCodeTable']['Data'] as List? ?? [];
      for (final item in list) {
        final code = item['Code']?.toString() ?? '';
        final name = item['Name']?.toString() ?? '';
        final marketCode = item['MarketCode']?.toString() ?? '';

        String market;
        if (marketCode == '1' || code.startsWith('6') || code.startsWith('9')) {
          market = 'SH';
        } else if (marketCode == '0' || code.startsWith('0') || code.startsWith('3')) {
          market = 'SZ';
        } else if (code.startsWith('8') || code.startsWith('4')) {
          market = 'BJ';
        } else {
          market = marketCode == '1' ? 'SH' : 'SZ';
        }

        stocks.add(StockInfo(code: code, name: name, market: market));
      }
      return stocks;
    } catch (e) {
      return _searchByCode(keyword);
    }
  }

  /// 当模糊搜索失败时，尝试直接按代码查询
  Future<List<StockInfo>> _searchByCode(String keyword) async {
    final code = keyword.replaceAll(RegExp(r'[^0-9]'), '');
    if (code.isEmpty || code.length < 6) return [];

    String market;
    if (code.startsWith('6') || code.startsWith('9')) {
      market = 'SH';
    } else if (code.startsWith('0') || code.startsWith('3')) {
      market = 'SZ';
    } else if (code.startsWith('8') || code.startsWith('4')) {
      market = 'BJ';
    } else {
      return [];
    }

    try {
      final quote = await getQuote(code, market);
      return [StockInfo(code: code, name: quote.name, market: market)];
    } catch (_) {
      return [StockInfo(code: code, name: code, market: market)];
    }
  }

  /// 腾讯行情API — 主用（稳定可靠）
  Future<StockQuote> getQuoteFromTencent(String code, String market) async {
    final prefix = _tencentPrefix(market);
    final response = await _dio.get(
      '$_tencentQuote=$prefix$code',
      options: Options(responseType: ResponseType.bytes),
    );
    final raw = gbk.decode(response.data as List<int>);
    if (raw.isEmpty || !raw.contains('~')) throw Exception('腾讯行情返回空: $code');
    return StockQuote.fromTencent(raw, code);
  }

  /// 获取实时行情 — 优先腾讯，失败时降级到东方财富
  Future<StockQuote> getQuote(String code, String market) async {
    try {
      return await getQuoteFromTencent(code, market);
    } catch (_) {
      // 降级到东方财富API
      final response = await _dio.get(
        _eastMoneyBase,
        queryParameters: {
          'secid': _secId(code, market),
          'fields':
              'f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f53,f57,f58,f170,f171,f84,f85,f116,f117,f152,f162,f167,f168,f169',
          'invt': 2,
          'ut': 'fa5fd1943c7b386f172d6893dbfd32bb',
        },
      );

      final data = response.data['data'] as Map<String, dynamic>?;
      if (data == null) throw Exception('获取行情失败: $code');

      return StockQuote.fromEastMoneySingle(data, code, '');
    }
  }

  /// 补充获取腾讯API缺失的字段（量比、PE、PB等）
  /// 腾讯行情不提供这些数据，从东方财富单独获取后合并到原Quote
  Future<StockQuote> supplementQuote(StockQuote quote, String market) async {
    try {
      final response = await _dio.get(
        _eastMoneyBase,
        queryParameters: {
          'secid': _secId(quote.code, market),
          'fields': 'f52,f53,f162', // PE, 量比, PB
          'invt': 2,
          'ut': 'fa5fd1943c7b386f172d6893dbfd32bb',
        },
      ).timeout(const Duration(seconds: 3));

      final data = response.data['data'] as Map<String, dynamic>?;
      if (data == null) return quote;

      final pe = double.tryParse(data['f52']?.toString() ?? '0') ?? 0;
      final volumeRatio = double.tryParse(data['f53']?.toString() ?? '0')?.toInt() ?? 0;
      final pb = (double.tryParse(data['f162']?.toString() ?? '0') ?? 0) / 100;

      return quote.copyWith(
        pe: pe,
        pb: pb,
        volumeRatio: volumeRatio,
      );
    } catch (_) {
      return quote; // 补充失败不影响已有数据
    }
  }

  /// 批量获取实时行情 — 合并请求到腾讯API
  Future<List<StockQuote>> getQuotes(List<StockInfo> stocks) async {
    if (stocks.isEmpty) return [];

    // 腾讯支持批量：合并成 sh600519,sz000001 格式
    final codes = stocks.map((s) => '${_tencentPrefix(s.market)}${s.code}').join(',');
    try {
      final response = await _dio.get(
        '$_tencentQuote=$codes',
        options: Options(responseType: ResponseType.bytes),
      );
      final raw = gbk.decode(response.data as List<int>);
      if (raw.isNotEmpty) {
        final results = <StockQuote>[];
        for (final line in raw.split('\n')) {
          if (line.trim().isEmpty) continue;
          try {
            // 从行内容直接解析（fromTencent会自动提取代码）
            results.add(StockQuote.fromTencent(line, ''));
          } catch (_) {}
        }
        if (results.isNotEmpty) return results;
      }
    } catch (_) {}

    // 降级：逐个查询
    final results = <StockQuote>[];
    for (final stock in stocks) {
      try {
        results.add(await getQuote(stock.code, stock.market));
      } catch (_) {}
    }
    return results;
  }

  /// 获取K线数据（EastMoney优先 → Sina降级）
  Future<List<KLineData>> getKLineData(
    String code,
    String market, {
    KLinePeriod period = KLinePeriod.daily,
    int count = 120,
  }) async {
    try {
      return await _getKLineDataEastMoney(code, market, period: period, count: count)
          .timeout(const Duration(seconds: 5));
    } catch (_) {
      try {
        return await _getKLineDataSina(code, market, count: count);
      } catch (_) {
        return [];
      }
    }
  }

  /// 东方财富K线API
  Future<List<KLineData>> _getKLineDataEastMoney(
    String code,
    String market, {
    KLinePeriod period = KLinePeriod.daily,
    int count = 120,
  }) async {
    final response = await _dio.get(
      _eastMoneyKLine,
      queryParameters: {
        'secid': _secId(code, market),
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57',
        'klt': period.code,
        'fqt': 1, // 前复权
        'end': '20500101',
        'lmt': count,
        'ut': 'fa5fd1943c7b386f172d6893dbfd32bb',
        '_': DateTime.now().millisecondsSinceEpoch.toString(), // 防缓存
      },
    );

    final klines = response.data['data']?['klines'] as List? ?? [];
    return klines.map<KLineData>((raw) {
      final parts = (raw as String).split(',');
      return KLineData.fromEastMoney(parts);
    }).toList();
  }

  /// 新浪财经K线API（降级方案）
  Future<List<KLineData>> _getKLineDataSina(String code, String market, {int count = 120}) async {
    final prefix = _tencentPrefix(market);
    final response = await _dio.get(
      'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData',
      queryParameters: {
        'symbol': '$prefix$code',
        'scale': 240,
        'ma': 'no',
        'datalen': count,
      },
      options: Options(headers: {'Referer': 'https://finance.sina.com.cn/'}),
    );
    final list = response.data as List? ?? [];
    return list.map<KLineData>((item) {
      return KLineData.fromSina(item as Map<String, dynamic>);
    }).toList();
  }

  /// 获取大盘指数
  Future<List<StockQuote>> getMarketIndices() async {
    const indices = [
      {'code': '000001', 'market': 'SH', 'name': '上证指数'},
      {'code': '399001', 'market': 'SZ', 'name': '深证成指'},
      {'code': '399006', 'market': 'SZ', 'name': '创业板指'},
    ];

    // 用腾讯API一次性获取
    final codes = indices.map((i) => '${_tencentPrefix(i['market']!)}${i['code']}').join(',');
    try {
      final response = await _dio.get(
        '$_tencentQuote=$codes',
        options: Options(responseType: ResponseType.bytes),
      );
      final raw = gbk.decode(response.data as List<int>);
      if (raw.isNotEmpty) {
        final results = <StockQuote>[];
        for (final line in raw.split('\n')) {
          if (line.trim().isEmpty) continue;
          try {
            final quote = StockQuote.fromTencent(line, '');
            results.add(quote);
          } catch (_) {}
        }
        if (results.length == 3) return results;
      }
    } catch (_) {}

    // 降级：逐个查询
    final results = <StockQuote>[];
    for (final idx in indices) {
      try {
        results.add(await getQuote(idx['code']!, idx['market']!));
      } catch (_) {}
    }
    return results;
  }

  // ==================== 发现股票API ====================

  static const String _sinaRankingUrl =
      'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData';

  /// 从Sina排名API解析StockQuote
  StockQuote _fromSinaRanking(Map<String, dynamic> item) {
    final symbol = item['symbol']?.toString() ?? '';
    final code = item['code']?.toString() ?? '';
    final name = item['name']?.toString() ?? '';

    // 从symbol前缀判断市场：sh→SH, sz→SZ, bj→BJ
    String market = '';
    if (symbol.startsWith('sh')) market = 'SH';
    else if (symbol.startsWith('sz')) market = 'SZ';
    else if (symbol.startsWith('bj')) market = 'BJ';
    else { // fallback: 从代码推断
      if (code.startsWith('6') || code.startsWith('9')) market = 'SH';
      else if (code.startsWith('0') || code.startsWith('3') || code.startsWith('2')) market = 'SZ';
      else if (code.startsWith('8') || code.startsWith('4')) market = 'BJ';
      else market = 'SH';
    }
    final trade = double.tryParse(item['trade']?.toString() ?? '0') ?? 0;
    final settlement = double.tryParse(item['settlement']?.toString() ?? '0') ?? 0;
    final open = double.tryParse(item['open']?.toString() ?? '0') ?? 0;
    final high = double.tryParse(item['high']?.toString() ?? '0') ?? 0;
    final low = double.tryParse(item['low']?.toString() ?? '0') ?? 0;
    final volume = double.tryParse(item['volume']?.toString() ?? '0') ?? 0; // 股
    final amount = double.tryParse(item['amount']?.toString() ?? '0') ?? 0; // 元
    final mktcap = double.tryParse(item['mktcap']?.toString() ?? '0') ?? 0; // 万元
    final nmc = double.tryParse(item['nmc']?.toString() ?? '0') ?? 0;
    final turnover = double.tryParse(item['turnoverratio']?.toString() ?? '0') ?? 0;
    final per = double.tryParse(item['per']?.toString() ?? '0') ?? 0;
    final pb = double.tryParse(item['pb']?.toString() ?? '0') ?? 0;

    return StockQuote(
      code: code,
      name: name,
      currentPrice: trade,
      yesterdayClose: settlement,
      openPrice: open,
      highPrice: high,
      lowPrice: low,
      volume: volume,
      amount: amount / 10000, // 元→万元
      changePercent: (item['changepercent'] as num?)?.toDouble() ?? 0,
      changeAmount: (item['pricechange'] as num?)?.toDouble() ?? 0,
      turnoverRate: turnover,
      amplitude: 0,
      pe: per,
      pb: pb,
      marketCap: mktcap / 10000, // 万元→亿元
      circulatingMarketCap: nmc / 10000,
      volumeRatio: 0,
      market: market,
      time: DateTime.now(),
    );
  }

  /// Sina排名查询
  Future<List<StockQuote>> _fetchSinaRanking({
    required String sort,
    int asc = 0,
    int count = 20,
  }) async {
    try {
      final response = await _dio.get(
        _sinaRankingUrl,
        queryParameters: {
          'page': 1,
          'num': count,
          'sort': sort,
          'asc': asc,
          'node': 'hs_a',
          'symbol': '',
          '_s_r_a': 'init',
        },
        options: Options(
          headers: {'Referer': 'https://finance.sina.com.cn'},
        ),
      );
      final list = response.data as List? ?? [];
      return list.map((item) => _fromSinaRanking(item as Map<String, dynamic>)).toList();
    } catch (e) {
      return [];
    }
  }

  /// 涨幅榜
  Future<List<StockQuote>> getTopGainers({int count = 20}) async {
    return _fetchSinaRanking(sort: 'changepercent', asc: 0, count: count);
  }

  /// 跌幅榜
  Future<List<StockQuote>> getTopLosers({int count = 20}) async {
    return _fetchSinaRanking(sort: 'changepercent', asc: 1, count: count);
  }

  /// 成交额榜
  Future<List<StockQuote>> getVolumeLeaders({int count = 20}) async {
    return _fetchSinaRanking(sort: 'amount', asc: 0, count: count);
  }

  // ==================== 行业板块 ===================

  static const String _clistUrl = 'https://push2.eastmoney.com/api/qt/clist/get';

  /// 热门行业板块静态备份BK代码（当API请求失败时使用）
  static const List<Map<String, String>> _fallbackSectors = [
    {'code': 'BK1027', 'name': '小金属'},
    {'code': 'BK1015', 'name': '能源金属'},
    {'code': 'BK1033', 'name': '电池'},
    {'code': 'BK0728', 'name': '环保行业'},
    {'code': 'BK0447', 'name': '酿酒行业'},
    {'code': 'BK0446', 'name': '医药制造'},
    {'code': 'BK0548', 'name': '生物制品'},
    {'code': 'BK0481', 'name': '汽车零部件'},
    {'code': 'BK0456', 'name': '家用电器'},
    {'code': 'BK0477', 'name': '酿酒概念'},
    {'code': 'BK0474', 'name': '保险'},
    {'code': 'BK0445', 'name': '汽车整车'},
    {'code': 'BK0444', 'name': '房地产开发'},
    {'code': 'BK0443', 'name': '证券'},
    {'code': 'BK0442', 'name': '银行'},
    {'code': 'BK0441', 'name': '保险Ⅱ'},
    {'code': 'BK1130', 'name': '半导体'},
    {'code': 'BK1131', 'name': '软件开发'},
    {'code': 'BK1132', 'name': '通信设备'},
    {'code': 'BK1133', 'name': '消费电子'},
    {'code': 'BK1135', 'name': '化学制药'},
    {'code': 'BK1136', 'name': '中药'},
    {'code': 'BK1137', 'name': '医疗器械'},
    {'code': 'BK1140', 'name': '光伏设备'},
    {'code': 'BK1145', 'name': '风电设备'},
    {'code': 'BK1147', 'name': '航天航空'},
    {'code': 'BK1150', 'name': '煤炭行业'},
    {'code': 'BK1151', 'name': '钢铁行业'},
    {'code': 'BK1152', 'name': '有色金属'},
    {'code': 'BK1153', 'name': '化工行业'},
    {'code': 'BK1155', 'name': '食品饮料'},
    {'code': 'BK1160', 'name': '文化传媒'},
    {'code': 'BK1161', 'name': '教育'},
    {'code': 'BK1162', 'name': '商业百货'},
    {'code': 'BK1163', 'name': '旅游酒店'},
    {'code': 'BK1165', 'name': '农牧饲渔'},
    {'code': 'BK1167', 'name': '工程建设'},
    {'code': 'BK1168', 'name': '装修装饰'},
    {'code': 'BK1170', 'name': '电力行业'},
    {'code': 'BK1171', 'name': '燃气'},
    {'code': 'BK1172', 'name': '交运物流'},
    {'code': 'BK1173', 'name': '高速公路'},
    {'code': 'BK1175', 'name': '港口水运'},
    {'code': 'BK1177', 'name': '航天军工'},
    {'code': 'BK1180', 'name': '仪器仪表'},
    {'code': 'BK1181', 'name': '专用设备'},
    {'code': 'BK1182', 'name': '通用设备'},
    {'code': 'BK1183', 'name': '电子元件'},
    {'code': 'BK1185', 'name': '计算机设备'},
    {'code': 'BK1187', 'name': '互联网服务'},
  ];

  /// 通用clist请求（用于板块数据）
  Future<List<StockQuote>> _fetchClist({
    required String fs,
    String sortField = 'f3',
    int sortOrder = 1,
    int count = 20,
    Duration? timeout,
  }) async {
    try {
      final response = await _dio.get(
        _clistUrl,
        queryParameters: {
          'pn': 1,
          'pz': count,
          'po': sortOrder,
          'np': 1,
          'ut': 'fa5fd1943c7b386f172d6893dbfd32bb',
          'fltt': 2,
          'invt': 2,
          'fid': sortField,
          'fs': fs,
          'fields': 'f2,f3,f4,f12,f14',
          '_': DateTime.now().millisecondsSinceEpoch.toString(),
        },
      ).timeout(timeout ?? const Duration(seconds: 15));

      final diff = response.data['data']?['diff'] as List? ?? [];
      return diff.map<StockQuote>((item) {
        final data = item as Map<String, dynamic>;
        final code = data['f12']?.toString() ?? '';
        final name = data['f14']?.toString() ?? '';
        return StockQuote.fromEastMoneyList(data, code, name);
      }).toList();
    } catch (e) {
      return [];
    }
  }

  /// 通过ulist API获取静态板块列表的实时行情
  Future<List<StockQuote>> _fetchUlistSectors() async {
    final secids = _fallbackSectors.map((s) => '90.${s['code']}').join(',');
    try {
      final response = await _dio.get(
        'https://push2.eastmoney.com/api/qt/ulist.np/get',
        queryParameters: {
          'fields': 'f2,f3,f4,f12,f14',
          'secids': secids,
          'ut': 'fa5fd1943c7b386f172d6893dbfd32bb',
          '_': DateTime.now().millisecondsSinceEpoch.toString(),
        },
      ).timeout(const Duration(seconds: 5));
      final data = response.data;
      if (data != null && data['data'] != null) {
        final diff = data['data'] as List? ?? [];
        if (diff.isNotEmpty) {
          return diff.map<StockQuote>((item) {
            final d = item as Map<String, dynamic>;
            final code = d['f12']?.toString() ?? '';
            final name = d['f14']?.toString() ?? '';
            return StockQuote.fromEastMoneyList(d, code, name);
          }).toList();
        }
      }
    } catch (_) {}
    // 完全降级：返回静态数据（涨跌幅为0）
    return _fallbackSectors.map((s) => StockQuote(
      code: s['code']!,
      name: s['name']!,
      currentPrice: 0,
      yesterdayClose: 0,
      openPrice: 0,
      highPrice: 0,
      lowPrice: 0,
      volume: 0,
      amount: 0,
      changePercent: 0,
      changeAmount: 0,
      turnoverRate: 0,
      amplitude: 0,
      pe: 0,
      pb: 0,
      marketCap: 0,
      circulatingMarketCap: 0,
      volumeRatio: 0,
      time: DateTime.now(),
    )).toList();
  }

  /// 行业板块列表（多层降级：clist → ulist → 静态数据）
  Future<List<StockQuote>> getIndustrySectors({int count = 50}) async {
    // 第一优先：clist API（在Android上可能正常工作，短超时5秒）
    try {
      final result = await _fetchClist(
        fs: 'm:90+t:2',
        sortField: 'f3',
        sortOrder: 1,
        count: count,
        timeout: const Duration(seconds: 5),
      );
      if (result.isNotEmpty) return result;
    } catch (_) {}
    // 第二优先：ulist API + 静态BK代码
    return _fetchUlistSectors();
  }

  /// 板块成分股
  Future<List<StockQuote>> getSectorStocks(String sectorCode, {int count = 50}) async {
    return _fetchClist(
      fs: 'b:$sectorCode',
      sortField: 'f3',
      sortOrder: 1,
      count: count,
    );
  }
}
