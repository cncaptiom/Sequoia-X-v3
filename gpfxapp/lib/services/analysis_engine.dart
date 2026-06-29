import 'dart:math';
import '../models/analysis_result.dart';
import '../models/kline_data.dart';
import '../models/stock_quote.dart';
import '../models/strategy_config.dart';

class AnalysisEngine {
  final StrategyConfig config;

  AnalysisEngine({StrategyConfig? config}) : config = config ?? const StrategyConfig();

  /// 执行完整分析
  AnalysisResult analyze({
    required StockQuote quote,
    required List<KLineData> klineData,
  }) {
    if (klineData.length < config.maLong) {
      return AnalysisResult(
        rating: Rating.attention,
        totalScore: 0,
        strategyScores: {},
        trendPassed: false,
        trendDescription: '数据不足（需要至少${config.maLong}根K线）',
        riskRewardRatio: 0,
        signals: [],
        time: DateTime.now(),
      );
    }

    final signals = <Signal>[];
    final strategyScores = <String, double>{};
    final closes = klineData.map((k) => k.close).toList();
    final volumes = klineData.map((k) => k.volume).toList();
    final highs = klineData.map((k) => k.high).toList();
    final lows = klineData.map((k) => k.low).toList();

    // ===== 第一步：趋势过滤 =====
    final trendResult = _checkTrend(closes);
    signals.addAll(trendResult.signals);

    // ===== 第二步：多策略打分 =====
    double totalScore = 0;

    // 动态权重分配：从StrategyConfig读取（PC端定期同步，无PC数据时用默认值）
    final weights = <String, double>{};
    if (config.maVolumeEnabled) weights['maVolume'] = config.weightMaVolume;
    if (config.turtleTradeEnabled) weights['turtleTrade'] = config.weightTurtleTrade;
    if (config.meanReversionEnabled) weights['meanReversion'] = config.weightMeanReversion;
    if (config.volumePriceEnabled) weights['volumePrice'] = config.weightVolumePrice;
    if (config.atrChannelEnabled) weights['atrChannel'] = config.weightAtrChannel;
    if (config.macdRsiEnabled) weights['macdRsi'] = config.weightMacdRsi;
    if (config.maCrossEnabled) weights['maCross'] = config.weightMaCross;
    if (config.kdjEnabled) weights['kdj'] = config.weightKdj;

    // 归一化权重总和为1.0
    if (weights.isNotEmpty) {
      final totalWeight = weights.values.fold(0.0, (a, b) => a + b);
      for (final key in weights.keys.toList()) {
        weights[key] = weights[key]! / totalWeight;
      }
    }

    // 策略A: MaVolume (均线金叉+放量)
    if (config.maVolumeEnabled && weights.containsKey('maVolume')) {
      final result = _maVolumeStrategy(closes, volumes);
      strategyScores['maVolume'] = result.score;
      totalScore += result.score * weights['maVolume']!;
      signals.addAll(result.signals);
    }

    // 策略B: TurtleTrade (突破+趋势强度)
    if (config.turtleTradeEnabled && weights.containsKey('turtleTrade')) {
      final result = _turtleTradeStrategy(closes, highs, lows, volumes);
      strategyScores['turtleTrade'] = result.score;
      totalScore += result.score * weights['turtleTrade']!;
      signals.addAll(result.signals);
    }

    // 策略C: 均值回归
    if (config.meanReversionEnabled && weights.containsKey('meanReversion')) {
      final result = _meanReversionStrategy(closes, lows, highs);
      strategyScores['meanReversion'] = result.score;
      totalScore += result.score * weights['meanReversion']!;
      signals.addAll(result.signals);
    }

    // 策略D: 量价关系
    if (config.volumePriceEnabled && weights.containsKey('volumePrice')) {
      final result = _volumePriceStrategy(closes, volumes);
      strategyScores['volumePrice'] = result.score;
      totalScore += result.score * weights['volumePrice']!;
      signals.addAll(result.signals);
    }

    // 策略E: ATR通道突破（进攻型）
    if (config.atrChannelEnabled && weights.containsKey('atrChannel')) {
      final result = _atrChannelStrategy(closes, highs, lows);
      strategyScores['atrChannel'] = result.score;
      totalScore += result.score * weights['atrChannel']!;
      signals.addAll(result.signals);
    }

    // 策略F: MACD+RSI共振
    if (config.macdRsiEnabled && weights.containsKey('macdRsi')) {
      final result = _macdRsiStrategy(closes, volumes);
      strategyScores['macdRsi'] = result.score;
      totalScore += result.score * weights['macdRsi']!;
      signals.addAll(result.signals);
    }

    // 策略G: MA20/60金叉死叉
    if (config.maCrossEnabled && weights.containsKey('maCross')) {
      final result = _maCrossStrategy(closes);
      strategyScores['maCross'] = result.score;
      totalScore += result.score * weights['maCross']!;
      signals.addAll(result.signals);
    }

    // 策略H: KDJ 金叉/超卖
    if (config.kdjEnabled && weights.containsKey('kdj')) {
      final result = _kdjStrategy(closes, highs, lows);
      strategyScores['kdj'] = result.score;
      totalScore += result.score * weights['kdj']!;
      signals.addAll(result.signals);
    }

    // ===== 第三步：盈亏比计算 =====
    final trendPassed = trendResult.passed && config.trendFilterEnabled;
    final support = _findSupport(lows, closes.last);
    final resistance = _findResistance(highs, closes.last);

    // 止损位不能高于现价95%，止盈位不能低于现价103%
    final stopLoss = min(support * (1 - 0.03), closes.last * 0.9);
    final takeProfit = max(resistance, closes.last * 1.03);

    // 只有止盈>现价>止损时盈亏比才有意义
    final riskReward = takeProfit > closes.last && closes.last > stopLoss
        ? (takeProfit - closes.last) / (closes.last - stopLoss)
        : 0.0;

    // 买入区间：仅当现价接近支撑位时显示（现价在支撑位15%以内）
    final nearSupport = closes.last <= support * 1.15;
    final buyRangeText = trendPassed && totalScore > 0 && nearSupport
        ? '${support.toStringAsFixed(2)} - ${(support * 1.02).toStringAsFixed(2)}'
        : null;

    // ===== 综合评级 =====
    final rating = _calculateRating(trendPassed, totalScore, riskReward);

    return AnalysisResult(
      rating: rating,
      totalScore: totalScore,
      strategyScores: strategyScores,
      trendPassed: trendPassed,
      trendDescription: trendResult.description,
      riskRewardRatio: riskReward,
      stopLossPrice: double.parse(stopLoss.toStringAsFixed(2)),
      takeProfitPrice: double.parse(takeProfit.toStringAsFixed(2)),
      buyRange: buyRangeText,
      signals: signals,
      time: DateTime.now(),
    );
  }

