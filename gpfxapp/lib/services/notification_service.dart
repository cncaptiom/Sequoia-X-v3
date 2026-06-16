import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

class NotificationService {
  static final NotificationService _instance = NotificationService._();
  factory NotificationService() => _instance;
  NotificationService._();

  final FlutterLocalNotificationsPlugin _plugin = FlutterLocalNotificationsPlugin();
  bool _initialized = false;

  Future<void> init() async {
    if (_initialized) return;

    const androidSettings = AndroidInitializationSettings('@mipmap/ic_launcher');
    const settings = InitializationSettings(android: androidSettings);
    await _plugin.initialize(settings);
    _initialized = true;
  }

  /// 发送买卖信号通知
  Future<void> sendSignalNotification({
    required String stockName,
    required String stockCode,
    required String signalType, // "买入信号" / "卖出信号" / "预警"
    required String message,
  }) async {
    if (!_initialized) await init();

    const androidDetails = AndroidNotificationDetails(
      'stock_signals',
      '股票信号',
      channelDescription: '股票买卖信号通知',
      importance: Importance.high,
      priority: Priority.high,
      color: Color(0xFF87CEEB),
    );

    const details = NotificationDetails(android: androidDetails);
    final id = DateTime.now().millisecondsSinceEpoch ~/ 1000;

    await _plugin.show(
      id,
      '[$signalType] $stockName($stockCode)',
      message,
      details,
    );
  }

  /// 发送价格预警通知
  Future<void> sendPriceAlert({
    required String stockName,
    required String stockCode,
    required double targetPrice,
    required double currentPrice,
    required bool isUpper, // true: 触及上轨/目标价, false: 触及下轨/止损
  }) async {
    final signalType = isUpper ? '价格预警' : '风险提示';
    final direction = isUpper ? '触及目标价' : '触及止损位';
    final message = '$stockName 当前价 $currentPrice，$direction $targetPrice';

    await sendSignalNotification(
      stockName: stockName,
      stockCode: stockCode,
      signalType: signalType,
      message: message,
    );
  }

  /// 取消所有通知
  Future<void> cancelAll() async {
    await _plugin.cancelAll();
  }
}
