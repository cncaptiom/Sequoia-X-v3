import 'dart:convert';
import 'package:crypto/crypto.dart';
import 'package:dio/dio.dart';
import '../models/pc_recommendation.dart';
import '../models/trade_signal.dart';

/// 安全解码 JSON，处理非 Map 类型和空响应
Map<String, dynamic>? _safeDecodeMap(String raw) {
  try {
    final decoded = json.decode(raw);
    if (decoded is Map<String, dynamic>) return decoded;
    return null;
  } catch (_) {
    return null;
  }
}

/// 生成 COS V5 签名认证头
/// 算法对齐腾讯云 COS Python SDK (cos_auth.py)
String _buildCosAuth({
  required String secretId,
  required String secretKey,
  required String method,
  required String path,
  Map<String, String>? extraHeaders,
  int expiresInSeconds = 3600,
}) {
  final now = DateTime.now().millisecondsSinceEpoch ~/ 1000;
  // sign_time 从 now-60 开始（对齐 SDK）
  final signTime = '${now - 60};${now + expiresInSeconds}';

  // 1. SignKey = HMAC-SHA1(SecretKey, KeyTime)
  final signKey = Hmac(sha1, utf8.encode(secretKey))
      .convert(utf8.encode(signTime))
      .toString();

  // 2. 对 header 做 URL encode（保留 -_.~）
  String _uriEncode(String s) {
    return Uri.encodeComponent(s)
        .replaceAll('%7E', '~')
        .replaceAll('%2D', '-')
        .replaceAll('%5F', '_')
        .replaceAll('.', '%2E');
  }

  final headers = <String, String>{};
  if (extraHeaders != null) headers.addAll(extraHeaders);
  final keys = headers.keys.map((k) => k.toLowerCase()).toList()..sort();

  // 3. format_str = method\npath\nparams\nheaders\n
  //    params 是 URL-encoded key=value 对 & 连接
  //    headers 是 URL-encoded key=value 对 & 连接（按 key 排序）
  final paramStr = '';
  final headerStr = keys.map((k) =>
      '${_uriEncode(k)}=${_uriEncode(headers[k]!)}').join('&');
  final formatStr = '${method.toLowerCase()}\n$path\n$paramStr\n$headerStr\n';

  // 4. SHA1(format_str)
  final formatSha1 = sha1.convert(utf8.encode(formatStr)).toString();

  // 5. StringToSign
  final stringToSign = 'sha1\n$signTime\n$formatSha1\n';

  // 6. Signature
  final signature = Hmac(sha1, utf8.encode(signKey))
      .convert(utf8.encode(stringToSign))
      .toString();

  return 'q-sign-algorithm=sha1&q-ak=$secretId'
      '&q-sign-time=$signTime&q-key-time=$signTime'
      '&q-header-list=${keys.join(';')}&q-url-param-list='
      '&q-signature=$signature';
}

/// PC端 COS 数据下载服务
/// 从腾讯云 COS 拉取 Sequoia-X PC 端计算的全市场评级和推荐数据
class CosApiService {
  final Dio _dio;

  /// COS 基础配置（可从设置页面修改）
  String _bucket = 'sequoia-recommend-1442452595';
  String _region = 'ap-beijing';
  bool _enabled = true;
  String? _secretId;
  String? _secretKey;

  CosApiService()
      : _dio = Dio(BaseOptions(
          connectTimeout: const Duration(seconds: 15),
          receiveTimeout: const Duration(seconds: 30),
        ));

  /// 用于下载大文件（如全量评级）的 Dio 实例，更长超时
  Dio _getLargeFileDio() => Dio(BaseOptions(
        connectTimeout: const Duration(seconds: 20),
        receiveTimeout: const Duration(seconds: 120),
      ));

  // ── 配置 ──

  void configure(String bucket, String region) {
    _bucket = bucket;
    _region = region;
  }

  /// 配置 COS 鉴权凭证（用于上传用户数据）
  void configureAuth(String secretId, String secretKey) {
    _secretId = secretId;
    _secretKey = secretKey;
  }

  bool get hasAuth => _secretId != null && _secretKey != null;

  bool get enabled => _enabled;
  set enabled(bool v) => _enabled = v;

  String get bucketName => _bucket;
  String get regionName => _region;