  // ==================== 趋势过滤 ====================

  _TrendResult _checkTrend(List<double> closes) {
    final mas = _calcMA(closes);
    final ma5 = mas[mas.length - 1];
    final ma10 = mas.length > 9 ? _calcMA(closes, 10).last : 0.0;
    final ma20 = mas.length > 19 ? _calcMA(closes, 20).last : 0.0;
    final ma60 = mas.length > 59 ? _calcMA(closes, 60).last : 0.0;
    final signals = <Signal>[];
    final conditions = <String>[];
    bool passed = true;

    // 条件1：均线多头排列 MA5 > MA10 > MA20 > MA60
    if (ma5 > ma10 && ma10 > ma20 && ma20 > ma60) {
      conditions.add('均线多头排列 ✓');
      signals.add(Signal(
        name: '趋势-均线排列',
        type: SignalType.bullish,
        description: 'MA5(${ma5.toStringAsFixed(2)}) > MA10(${ma10.toStringAsFixed(2)}) > MA20(${ma20.toStringAsFixed(2)}) > MA60(${ma60.toStringAsFixed(2)})',
        score: 0,
      ));
    } else {
      conditions.add('均线非多头排列');
      passed = false;
      signals.add(Signal(
        name: '趋势-均线排列',
        type: SignalType.bearish,
        description: '均线未形成多头排列',
        score: 0,
      ));
    }

    // 条件2：MA20方向向上
    final ma20Prev = mas.length > 20 ? _calcMA(closes, 20)[mas.length - 21] : 0.0;
    if (ma20 > ma20Prev) {
      conditions.add('MA20上翘 ✓');
    } else {
      conditions.add('MA20走平/下弯');
      passed = false;
    }

    // 条件3：价格在MA20上方
    final lastClose = closes.last;
    if (lastClose > ma20) {
      conditions.add('价格在MA20之上 ✓');
      signals.add(Signal(
        name: '趋势-MA20支撑',
        type: SignalType.bullish,
        description: '收盘价${lastClose.toStringAsFixed(2)} 在 MA20(${ma20.toStringAsFixed(2)}) 之上',
        score: 0,
      ));
    } else {
      conditions.add('价格跌破MA20');
      passed = false;
    }

    return _TrendResult(
      passed: passed,
      description: conditions.join(' | '),
      signals: signals,
    );
  }

  // ==================== 策略A: MaVolume ====================

