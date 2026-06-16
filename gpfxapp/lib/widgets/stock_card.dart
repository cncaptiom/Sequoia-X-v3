import 'package:flutter/material.dart';
import '../theme/app_theme.dart';
import '../models/stock_info.dart';
import '../models/stock_quote.dart';
import '../models/analysis_result.dart';
import '../models/position.dart';

class StockCard extends StatelessWidget {
  final StockInfo stock;
  final StockQuote? quote;
  final AnalysisResult? analysis;
  final Position? position; // 持仓信息
  final VoidCallback? onTap;
  final VoidCallback? onRemove;
  final bool isHeld; // 已买入标记（自动刷新+通知）

  const StockCard({
    super.key,
    required this.stock,
    this.quote,
    this.analysis,
    this.position,
    this.onTap,
    this.onRemove,
    this.isHeld = false,
  });

  @override
  Widget build(BuildContext context) {
    if (quote == null) {
      return Card(
        margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: onTap,
          child: ListTile(
            contentPadding: const EdgeInsets.symmetric(horizontal: 14),
            leading: _buildExchangeIcon(),
            title: Text(stock.name, style: const TextStyle(fontWeight: FontWeight.w500)),
            subtitle: Text(stock.code, style: const TextStyle(color: AppTheme.textHint, fontSize: 12)),
            trailing: const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2)),
          ),
        ),
      );
    }

    final changeColor = AppTheme.priceColor(quote!.changePercent);

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            children: [
              // 股票信息
              Expanded(
                flex: 3,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(stock.name, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                        if (isHeld && analysis != null)
                          _buildSignalBadge(analysis!.rating),
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(stock.code, style: const TextStyle(color: AppTheme.textHint, fontSize: 12)),
                  ],
                ),
              ),

              // 价格信息
              Expanded(
                flex: 2,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(quote!.priceText,
                        style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: changeColor)),
                    Text(quote!.changePercentText,
                        style: TextStyle(color: changeColor, fontSize: 13, fontWeight: FontWeight.w500)),
                    // 持仓盈亏
                    if (position != null && quote != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        _profitLossText(position!, quote!.currentPrice),
                        style: TextStyle(
                          fontSize: 11,
                          color: position!.profitLoss(quote!.currentPrice) >= 0
                              ? AppTheme.upColor
                              : AppTheme.downColor,
                        ),
                      ),
                    ],
                  ],
                ),
              ),

              // 评级
              if (analysis != null) ...[
                const SizedBox(width: 8),
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: AppTheme.ratingColorFromEnum(analysis!.rating).withOpacity(0.1),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Center(
                    child: Text(
                      _ratingShort(analysis!.rating),
                      style: TextStyle(
                        fontSize: 12,
                        color: AppTheme.ratingColorFromEnum(analysis!.rating),
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ),
              ],

              // 删除按钮
              IconButton(
                icon: const Icon(Icons.close, size: 18, color: AppTheme.textHint),
                onPressed: onRemove,
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildExchangeIcon() {
    return Container(
      width: 40,
      height: 40,
      decoration: BoxDecoration(
        color: AppTheme.primaryLight.withOpacity(0.3),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Center(
        child: Text(
          stock.exchange[0],
          style: const TextStyle(color: AppTheme.primaryDark, fontWeight: FontWeight.bold, fontSize: 14),
        ),
      ),
    );
  }

  /// 重点关注股票的买卖信号徽标
  Widget _buildSignalBadge(Rating rating) {
    if (rating == Rating.strongBuy || rating == Rating.buy) {
      return Container(
        margin: const EdgeInsets.only(left: 6),
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
        decoration: BoxDecoration(
          color: AppTheme.upColor,
          borderRadius: BorderRadius.circular(4),
        ),
        child: const Text('买', style: TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold)),
      );
    }
    if (rating == Rating.avoid) {
      return Container(
        margin: const EdgeInsets.only(left: 6),
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
        decoration: BoxDecoration(
          color: AppTheme.downColor,
          borderRadius: BorderRadius.circular(4),
        ),
        child: const Text('卖', style: TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold)),
      );
    }
    return const SizedBox();
  }

  String _ratingShort(Rating rating) {
    switch (rating) {
      case Rating.strongBuy:
      case Rating.buy:
        return '买入';
      case Rating.watch:
        return '持有';
      case Rating.attention:
        return '关注';
      case Rating.avoid:
        return '回避';
    }
  }

  String _profitLossText(Position pos, double currentPrice) {
    final pl = pos.profitLoss(currentPrice);
    final plp = pos.profitLossPercent(currentPrice);
    final sign = pl >= 0 ? '+' : '';
    return '持仓 ${sign}${pl.toStringAsFixed(0)} (${sign}${plp.toStringAsFixed(1)}%)';
  }
}
