import 'package:flutter/material.dart';
import '../theme/app_theme.dart';
import '../models/analysis_result.dart';

class SignalBadge extends StatelessWidget {
  final Rating rating;
  final double fontSize;

  const SignalBadge({
    super.key,
    required this.rating,
    this.fontSize = 12,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: _bgColor.withOpacity(0.15),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        _label,
        style: TextStyle(
          color: _bgColor,
          fontSize: fontSize,
          fontWeight: FontWeight.bold,
        ),
      ),
    );
  }

  Color get _bgColor {
    switch (rating) {
      case Rating.strongBuy:
        return AppTheme.signalBuy;
      case Rating.buy:
        return AppTheme.signalBuy;
      case Rating.watch:
        return AppTheme.signalHold;
      case Rating.attention:
        return AppTheme.signalWatch;
      case Rating.avoid:
        return AppTheme.signalSell;
    }
  }

  String get _label {
    switch (rating) {
      case Rating.strongBuy:
        return '强烈买入';
      case Rating.buy:
        return '买入';
      case Rating.watch:
        return '观望';
      case Rating.attention:
        return '关注';
      case Rating.avoid:
        return '回避';
    }
  }
}