  _StrategyResult _maVolumeStrategy(List<double> closes, List<double> volumes) {
    final score = _ScoreCounter();
    final signals = <Signal>[];
    final maShort = _calcMA(closes, config.maShort);
    final maLong = _calcMA(closes, config.maLong);
    final avgVolume = _calcAvgVolume(volumes, config.maLong);
    final lastVol = volumes.last;
    final lastIdx = closes.length - 1;

    // 检查金叉/死叉
    final maShortNow = maShort[lastIdx];
    final maShortPrev = maShort[lastIdx - 1];
    final maLongNow = maLong[lastIdx];
    final maLongPrev = maLong[lastIdx - 1];

    if (maShortPrev <= maLongPrev && maShortNow > maLongNow) {
      // 金叉
      if (lastVol > avgVolume * config.volumeThreshold) {
        score.add(3);
        signals.add(Signal(
          name: 'MaVolume-金叉放量',
          type: SignalType.bullish,
          description: 'MA${config.maShort}(${maShortNow.toStringAsFixed(2)})上穿MA${config.maLong}(${maLongNow.toStringAsFixed(2)})，成交量${(lastVol / avgVolume).toStringAsFixed(1)}倍均量',
          score: 3,
        ));
      } else {
        score.add(1);
        signals.add(Signal(
          name: 'MaVolume-金叉',
          type: SignalType.bullish,
          description: 'MA${config.maShort}上穿MA${config.maLong}，但量能不足',
          score: 1,
        ));
      }
    } else if (maShortPrev >= maLongPrev && maShortNow < maLongNow) {
      // 死叉
      score.add(-3);
      signals.add(Signal(
        name: 'MaVolume-死叉',
        type: SignalType.bearish,
        description: 'MA${config.maShort}下穿MA${config.maLong}，死叉信号',
        score: -3,
      ));
    }

    // 均线多头排列加分
    if (maShortNow > maLongNow) {
      score.add(2);
      signals.add(Signal(
        name: 'MaVolume-多头排列',
        type: SignalType.bullish,
        description: '短期均线在长期均线上方，多头排列',
        score: 2,
      ));
    }

    // 价格在MA20上方
    final ma20 = _calcMA(closes, 20).last;
    if (closes.last > ma20) {
      score.add(1);
    }

    return _StrategyResult(score: score.total, signals: signals);
  }

  // ==================== 策略B: TurtleTrade ====================

  _StrategyResult _turtleTradeStrategy(
      List<double> closes, List<double> highs, List<double> lows, List<double> volumes) {
    final score = _ScoreCounter();
    final signals = <Signal>[];
    final lastClose = closes.last;
    final avgVolume = _calcAvgVolume(volumes, config.breakPeriod);
    final lastVol = volumes.last;

    // 20日最高/最低
    final recentHigh = highs.sublist(max(0, highs.length - config.breakPeriod)).reduce(max);
    final recentLow = lows.sublist(max(0, lows.length - config.breakPeriod)).reduce(min);
    final high60 = highs.sublist(max(0, highs.length - config.newHighPeriod)).reduce(max);

    // 突破前高
    if (lastClose >= recentHigh) {
      if (lastVol > avgVolume * config.volumeThreshold) {
        score.add(3);
        signals.add(Signal(
          name: 'Turtle-放量突破',
          type: SignalType.bullish,
          description: '价格突破${config.breakPeriod}日高点(${recentHigh.toStringAsFixed(2)})，成交量放大',
          score: 3,
        ));
      } else {
        score.add(1);
      }
    }

    // 跌破前低
    if (lastClose <= recentLow) {
      score.add(-3);
      signals.add(Signal(
        name: 'Turtle-破位',
        type: SignalType.bearish,
        description: '价格跌破${config.breakPeriod}日低点(${recentLow.toStringAsFixed(2)})',
        score: -3,
      ));
    }

    // 创60日新高
    if (lastClose >= high60) {
      score.add(2);
      signals.add(Signal(
        name: 'Turtle-60日新高',
        type: SignalType.bullish,
        description: '价格创60日新高，强势信号',
        score: 2,
      ));
    }

    return _StrategyResult(score: score.total, signals: signals);
  }

  // ==================== 策略C: 均值回归 ====================