  String _cosUrl(String path) =>
      'https://$_bucket.cos.$_region.myqcloud.com/$path';

  /// 通用 GET 请求
  Future<Map<String, dynamic>?> _getJson(String path) async {
    if (!_enabled) return null;
    try {
      final response = await _dio.get(
        _cosUrl(path),
        options: Options(responseType: ResponseType.plain),
      );
      if (response.statusCode == 200 && response.data != null) {
        return _safeDecodeMap(response.data.toString());
      }
      return null;
    } on DioException catch (e) {
      if (e.response?.statusCode == 404) {
        // 数据不存在（比如当日还没运行PC端）
        return null;
      }
      rethrow;
    }
  }

  /// 大文件 JSON 下载（超时更长 + 自动重试一次）
  Future<Map<String, dynamic>?> _getLargeJson(String path) async {
    if (!_enabled) return null;
    final dio = _getLargeFileDio();
    for (var attempt = 0; attempt < 2; attempt++) {
      try {
        final response = await dio.get(
          _cosUrl(path),
          options: Options(responseType: ResponseType.plain),
        );
        if (response.statusCode == 200 && response.data != null) {
          return _safeDecodeMap(response.data.toString());
        }
        return null;
      } on DioException catch (e) {
        if (e.response?.statusCode == 404) return null;
        if (attempt == 0) {
          await Future.delayed(const Duration(seconds: 2));
          continue;
        }
        return null; // 重试仍失败返回 null，不抛异常
      }
    }
    return null;
  }

  // ── API 接口 ──

  /// 获取PC端全市场评级数据（大文件，使用长超时 + 自动重试）
  Future<PcFullRatings?> fetchFullRatings() async {
    final json = await _getLargeJson('ratings/today.json');
    if (json == null) return null;
    return PcFullRatings.fromJson(json);
  }

  /// 获取PC端每日组合摘要（首页展示）
  Future<PcPortfolioSummary?> fetchPortfolioSummary() async {
    final json = await _getJson('summary/today.json');
    if (json == null) return null;
    return PcPortfolioSummary.fromJson(json);
  }

  /// 获取PC端策略配置
  Future<Map<String, dynamic>?> fetchRatingConfig() async {
    final json = await _getJson('config/rating_config.json');
    if (json == null) return null;
    return json['config'] as Map<String, dynamic>?;
  }

  /// 获取PC端市场状态
  Future<Map<String, dynamic>?> fetchMarketRegime() async {
    final json = await _getJson('regime/today.json');
    if (json == null) return null;
    return json['regime'] as Map<String, dynamic>?;
  }

  /// 获取策略收益率回测数据
  Future<Map<String, dynamic>?> fetchStrategyBacktest() async {
    return await _getJson('backtest/strategy_performance.json');
  }

  /// 获取PC端完整操作信号（signals/today.json，含形态分/仓位/理由）
  Future<PcSignalList?> fetchSignals() async {
    final json = await _getJson('signals/today.json');
    if (json == null) return null;
    return PcSignalList.fromJson(json);
  }

  /// 带 COS 签名认证的 PUT 请求
  Future<bool> _putJson(String path, Map<String, dynamic> data) async {
    if (!_enabled) return false;
    final body = json.encode(data);
    final bodyBytes = utf8.encode(body);
    try {
      final uri = Uri.parse(_cosUrl(path));
      final options = Options(
        contentType: 'application/json',
        responseType: ResponseType.plain,
      );

      // 有密钥时加签名头（需同时签 content-type + content-length）
      if (_secretId != null && _secretKey != null) {
        final auth = _buildCosAuth(
          secretId: _secretId!,
          secretKey: _secretKey!,
          method: 'PUT',
          path: uri.path,
          extraHeaders: {
            'content-type': 'application/json',
            'content-length': bodyBytes.length.toString(),
          },
        );
        options.headers = {'Authorization': auth};
      }

      await _dio.put(uri.toString(), data: body, options: options);
      return true;
    } catch (_) {
      return false;
    }
  }

  /// 获取自选股云端备份
  Future<Map<String, dynamic>?> fetchWatchlistBackup() async {
    return await _getJson('user/watchlist.json');
  }

  /// 上传自选股到云端备份
  Future<bool> uploadWatchlistBackup(Map<String, dynamic> data) async {
    return await _putJson('user/watchlist.json', data);
  }
}
