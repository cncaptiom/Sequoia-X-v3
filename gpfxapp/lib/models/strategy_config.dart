/// 交易风格
enum TradingStyle {
  aggressive,
  conservative,
  signal;

  String get label {
    switch (this) {
      case TradingStyle.aggressive:
        return '激进型';
      case TradingStyle.conservative:
        return '保守型';
      case TradingStyle.signal:
        return '信号型';
    }
  }

  String get description {
    switch (this) {
      case TradingStyle.aggressive:
        return '趋势追踪，追涨突破';
      case TradingStyle.conservative:
        return '逆势低吸，注重安全边际';
      case TradingStyle.signal:
        return '多指标共振确认';
    }
  }
}

class StrategyConfig {
  /// 均线策略（MaVolume）
  final bool maVolumeEnabled;
  final int maShort; // 短期均线周期，默认5
  final int maLong; // 长期均线周期，默认20
  final double volumeThreshold; // 放量阈值，默认1.5倍

  /// 趋势突破策略（TurtleTrade）
  final bool turtleTradeEnabled;
  final int breakPeriod; // 突破周期，默认20
  final int newHighPeriod; // 中期新高周期，默认60

  /// 均值回归策略
  final bool meanReversionEnabled;
  final int rsiPeriod; // RSI周期，默认14
  final double rsiOverbought; // 超买阈值，默认70
  final double rsiOversold; // 超卖阈值，默认30

  /// 量价关系策略
  final bool volumePriceEnabled;
  final double volumeSurgeThreshold; // 放量异常阈值，默认3倍

  /// ATR通道突破策略（进攻型）
  final bool atrChannelEnabled;
  final int atrTrendPeriod; // 趋势周期(EMA+ATR周期)，默认30
  final int atrChannelWindow; // 通道计算窗口，默认20
  final double atrExitScore; // 趋势减弱出场阈值，默认0.2
  final double atrMultiple; // ATR止损倍数，默认2.0

  /// MACD+RSI共振策略
  final bool macdRsiEnabled;

  /// MA20/60金叉死叉策略
  final bool maCrossEnabled;

  /// 趋势过滤
  final bool trendFilterEnabled;

  /// 交易风格
  final TradingStyle defaultStyle;

  /// 通知设置
  final bool notificationEnabled;
  final bool notifyGoldenCross;
  final bool notifyOversold;
  final bool notifyBreakout;
  final bool notifyBuyZone;
  final bool notifyStopLoss;
  final bool notifyTakeProfit;

  const StrategyConfig({
    this.maVolumeEnabled = true,
    this.maShort = 5,
    this.maLong = 20,
    this.volumeThreshold = 1.5,
    this.turtleTradeEnabled = true,
    this.breakPeriod = 20,
    this.newHighPeriod = 60,
    this.meanReversionEnabled = true,
    this.rsiPeriod = 14,
    this.rsiOverbought = 70,
    this.rsiOversold = 30,
    this.volumePriceEnabled = true,
    this.volumeSurgeThreshold = 3.0,
    this.trendFilterEnabled = true,
    this.maCrossEnabled = true,
    this.defaultStyle = TradingStyle.aggressive,
    this.notificationEnabled = true,
    this.notifyGoldenCross = true,
    this.notifyOversold = true,
    this.notifyBreakout = true,
    this.notifyBuyZone = true,
    this.notifyStopLoss = true,
    this.notifyTakeProfit = true,
    this.atrChannelEnabled = true,
    this.atrTrendPeriod = 30,
    this.atrChannelWindow = 20,
    this.atrExitScore = 0.2,
    this.atrMultiple = 2.0,
    this.macdRsiEnabled = true,
  });

  Map<String, dynamic> toJson() => {
        'maVolumeEnabled': maVolumeEnabled,
        'maShort': maShort,
        'maLong': maLong,
        'volumeThreshold': volumeThreshold,
        'turtleTradeEnabled': turtleTradeEnabled,
        'breakPeriod': breakPeriod,
        'newHighPeriod': newHighPeriod,
        'meanReversionEnabled': meanReversionEnabled,
        'rsiPeriod': rsiPeriod,
        'rsiOverbought': rsiOverbought,
        'rsiOversold': rsiOversold,
        'volumePriceEnabled': volumePriceEnabled,
        'volumeSurgeThreshold': volumeSurgeThreshold,
        'trendFilterEnabled': trendFilterEnabled,
        'notificationEnabled': notificationEnabled,
        'notifyGoldenCross': notifyGoldenCross,
        'notifyOversold': notifyOversold,
        'notifyBreakout': notifyBreakout,
        'notifyBuyZone': notifyBuyZone,
        'notifyStopLoss': notifyStopLoss,
        'notifyTakeProfit': notifyTakeProfit,
        'atrChannelEnabled': atrChannelEnabled,
        'atrTrendPeriod': atrTrendPeriod,
        'atrChannelWindow': atrChannelWindow,
        'atrExitScore': atrExitScore,
        'atrMultiple': atrMultiple,
        'macdRsiEnabled': macdRsiEnabled,
        'maCrossEnabled': maCrossEnabled,
        'defaultStyle': defaultStyle.name,
      };