  _StrategyResult _meanReversionStrategy(
      List<double> closes, List<double> lows, List<double> highs) {
    final score = _ScoreCounter();
    final signals = <Signal>[];
    final lastClose = closes.last;

    // 布林带计算
    final ma20 = _calcMA(closes, 20).last;
    final std20 = _calcStd(closes, 20);
    final upperBand = ma20 + 2 * std20;
    final lowerBand = ma20 - 2 * std20;

    // RSI计算
    final rsi = _calcRSI(closes, config.rsiPeriod);

    // 触及下轨 + 超卖
    if (lastClose <= lowerBand && rsi < config.rsiOversold) {
      score.add(3);
      signals.add(Signal(
        name: '超卖-下轨反弹',
        type: SignalType.bullish,
        description: '价格触及布林下轨(${lowerBand.toStringAsFixed(2)})，RSI(${rsi.toStringAsFixed(1)})超卖',
        score: 3,
      ));
    } else if (lastClose <= lowerBand) {
      score.add(1);
      signals.add(Signal(
        name: '布林-触及下轨',
        type: SignalType.bullish,
        description: '价格触及布林下轨(${lowerBand.toStringAsFixed(2)})',
        score: 1,
      ));
    }

    // 触及上轨 + 超买
    if (lastClose >= upperBand && rsi > config.rsiOverbought) {
      score.add(-2);
      signals.add(Signal(
        name: '超买-上轨风险',
        type: SignalType.bearish,
        description: '价格触及布林上轨(${upperBand.toStringAsFixed(2)})，RSI(${rsi.toStringAsFixed(1)})超买',
        score: -2,
      ));
    }

    // RSI背离检测
    if (closes.length >= 30) {
      // 底背离：价格新低 + RSI没新低
      final lowestClose = closes.sublist(closes.length - 20).reduce(min);
      final rsiValues = List.generate(20, (i) => _calcRSI(closes, config.rsiPeriod, closes.length - 20 + i));
      final lowestRsi = rsiValues.reduce(min);

      if (lastClose <= lowestClose && rsi > lowestRsi + 5) {
        score.add(3);
        signals.add(Signal(
          name: 'RSI-底背离',
          type: SignalType.bullish,
          description: '价格新低但RSI未同步新低，底背离信号',
          score: 3,
        ));
      }

      // 顶背离：价格新高 + RSI没新高
      final highestClose = closes.sublist(closes.length - 20).reduce(max);
      final highestRsi = rsiValues.reduce(max);

      if (lastClose >= highestClose && rsi < highestRsi - 5) {
        score.add(-3);
        signals.add(Signal(
          name: 'RSI-顶背离',
          type: SignalType.bearish,
          description: '价格新高但RSI未同步新高，顶背离信号',
          score: -3,
        ));
      }
    }

    return _StrategyResult(score: score.total, signals: signals);
  }

  // ==================== 策略D: 量价关系 ====================

  _StrategyResult _volumePriceStrategy(List<double> closes, List<double> volumes) {
    final score = _ScoreCounter();
    final signals = <Signal>[];
    final avgVolume = _calcAvgVolume(volumes, config.maLong);
    final lastVol = volumes.last;
    final volRatio = lastVol / avgVolume;
    final lastIdx = volumes.length - 1;
    final prevClose = closes[lastIdx - 1];

    // 突破放量
    if (closes.last > prevClose * 1.02) {
      if (volRatio > config.volumeThreshold) {
        score.add(2);
        signals.add(Signal(
          name: '量价-放量上涨',
          type: SignalType.bullish,
          description: '价格上涨，成交量${volRatio.toStringAsFixed(1)}倍均量，量价配合',
          score: 2,
        ));
      }
    }

    // 回调缩量
    if (closes.last < prevClose && volRatio < 1.0) {
      score.add(1);
      signals.add(Signal(
        name: '量价-缩量回调',
        type: SignalType.bullish,
        description: '价格回调但缩量，回调健康',
        score: 1,
      ));
    }

    // 价创新高量没创新高
    if (closes.length >= 10) {
      final high10 = closes.sublist(closes.length - 10).reduce(max);
      final vol10 = volumes.sublist(volumes.length - 10).reduce(max);
      if (closes.last >= high10 && lastVol < vol10 * 0.8) {
        score.add(-2);
        signals.add(Signal(
          name: '量价-量价背离',
          type: SignalType.bearish,
          description: '价格创新高但成交量萎缩，上涨动力不足',
          score: -2,
        ));
      }
    }

    // 成交量异常放大
    if (volRatio > config.volumeSurgeThreshold) {
      score.add(-1);
      signals.add(Signal(
        name: '量价-异常放量',
        type: SignalType.neutral,
        description: '成交量异常放大(${volRatio.toStringAsFixed(1)}倍)，警惕派发',
        score: -1,
      ));
    }

    return _StrategyResult(score: score.total, signals: signals);
  }

  // ==================== 策略E: ATR通道突破（进攻型） ====================

