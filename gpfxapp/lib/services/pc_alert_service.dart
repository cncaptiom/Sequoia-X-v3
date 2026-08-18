import '../models/stock_quote.dart';
import '../models/strategy_config.dart';
import 'notification_service.dart';
import 'storage_service.dart';

/// PC端推荐价格区间触发告警服务
/// 在交易时段检查实时价格是否到达PC端推荐的买入/止损/止盈位
class PcAlertService {
  final NotificationService _notifier = NotificationService();
  final StorageService _storage = StorageService();

  /// 防重复通知标记: { code_alertType: last_notified_time }
  final Map<String, DateTime> _cooldowns = {};

  /// 通知冷却时间（同类型30分钟内不重复提醒）
  static const Duration _cooldownDuration = Duration(minutes: 30);

  /// 检查单只股票的PC端价格触发告警
  /// 在WatchlistProvider的_refreshSingleStock中调用
  Future<void> checkPriceAlerts({
    required String code,
    required String name,
    required StockQuote quote,
    required StrategyConfig config,
    Map<String, dynamic>? pcRecommendation, // PC端推荐数据（含买入区间/止损/止盈）
    bool isHeld = false,
  }) async {
    if (!config.notificationEnabled) return;
    if (pcRecommendation == null) return;

    final currentPrice = quote.currentPrice;

    // 检查买入区间触发
    if (config.notifyBuyZone && !isHeld) {
      final entryLow = pcRecommendation['entry_low'] as num?;
      final entryHigh = pcRecommendation['entry_high'] as num?;
      if (entryLow != null && entryHigh != null) {
        final entryLowD = entryLow.toDouble();
        final entryHighD = entryHigh.toDouble();
        if (currentPrice >= entryLowD && currentPrice <= entryHighD) {
          await _fireAlert(
            code: code, name: name,
            alertType: 'buy_zone',
            title: '买入区间',
            message: '当前价 $currentPrice 进入买入区间 [$entryLowD - $entryHighD]',
          );
        }
      }
    }

    // 检查止损触发（仅持仓股票）
    if (config.notifyStopLoss && isHeld) {
      final stopLoss = pcRecommendation['stop_loss'] as num?;
      if (stopLoss != null) {
        final stopLossD = stopLoss.toDouble();
        if (currentPrice <= stopLossD) {
          await _fireAlert(
            code: code, name: name,
            alertType: 'stop_loss',
            title: '止损触发',
            message: '当前价 $currentPrice 触及止损位 $stopLossD',
          );
        }
      }
    }

    // 检查目标价触发（仅持仓股票）
    if (config.notifyTakeProfit && isHeld) {
      final target1 = pcRecommendation['target_1'] as num?;
      if (target1 != null && currentPrice >= target1.toDouble()) {
        await _fireAlert(
          code: code, name: name,
          alertType: 'take_profit_1',
          title: '目标价1触及',
          message: '当前价 $currentPrice 达到第一目标价 ${target1.toDouble()}',
        );
      }
    }

    // 检查涨幅异常（无论是否持仓）
    if (config.notifyBreakout) {
      final changePercent = quote.changePercent.abs();
      if (changePercent >= 5) {
        await _fireAlert(
          code: code, name: name,
          alertType: 'surge_${changePercent >= 9.8 ? "limit_up" : "big_move"}',
          title: changePercent >= 9.8 ? '涨停/跌停' : '异动提醒',
          message: '${quote.changePercentText} 振幅${quote.amplitude}%',
        );
      }
    }
  }

  /// 发送告警（带冷却和防重复）
  Future<void> _fireAlert({
    required String code,
    required String name,
    required String alertType,
    required String title,
    required String message,
  }) async {
    final key = '${code}_$alertType';
    final now = DateTime.now();

    // 冷却检查
    if (_cooldowns.containsKey(key)) {
      final elapsed = now.difference(_cooldowns[key]!);
      if (elapsed < _cooldownDuration) return;
    }

    _cooldowns[key] = now;
    await _notifier.sendSignalNotification(
      stockName: name,
      stockCode: code,
      signalType: title,
      message: message,
    );
  }

  /// 清除冷却记录（当股票从自选移除时调用）
  void clearCooldown(String code) {
    _cooldowns.removeWhere((key, _) => key.startsWith('${code}_'));
  }

  /// 清除所有冷却记录
  void clearAllCooldowns() {
    _cooldowns.clear();
  }
}
