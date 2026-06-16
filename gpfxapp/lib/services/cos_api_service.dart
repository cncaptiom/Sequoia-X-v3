import 'dart:convert';
import 'package:dio/dio.dart';
import '../models/pc_recommendation.dart';

/// PC端 COS 数据下载服务
/// 从腾讯云 COS 拉取 Sequoia-X PC 端计算的全市场评级和推荐数据
class CosApiService {
  final Dio _dio;

  /// COS 基础配置（可从设置页面修改）
  String _bucket = 'sequoia-recommend-1442452595';
  String _region = 'ap-beijing';
  bool _enabled = true;

  CosApiService()
      : _dio = Dio(BaseOptions(
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 15),
        ));

  // ── 配置 ──

  void configure(String bucket, String region) {
    _bucket = bucket;
    _region = region;
  }

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
        return json.decode(response.data.toString()) as Map<String, dynamic>;
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

  // ── API 接口 ──

  /// 获取PC端全市场评级数据
  Future<PcFullRatings?> fetchFullRatings() async {
    final json = await _getJson('ratings/today.json');
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
}