  _StrategyResult _atrChannelStrategy(
      List<double> closes, List<double> highs, List<double> lows) {
    final score = _ScoreCounter();
    final signals = <Signal>[];
    final period = config.atrTrendPeriod;
    final n = closes.length;
    if (n < period + 5) return _StrategyResult(score: 0, signals: signals);

    // 计算EMA
    final ema = _calcEMA(closes, period);
    final currentEma = ema.last;
    final prevEma = ema[n - 2];

    // 计算ATR
    final atr = _calcATR(highs, lows, closes, period);
    final currentAtr = atr.last;
    if (currentAtr <= 0) return _StrategyResult(score: 0, signals: signals);

    // 趋势评分 = (收盘价 - EMA) / ATR
    final lastClose = closes.last;
    final trendScore = (lastClose - currentEma) / currentAtr;

    // EMA斜率（判断趋势方向）
    final emaSlope = (currentEma - prevEma) / prevEma;

    // 方向判定
    final isBullish = trendScore >= 1.0 && emaSlope > 0;
    final isBearish = trendScore <= -1.0 && emaSlope < 0;
    final hasTrend = isBullish || isBearish;

    // 通道上下轨（最高价/最低价的 channelWindow 周期）
    final channelWin = config.atrChannelWindow;
    final upperChannel = highs.sublist(max(0, n - channelWin)).reduce(max);
    final lowerChannel = lows.sublist(max(0, n - channelWin)).reduce(min);

    // 价格突破通道
    final brokeUpper = lastClose >= upperChannel;
    final brokeLower = lastClose <= lowerChannel;

    // 评分逻辑
    if (isBullish && brokeUpper) {
      // 多头趋势 + 突破通道上轨 = 强买入信号
      score.add(3);
      signals.add(Signal(
        name: 'ATR-多头突破',
        type: SignalType.bullish,
        description: 'ATR趋势评分${trendScore.toStringAsFixed(1)}，突破${channelWin}日通道上轨${upperChannel.toStringAsFixed(2)}',
        score: 3,
      ));
    } else if (isBullish) {
      // 多头趋势但未突破
      score.add(1);
      signals.add(Signal(
        name: 'ATR-多头趋势',
        type: SignalType.bullish,
        description: 'ATR趋势评分${trendScore.toStringAsFixed(1)}，EMA向上，等待突破',
        score: 1,
      ));
    } else if (isBearish && brokeLower) {
      // 空头趋势 + 跌破通道下轨
      score.add(-3);
      signals.add(Signal(
        name: 'ATR-空头破位',
        type: SignalType.bearish,
        description: 'ATR趋势评分${trendScore.toStringAsFixed(1)}，跌破${channelWin}日通道下轨${lowerChannel.toStringAsFixed(2)}',
        score: -3,
      ));
    } else if (isBearish) {
      score.add(-1);
      signals.add(Signal(
        name: 'ATR-空头趋势',
        type: SignalType.bearish,
        description: 'ATR趋势评分${trendScore.toStringAsFixed(1)}，EMA向下',
        score: -1,
      ));
    }

    // 趋势过热反转信号（评分 > 3.0 或有顶背离）
    if (trendScore > 3.0) {
      score.add(-1);
      signals.add(Signal(
        name: 'ATR-趋势过热',
        type: SignalType.neutral,
        description: 'ATR趋势评分${trendScore.toStringAsFixed(1)} > 3.0，短期过热注意回调',
        score: -1,
      ));
    } else if (trendScore < -3.0) {
      score.add(1);
      signals.add(Signal(
        name: 'ATR-超跌反弹',
        type: SignalType.bullish,
        description: 'ATR趋势评分${trendScore.toStringAsFixed(1)} < -3.0，超跌反弹机会',
        score: 1,
      ));
    }

    // 趋势减弱（评分从高位回落）
    if (n >= period + 2) {
      final prevScore = (closes[n - 2] - ema[n - 2]) / (atr[n - 2] > 0 ? atr[n - 2] : 1);
      if (prevScore >= 1.5 && trendScore < config.atrExitScore) {
        score.add(-2);
        signals.add(Signal(
          name: 'ATR-趋势减弱',
          type: SignalType.bearish,
          description: 'ATR评分从${prevScore.toStringAsFixed(1)}回落至${trendScore.toStringAsFixed(1)}，趋势动能衰减',
          score: -2,
        ));
      }
    }

    // 无趋势/震荡扣分
    if (!hasTrend && (trendScore.abs() < 0.3)) {
      score.add(-1);
    }

    return _StrategyResult(score: score.total, signals: signals);
  }

  // ==================== 策略F: MACD+RSI共振 ====================

