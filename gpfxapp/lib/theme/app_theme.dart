import 'package:flutter/material.dart';
import '../models/analysis_result.dart';

class AppTheme {
  // 天空蓝主色
  static const Color primaryColor = Color(0xFF87CEEB);
  static const Color primaryDark = Color(0xFF4A90D9);
  static const Color primaryLight = Color(0xFFB0E0F6);
  static const Color background = Color(0xFFF0F8FF);
  static const Color cardColor = Colors.white;

  // 涨跌色（中国股市：红涨绿跌）
  static const Color upColor = Color(0xFFFF4444); // 红色-涨
  static const Color downColor = Color(0xFF00C853); // 绿色-跌
  static const Color flatColor = Color(0xFF9E9E9E); // 平盘-灰

  // 文字色
  static const Color textPrimary = Color(0xFF1A1A2E);
  static const Color textSecondary = Color(0xFF666680);
  static const Color textHint = Color(0xFF999999);

  // 信号色
  static const Color signalBuy = Color(0xFFFF4444); // 买入信号-红
  static const Color signalSell = Color(0xFF00C853); // 卖出信号-绿
  static const Color signalHold = Color(0xFFFFA726); // 持有-橙
  static const Color signalWatch = Color(0xFF42A5F5); // 观望-蓝

  static ThemeData get theme {
    return ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(
        seedColor: primaryColor,
        primary: primaryDark,
        surface: background,
        brightness: Brightness.light,
      ),
      scaffoldBackgroundColor: background,
      appBarTheme: const AppBarTheme(
        backgroundColor: Colors.white,
        foregroundColor: textPrimary,
        elevation: 0,
        centerTitle: true,
        titleTextStyle: TextStyle(
          color: textPrimary,
          fontSize: 18,
          fontWeight: FontWeight.w600,
        ),
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: Colors.white,
        selectedItemColor: primaryDark,
        unselectedItemColor: textHint,
        type: BottomNavigationBarType.fixed,
        elevation: 8,
      ),
      cardTheme: CardThemeData(
        color: cardColor,
        elevation: 1,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
        ),
        margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Colors.white,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Color(0xFFE0E0E0)),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Color(0xFFE0E0E0)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: primaryDark, width: 2),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        hintStyle: const TextStyle(color: textHint),
      ),
      dividerTheme: const DividerThemeData(
        color: Color(0xFFF0F0F0),
        thickness: 1,
        space: 1,
      ),
    );
  }

  /// 根据涨跌幅返回颜色
  static Color priceColor(double changePercent) {
    if (changePercent > 0) return upColor;
    if (changePercent < 0) return downColor;
    return flatColor;
  }

  /// 根据评级返回颜色（兼容字符串方式）
  static Color ratingColor(String rating) {
    switch (rating) {
      case 'strongBuy':
      case '强烈买入':
        return signalBuy;
      case 'buy':
      case '买入':
        return Colors.deepOrange;
      case 'watch':
      case '观望':
        return signalHold;
      case 'attention':
      case '关注':
        return signalWatch;
      case 'avoid':
      case '回避':
        return signalSell;
      default:
        return flatColor;
    }
  }

  /// 根据 Rating 枚举返回颜色（类型安全）
  static Color ratingColorFromEnum(Rating rating) {
    switch (rating) {
      case Rating.strongBuy:
        return signalBuy;
      case Rating.buy:
        return Colors.deepOrange;
      case Rating.watch:
        return signalHold;
      case Rating.attention:
        return signalWatch;
      case Rating.avoid:
        return signalSell;
    }
  }
}