  factory StrategyConfig.fromJson(Map<String, dynamic> json) => StrategyConfig(
        maVolumeEnabled: json['maVolumeEnabled'] as bool? ?? true,
        maShort: json['maShort'] as int? ?? 5,
        maLong: json['maLong'] as int? ?? 20,
        volumeThreshold: (json['volumeThreshold'] as num?)?.toDouble() ?? 1.5,
        turtleTradeEnabled: json['turtleTradeEnabled'] as bool? ?? true,
        breakPeriod: json['breakPeriod'] as int? ?? 20,
        newHighPeriod: json['newHighPeriod'] as int? ?? 60,
        meanReversionEnabled: json['meanReversionEnabled'] as bool? ?? true,
        rsiPeriod: json['rsiPeriod'] as int? ?? 14,
        rsiOverbought: (json['rsiOverbought'] as num?)?.toDouble() ?? 70,
        rsiOversold: (json['rsiOversold'] as num?)?.toDouble() ?? 30,
        volumePriceEnabled: json['volumePriceEnabled'] as bool? ?? true,
        volumeSurgeThreshold: (json['volumeSurgeThreshold'] as num?)?.toDouble() ?? 3.0,
        trendFilterEnabled: json['trendFilterEnabled'] as bool? ?? true,
        notificationEnabled: json['notificationEnabled'] as bool? ?? true,
        notifyGoldenCross: json['notifyGoldenCross'] as bool? ?? true,
        notifyOversold: json['notifyOversold'] as bool? ?? true,
        notifyBreakout: json['notifyBreakout'] as bool? ?? true,
        notifyBuyZone: json['notifyBuyZone'] as bool? ?? true,
        notifyStopLoss: json['notifyStopLoss'] as bool? ?? true,
        notifyTakeProfit: json['notifyTakeProfit'] as bool? ?? true,
        atrChannelEnabled: json['atrChannelEnabled'] as bool? ?? true,
        atrTrendPeriod: json['atrTrendPeriod'] as int? ?? 30,
        atrChannelWindow: json['atrChannelWindow'] as int? ?? 20,
        atrExitScore: (json['atrExitScore'] as num?)?.toDouble() ?? 0.2,
        atrMultiple: (json['atrMultiple'] as num?)?.toDouble() ?? 2.0,
        macdRsiEnabled: json['macdRsiEnabled'] as bool? ?? true,
        maCrossEnabled: json['maCrossEnabled'] as bool? ?? true,
        defaultStyle: json['defaultStyle'] != null
            ? TradingStyle.values.firstWhere((e) => e.name == json['defaultStyle'],
                orElse: () => TradingStyle.aggressive)
            : TradingStyle.aggressive,
      );

  StrategyConfig copyWith({
    bool? maVolumeEnabled,
    int? maShort,
    int? maLong,
    double? volumeThreshold,
    bool? turtleTradeEnabled,
    int? breakPeriod,
    int? newHighPeriod,
    bool? meanReversionEnabled,
    int? rsiPeriod,
    double? rsiOverbought,
    double? rsiOversold,
    bool? volumePriceEnabled,
    double? volumeSurgeThreshold,
    bool? trendFilterEnabled,
    bool? notificationEnabled,
    bool? notifyGoldenCross,
    bool? notifyOversold,
    bool? notifyBreakout,
    bool? atrChannelEnabled,
    int? atrTrendPeriod,
    int? atrChannelWindow,
    double? atrExitScore,
    double? atrMultiple,
    bool? macdRsiEnabled,
    bool? maCrossEnabled,
    bool? notifyBuyZone,
    bool? notifyStopLoss,
    bool? notifyTakeProfit,
    TradingStyle? defaultStyle,
  }) {
    return StrategyConfig(
      maVolumeEnabled: maVolumeEnabled ?? this.maVolumeEnabled,
      maShort: maShort ?? this.maShort,
      maLong: maLong ?? this.maLong,
      volumeThreshold: volumeThreshold ?? this.volumeThreshold,
      turtleTradeEnabled: turtleTradeEnabled ?? this.turtleTradeEnabled,
      breakPeriod: breakPeriod ?? this.breakPeriod,
      newHighPeriod: newHighPeriod ?? this.newHighPeriod,
      meanReversionEnabled: meanReversionEnabled ?? this.meanReversionEnabled,
      rsiPeriod: rsiPeriod ?? this.rsiPeriod,
      rsiOverbought: rsiOverbought ?? this.rsiOverbought,
      rsiOversold: rsiOversold ?? this.rsiOversold,
      volumePriceEnabled: volumePriceEnabled ?? this.volumePriceEnabled,
      volumeSurgeThreshold: volumeSurgeThreshold ?? this.volumeSurgeThreshold,
      trendFilterEnabled: trendFilterEnabled ?? this.trendFilterEnabled,
      notificationEnabled: notificationEnabled ?? this.notificationEnabled,
      notifyGoldenCross: notifyGoldenCross ?? this.notifyGoldenCross,
      notifyOversold: notifyOversold ?? this.notifyOversold,
      notifyBreakout: notifyBreakout ?? this.notifyBreakout,
      atrChannelEnabled: atrChannelEnabled ?? this.atrChannelEnabled,
      atrTrendPeriod: atrTrendPeriod ?? this.atrTrendPeriod,
      atrChannelWindow: atrChannelWindow ?? this.atrChannelWindow,
      atrExitScore: atrExitScore ?? this.atrExitScore,
      atrMultiple: atrMultiple ?? this.atrMultiple,
      macdRsiEnabled: macdRsiEnabled ?? this.macdRsiEnabled,
      maCrossEnabled: maCrossEnabled ?? this.maCrossEnabled,
      notifyBuyZone: notifyBuyZone ?? this.notifyBuyZone,
      notifyStopLoss: notifyStopLoss ?? this.notifyStopLoss,
      notifyTakeProfit: notifyTakeProfit ?? this.notifyTakeProfit,
      defaultStyle: defaultStyle ?? this.defaultStyle,
    );
  }
}