  _StrategyResult _macdRsiStrategy(List<double> closes, List<double> volumes) {
    final score = _ScoreCounter();
    final signals = <Signal>[];
    final n = closes.length;
    if (n < 35) return _StrategyResult(score: 0, signals: signals);

    // 计算MACD
    final ema12 = _calcEMA(closes, 12);
    final ema26 = _calcEMA(closes, 26);
    final dif = List.generate(n, (i) => ema12[i] - ema26[i]);
    final dea = _calcEMA(dif, 9);
    final macd = List.generate(n, (i) => (dif[i] - dea[i]) * 2);

    final lastIdx = n - 1;
    final difNow = dif[lastIdx];
    final deaNow = dea[lastIdx];
    final difPrev = dif[lastIdx - 1];
    final deaPrev = dea[lastIdx - 1];
    final macdNow = macd[lastIdx];
    final macdPrev = macd[lastIdx - 1];

    // RSI
    final rsi = _calcRSI(closes, 14);

    // 均量
    final avgVolume = _calcAvgVolume(volumes, 20);
    final lastVol = volumes.last;

    // 1. DIF上穿DEA（金叉）
    if (difPrev <= deaPrev && difNow > deaNow) {
      if (rsi < 60 && lastVol > avgVolume * 1.5) {
        score.add(3);
        signals.add(Signal(
          name: 'MACD-金叉放量',
          type: SignalType.bullish,
          description: 'DIF上穿DEA，RSI(${rsi.toStringAsFixed(1)})健康，成交放量',
          score: 3,
        ));
      } else {
        score.add(2);
        signals.add(Signal(
          name: 'MACD-金叉',
          type: SignalType.bullish,
          description: 'DIF上穿DEA，MACD金叉',
          score: 2,
        ));
      }
    }

    // 2. DIF下穿DEA（死叉）
    if (difPrev >= deaPrev && difNow < deaNow) {
      score.add(-3);
      signals.add(Signal(
        name: 'MACD-死叉',
        type: SignalType.bearish,
        description: 'DIF下穿DEA，MACD死叉',
        score: -3,
      ));
    }

    // 3. RSI超卖 + MACD绿柱缩短（底背离反弹）
    if (rsi < 30 && macdPrev < 0 && macdNow > macdPrev) {
      score.add(2);
      signals.add(Signal(
        name: 'MACD-RSI底背离',
        type: SignalType.bullish,
        description: 'RSI(${rsi.toStringAsFixed(1)})超卖，MACD绿柱缩短',
        score: 2,
      ));
    }

    // 4. RSI超买 + MACD红柱缩短（顶背离回调）
    if (rsi > 70 && macdPrev > 0 && macdNow < macdPrev) {
      score.add(-2);
      signals.add(Signal(
        name: 'MACD-RSI顶背离',
        type: SignalType.bearish,
        description: 'RSI(${rsi.toStringAsFixed(1)})超买，MACD红柱缩短',
        score: -2,
      ));
    }

    // 5. DIF在零轴上方（多头市场）
    if (difNow > 0) {
      score.add(1);
    } else {
      score.add(-1);
    }

    return _StrategyResult(score: score.total, signals: signals);
  }

  // ==================== 策略G: MA20/60金叉死叉 ====================

  _StrategyResult _maCrossStrategy(List<double> closes) {
    final score = _ScoreCounter();
    final signals = <Signal>[];
    final n = closes.length;
    if (n < 61) return _StrategyResult(score: 0, signals: signals);

    final ma20 = _calcMA(closes, 20);
    final ma60 = _calcMA(closes, 60);
    final lastIdx = n - 1;

    final ma20Now = ma20[lastIdx];
    final ma60Now = ma60[lastIdx];
    final ma20Prev = ma20[lastIdx - 1];
    final ma60Prev = ma60[lastIdx - 1];
    final ma60PrevPrev = ma60.length > 2 ? ma60[lastIdx - 2] : ma60Now;

    // 1. MA20上穿MA60（金叉）
    if (ma20Prev <= ma60Prev && ma20Now > ma60Now) {
      score.add(3);
      signals.add(Signal(
        name: 'MA20/60-金叉',
        type: SignalType.bullish,
        description: 'MA20(${ma20Now.toStringAsFixed(2)})上穿MA60(${ma60Now.toStringAsFixed(2)})',
        score: 3,
      ));
    }

    // 2. MA20下穿MA60（死叉）
    if (ma20Prev >= ma60Prev && ma20Now < ma60Now) {
      score.add(-3);
      signals.add(Signal(
        name: 'MA20/60-死叉',
        type: SignalType.bearish,
        description: 'MA20(${ma20Now.toStringAsFixed(2)})下穿MA60(${ma60Now.toStringAsFixed(2)})',
        score: -3,
      ));
    }

    // 3. 价格在MA20和MA60之上
    final lastClose = closes.last;
    if (lastClose > ma20Now && lastClose > ma60Now) {
      score.add(1);
    }

    // 4. MA60方向向上
    if (ma60Now > ma60PrevPrev) {
      score.add(1);
      signals.add(Signal(
        name: 'MA60-趋势向上',
        type: SignalType.bullish,
        description: 'MA60(${ma60Now.toStringAsFixed(2)})方向向上，中长期看多',
        score: 1,
      ));
    }

    // 5. MA20在MA60之上（多头排列）
    if (ma20Now > ma60Now) {
      score.add(1);
    }

    return _StrategyResult(score: score.total, signals: signals);
  }

  // ==================== 策略H: KDJ ====================

  _StrategyResult _kdjStrategy(
      List<double> closes, List<double> highs, List<double> lows) {
    final score = _ScoreCounter();
    final signals = <Signal>[];
    final n = closes.length;
    if (n < 30) return _StrategyResult(score: 0, signals: signals);

    final period = config.kdjKPeriod;

    // 计算 RSV
    final rsv = List<double>.generate(n, (i) {
      if (i < period - 1) return 50.0;
      final lowMin = lows.sublist(i - period + 1, i + 1).reduce(min);
      final highMax = highs.sublist(i - period + 1, i + 1).reduce(max);
      final denom = highMax - lowMin;
      if (denom <= 0) return 50.0;
      return (closes[i] - lowMin) / denom * 100;
    });

    // K = SMA(RSV, d_period), D = SMA(K, j_period)
    final k = _calcSMA(rsv, config.kdjDPeriod);
    final d = _calcSMA(k, config.kdjJPeriod);
    final j = List.generate(n, (i) => 3 * k[i] - 2 * d[i]);

    final kNow = k.last;
    final kPrev = k[n - 2];
    final dNow = d.last;
    final dPrev = d[n - 2];
    final jNow = j.last;

    // 1. K 上穿 D（金叉）
    if (kPrev <= dPrev && kNow > dNow) {
      if (kNow < 25) {
        score.add(2);
        signals.add(Signal(
          name: 'KDJ-超卖金叉',
          type: SignalType.bullish,
          description: 'K(${kNow.toStringAsFixed(1)})上穿D(${dNow.toStringAsFixed(1)})在超卖区',
          score: 2,
        ));
      } else if (kNow > 75) {
        score.add(0.5);
        signals.add(Signal(
          name: 'KDJ-高位金叉',
          type: SignalType.neutral,
          description: 'K(${kNow.toStringAsFixed(1)})上穿D(${dNow.toStringAsFixed(1)})但处于高位',
          score: 0.5,
        ));
      } else {
        score.add(1.5);
        signals.add(Signal(
          name: 'KDJ-金叉',
          type: SignalType.bullish,
          description: 'K(${kNow.toStringAsFixed(1)})上穿D(${dNow.toStringAsFixed(1)})',
          score: 1.5,
        ));
      }
    }

    // 2. K 下穿 D（死叉）
    if (kPrev >= dPrev && kNow < dNow) {
      if (kNow > 75) {
        score.add(-2);
        signals.add(Signal(
          name: 'KDJ-超买死叉',
          type: SignalType.bearish,
          description: 'K(${kNow.toStringAsFixed(1)})从高位下穿D(${dNow.toStringAsFixed(1)})',
          score: -2,
        ));
      } else {
        score.add(-1);
        signals.add(Signal(
          name: 'KDJ-死叉',
          type: SignalType.bearish,
          description: 'K(${kNow.toStringAsFixed(1)})下穿D(${dNow.toStringAsFixed(1)})',
          score: -1,
        ));
      }
    }

    // 3. 超卖区（辅助 meanReversion 确认）
    if (kNow < 20 && dNow < 20) {
      score.add(1);
      signals.add(Signal(
        name: 'KDJ-超卖',
        type: SignalType.bullish,
        description: 'K(${kNow.toStringAsFixed(1)}) D(${dNow.toStringAsFixed(1)})均在超卖区',
        score: 1,
      ));
    }

    // 4. 超买区
    if (kNow > 80 && dNow > 80) {
      score.add(-1);
      signals.add(Signal(
        name: 'KDJ-超买',
        type: SignalType.bearish,
        description: 'K(${kNow.toStringAsFixed(1)}) D(${dNow.toStringAsFixed(1)})均在超买区',
        score: -1,
      ));
    }

    // 5. J值极端
    if (jNow < 0) {
      score.add(1);
      signals.add(Signal(
        name: 'KDJ-J值负',
        type: SignalType.bullish,
        description: 'J值(${jNow.toStringAsFixed(1)})为负，极短线超卖',
        score: 1,
      ));
    }
    if (jNow > 100) {
      score.add(-1);
      signals.add(Signal(
        name: 'KDJ-J值>100',
        type: SignalType.bearish,
        description: 'J值(${jNow.toStringAsFixed(1)})超过100，极短线超买',
        score: -1,
      ));
    }

    // 6. KDJ与MA20共振（增强信号）
    if (n >= 20) {
      final ma20 = _calcMA(closes, 20).last;
      if (closes.last > ma20 && kNow > dNow) {
        score.add(1);
        signals.add(Signal(
          name: 'KDJ-均线共振',
          type: SignalType.bullish,
          description: '价格在MA20之上且KDJ金叉，趋势共振',
          score: 1,
        ));
      }
    }

    return _StrategyResult(score: score.total, signals: signals);
  }

  // ==================== 工具方法 ====================

  List<double> _calcMA(List<double> data, [int period = 5]) {
    final result = <double>[];
    for (int i = 0; i < data.length; i++) {
      if (i < period - 1) {
        result.add(data[i]);
      } else {
        double sum = 0;
        for (int j = i - period + 1; j <= i; j++) {
          sum += data[j];
        }
        result.add(sum / period);
      }
    }
    return result;
  }

  double _calcAvgVolume(List<double> volumes, int period) {
    if (volumes.length < period) return volumes.last;
    double sum = 0;
    for (int i = volumes.length - period; i < volumes.length; i++) {
      sum += volumes[i];
    }
    return sum / period;
  }

  /// 计算EMA
  List<double> _calcEMA(List<double> data, int period) {
    final result = <double>[];
    if (data.isEmpty) return result;
    // 首值用SMA
    final multiplier = 2.0 / (period + 1);
    double ema = data[0];
    result.add(ema);
    for (int i = 1; i < data.length; i++) {
      ema = (data[i] - ema) * multiplier + ema;
      result.add(ema);
    }
    return result;
  }

  /// 计算SMA（Simple Moving Average，用于KDJ的K/D值计算）
  List<double> _calcSMA(List<double> data, int period) {
    final result = <double>[];
    for (int i = 0; i < data.length; i++) {
      if (i < period - 1) {
        result.add(data[i]);
      } else {
        double sum = 0;
        for (int j = i - period + 1; j <= i; j++) {
          sum += data[j];
        }
        result.add(sum / period);
      }
    }
    return result;
  }

  /// 计算ATR（Average True Range）
  List<double> _calcATR(List<double> highs, List<double> lows, List<double> closes, int period) {
    final tr = <double>[];
    for (int i = 0; i < closes.length; i++) {
      if (i == 0) {
        tr.add(highs[i] - lows[i]);
      } else {
        final trueRange = max(highs[i] - lows[i], max(
          (highs[i] - closes[i - 1]).abs(),
          (lows[i] - closes[i - 1]).abs(),
        ));
        tr.add(trueRange);
      }
    }
    // ATR = SMA of TR
    final atr = <double>[];
    for (int i = 0; i < tr.length; i++) {
      if (i < period - 1) {
        atr.add(tr[i]);
      } else {
        double sum = 0;
        for (int j = i - period + 1; j <= i; j++) {
          sum += tr[j];
        }
        atr.add(sum / period);
      }
    }
    return atr;
  }

  double _calcStd(List<double> data, int period) {
    final slice = data.sublist(data.length - period);
    final mean = slice.reduce((a, b) => a + b) / period;
    final variance = slice.map((v) => pow(v - mean, 2)).reduce((a, b) => a + b) / period;
    return sqrt(variance);
  }

  double _calcRSI(List<double> closes, int period, [int? index]) {
    final end = index ?? closes.length - 1;
    final start = end - period;
    if (start < 0) return 50;

    double gain = 0, loss = 0;
    for (int i = start + 1; i <= end; i++) {
      final diff = closes[i] - closes[i - 1];
      if (diff > 0) {
        gain += diff;
      } else {
        loss -= diff;
      }
    }

    if (loss == 0) return 100;
    final rs = gain / loss;
    return 100 - (100 / (1 + rs));
  }

  double _findSupport(List<double> lows, double currentPrice) {
    // 寻找近期低点作为支撑，但不高于现价95%
    final recent = lows.sublist(max(0, lows.length - 20));
    recent.sort();
    final q1 = recent.length > 3 ? recent[recent.length ~/ 4] : currentPrice * 0.95;
    return min(q1, currentPrice * 0.95);
  }

  double _findResistance(List<double> highs, double currentPrice) {
    // 寻找近期高点作为压力，但不低于现价103%
    final recent = highs.sublist(max(0, highs.length - 20));
    recent.sort();
    if (recent.length < 3) return currentPrice * 1.05;
    final q3 = recent[recent.length * 3 ~/ 4]; // 75分位
    // 如果75分位低于现价，说明已突破，取近期最高点和现价103%的较大值
    return max(q3, currentPrice * 1.03);
  }

  Rating _calculateRating(bool trendPassed, double score, double riskReward) {
    if (!trendPassed) return Rating.avoid;
    if (score >= 6 && riskReward > 3) return Rating.strongBuy;
    if (score >= 4 && riskReward > 2) return Rating.buy;
    if (score >= 2) return Rating.watch;
    return Rating.attention;
  }
}

// ==================== 内部类 ====================

class _TrendResult {
  final bool passed;
  final String description;
  final List<Signal> signals;
  _TrendResult({
    required this.passed,
    required this.description,
    required this.signals,
  });
}

class _StrategyResult {
  final double score;
  final List<Signal> signals;
  _StrategyResult({required this.score, required this.signals});
}

class _ScoreCounter {
  double _total = 0;
  double get total => _total;
  void add(double value) => _total += value;
}
