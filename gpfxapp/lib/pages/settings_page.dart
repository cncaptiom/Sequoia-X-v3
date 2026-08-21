import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import '../theme/app_theme.dart';
import '../providers/stock_provider.dart';
import '../providers/watchlist_provider.dart';
import '../providers/pc_data_provider.dart';
import '../services/notification_service.dart';
import '../services/storage_service.dart';
import '../models/pc_recommendation.dart';
import 'performance_page.dart';

class SettingsPage extends StatelessWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('设置'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // PC端数据状态
          Consumer<PcDataProvider>(
            builder: (context, pcProvider, _) {
              final hasData = pcProvider.isAvailable;
              final lastFetch = pcProvider.lastFetch;
              final regime = pcProvider.marketRegimeLabel;
              final exposure = pcProvider.suggestedExposure;
              final picks = pcProvider.topPicks;

              return Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                      color: hasData
                          ? AppTheme.primaryDark.withOpacity(0.3)
                          : AppTheme.textHint.withOpacity(0.2)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.cloud_sync,
                            size: 20,
                            color: hasData
                                ? AppTheme.primaryDark
                                : AppTheme.textHint),
                        const SizedBox(width: 8),
                        Text('PC端数据同步',
                            style: TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w600,
                                color: hasData
                                    ? AppTheme.textPrimary
                                    : AppTheme.textHint)),
                        const Spacer(),
                        if (hasData)
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(
                              color: AppTheme.upColor.withOpacity(0.1),
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: const Text('已同步',
                                style: TextStyle(
                                    fontSize: 11, color: AppTheme.upColor)),
                          )
                        else
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(
                              color: AppTheme.textHint.withOpacity(0.1),
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: const Text('未同步',
                                style: TextStyle(
                                    fontSize: 11, color: AppTheme.textHint)),
                          ),
                      ],
                    ),
                    if (hasData) ...[
                      const SizedBox(height: 8),
                      if (regime.isNotEmpty)
                        Text(
                            '市场状态: $regime  建议仓位: ${(exposure * 100).toInt()}%',
                            style: const TextStyle(
                                fontSize: 12, color: AppTheme.textSecondary)),
                      if (lastFetch != null)
                        Text(
                            '数据日期: ${lastFetch.toLocal().toString().substring(0, 10)}',
                            style: const TextStyle(
                                fontSize: 11, color: AppTheme.textHint)),
                      Text('推荐股票: ${picks.length}只',
                          style: const TextStyle(
                              fontSize: 11, color: AppTheme.textHint)),
                    ],
                    const SizedBox(height: 8),
                    SizedBox(
                      width: double.infinity,
                      child: OutlinedButton.icon(
                        onPressed: () async {
                          final pcProvider = context.read<PcDataProvider>();
                          await pcProvider.refresh();
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(
                                content: Text(pcProvider.isAvailable
                                    ? 'PC端数据已刷新'
                                    : '刷新失败，请检查网络'),
                                duration: const Duration(seconds: 2),
                              ),
                            );
                          }
                        },
                        icon: const Icon(Icons.refresh, size: 16),
                        label: const Text('手动刷新'),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: AppTheme.primaryDark,
                          side: const BorderSide(color: AppTheme.primaryDark),
                        ),
                      ),
                    ),
                  ],
                ),
              );
            },
          ),

          const SizedBox(height: 12),

          // 通知设置
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Consumer<StockProvider>(
              builder: (context, provider, _) {
                final config = provider.strategyConfig;
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Padding(
                      padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
                      child: Row(
                        children: [
                          Icon(Icons.notifications_outlined,
                              size: 20, color: AppTheme.primaryDark),
                          SizedBox(width: 8),
                          Text('通知设置',
                              style: TextStyle(
                                  fontSize: 16, fontWeight: FontWeight.w600)),
                        ],
                      ),
                    ),
                    _buildSwitch(
                      icon: Icons.notifications_active,
                      title: '启用通知',
                      subtitle: '接收买卖信号推送',
                      value: config.notificationEnabled,
                      onChanged: (v) => provider.updateStrategy(
                          config.copyWith(notificationEnabled: v)),
                    ),
                    if (config.notificationEnabled) ...[
                      const Divider(),
                      _buildSwitch(
                          icon: Icons.timeline,
                          iconColor: AppTheme.signalBuy,
                          title: '金叉/死叉通知',
                          subtitle: '均线金叉死叉信号',
                          value: config.notifyGoldenCross,
                          onChanged: (v) => provider.updateStrategy(
                              config.copyWith(notifyGoldenCross: v))),
                      const Divider(),
                      _buildSwitch(
                          icon: Icons.swap_vert,
                          iconColor: AppTheme.signalHold,
                          title: '超买/超卖通知',
                          subtitle: 'RSI超买超卖信号',
                          value: config.notifyOversold,
                          onChanged: (v) => provider.updateStrategy(
                              config.copyWith(notifyOversold: v))),
                      const Divider(),
                      _buildSwitch(
                          icon: Icons.trending_up,
                          iconColor: AppTheme.upColor,
                          title: '突破通知',
                          subtitle: '价格突破关键位',
                          value: config.notifyBreakout,
                          onChanged: (v) => provider.updateStrategy(
                              config.copyWith(notifyBreakout: v))),
                      const Divider(),
                      _buildSwitch(
                          icon: Icons.shopping_cart,
                          iconColor: AppTheme.signalBuy,
                          title: '买入区间通知',
                          subtitle: '价格到达买入区间时提醒',
                          value: config.notifyBuyZone,
                          onChanged: (v) => provider.updateStrategy(
                              config.copyWith(notifyBuyZone: v))),
                      const Divider(),
                      _buildSwitch(
                          icon: Icons.flag,
                          iconColor: AppTheme.signalSell,
                          title: '止损通知',
                          subtitle: '价格触及止损位时提醒',
                          value: config.notifyStopLoss,
                          onChanged: (v) => provider.updateStrategy(
                              config.copyWith(notifyStopLoss: v))),
                      const Divider(),
                      _buildSwitch(
                          icon: Icons.emoji_events,
                          iconColor: AppTheme.signalBuy,
                          title: '止盈通知',
                          subtitle: '价格触及止盈位时提醒',
                          value: config.notifyTakeProfit,
                          onChanged: (v) => provider.updateStrategy(
                              config.copyWith(notifyTakeProfit: v))),
                    ],
                  ],
                );
              },
            ),
          ),

          const SizedBox(height: 12),

          // 效果跟踪
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Padding(
                  padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
                  child: Row(
                    children: [
                      Icon(Icons.auto_graph,
                          size: 20, color: AppTheme.primaryDark),
                      SizedBox(width: 8),
                      Text('效果跟踪',
                          style: TextStyle(
                              fontSize: 16, fontWeight: FontWeight.w600)),
                    ],
                  ),
                ),
                ListTile(
                  leading:
                      const Icon(Icons.analytics, color: AppTheme.primaryDark),
                  title: const Text('查看评级效果'),
                  subtitle: const Text('历史评级与后续涨跌对比统计',
                      style: TextStyle(fontSize: 12)),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute(builder: (_) => const PerformancePage()),
                  ),
                ),
              ],
            ),
          ),

          const SizedBox(height: 12),

          // 关于
          Container(
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(12),
            ),
            child: const Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
                  child: Row(
                    children: [
                      Icon(Icons.info_outline,
                          size: 20, color: AppTheme.primaryDark),
                      SizedBox(width: 8),
                      Text('关于',
                          style: TextStyle(
                              fontSize: 16, fontWeight: FontWeight.w600)),
                    ],
                  ),
                ),
                ListTile(
                  title: Text('版本'),
                  trailing: Text('v3.1.0',
                      style: TextStyle(color: AppTheme.textSecondary)),
                ),
                ListTile(
                  title: Text('数据来源'),
                  subtitle: Text('新浪财经 · 东方财富公开API · 数据仅供参考',
                      style: TextStyle(fontSize: 12)),
                ),
                ListTile(
                  title: Text('一键安装PC端定时任务'),
                  subtitle: Text('python main.py --install-schedule',
                      style: TextStyle(fontSize: 11, color: AppTheme.textHint)),
                ),
                ListTile(
                  title: Text('风险提示'),
                  subtitle: Text('本APP分析结果仅供参考，不构成投资建议。股市有风险，投资需谨慎。',
                      style: TextStyle(fontSize: 12, color: AppTheme.textHint)),
                ),
              ],
            ),
          ),

          const SizedBox(height: 12),

          // COS鉴权配置
          _CosAuthSection(),

          const SizedBox(height: 12),

          // 测试通知
          Container(
            margin: const EdgeInsets.symmetric(vertical: 8),
            child: OutlinedButton.icon(
              onPressed: () {
                NotificationService().sendSignalNotification(
                  stockName: '测试',
                  stockCode: '000000',
                  signalType: '测试通知',
                  message: '如果您看到此通知，说明通知功能正常',
                );
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                      content: Text('测试通知已发送'), duration: Duration(seconds: 2)),
                );
              },
              icon: const Icon(Icons.notifications_none),
              label: const Text('发送测试通知'),
              style: OutlinedButton.styleFrom(
                foregroundColor: AppTheme.primaryDark,
                side: const BorderSide(color: AppTheme.primaryDark),
                padding: const EdgeInsets.symmetric(vertical: 12),
              ),
            ),
          ),

          const SizedBox(height: 20),
        ],
      ),
    );
  }

  Widget _buildSwitch({
    required IconData icon,
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
    Color iconColor = AppTheme.primaryDark,
  }) {
    return ListTile(
      leading: Icon(icon, color: iconColor),
      title: Text(title),
      subtitle: Text(subtitle, style: const TextStyle(fontSize: 12)),
      trailing: Switch(
          value: value,
          activeColor: AppTheme.primaryDark,
          onChanged: onChanged),
    );
  }
}

/// COS 鉴权配置卡片
class _CosAuthSection extends StatelessWidget {
  const _CosAuthSection();

  String _maskSecret(PcDataProvider p, {required bool isId}) {
    final s = isId ? p.cosSecretId : p.cosSecretKey;
    if (s.isEmpty) return '未配置，自选股云备份将不可用';
    if (s.length <= 8) return '${s.substring(0, 2)}****';
    return '${s.substring(0, 4)}****${s.substring(s.length - 4)}';
  }

  Future<void> _editCredential(BuildContext context,
      {required bool isId}) async {
    final pcProvider = context.read<PcDataProvider>();
    final current = isId ? pcProvider.cosSecretId : pcProvider.cosSecretKey;
    final label = isId ? 'SecretId' : 'SecretKey';
    final hint = isId ? '请输入腾讯云COS SecretId' : '请输入腾讯云COS SecretKey';
    final controller = TextEditingController(text: current);

    final result = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('编辑 $label'),
        content: TextField(
          controller: controller,
          decoration: InputDecoration(hintText: hint),
          obscureText: !isId,
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, controller.text.trim()),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    if (result == null || result == current) return;

    final storage = StorageService();
    if (isId) {
      await storage.setCosSecretId(result);
    } else {
      await storage.setCosSecretKey(result);
    }
    pcProvider.updateCosAuth();
    // 同步更新 WatchlistProvider 的 COS 凭证（用于自选股云备份）
    if (context.mounted) {
      context.read<WatchlistProvider>().reloadCosAuth();
    }
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
            content: Text('$label 已更新'), duration: const Duration(seconds: 2)),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Row(
              children: [
                Icon(Icons.cloud, size: 20, color: AppTheme.primaryDark),
                SizedBox(width: 8),
                Text('云存储配置',
                    style:
                        TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
              ],
            ),
          ),
          Consumer<PcDataProvider>(
            builder: (context, pc, _) => Column(
              children: [
                ListTile(
                  leading: const Icon(Icons.key, color: AppTheme.primaryDark),
                  title: const Text('COS SecretId'),
                  subtitle: Text(_maskSecret(pc, isId: true),
                      style: const TextStyle(fontSize: 12)),
                  trailing: const Icon(Icons.edit, size: 18),
                  onTap: () => _editCredential(context, isId: true),
                ),
                ListTile(
                  leading: const Icon(Icons.lock, color: AppTheme.primaryDark),
                  title: const Text('COS SecretKey'),
                  subtitle: Text(_maskSecret(pc, isId: false),
                      style: const TextStyle(fontSize: 12)),
                  trailing: const Icon(Icons.edit, size: 18),
                  onTap: () => _editCredential(context, isId: false),
                ),
                ListTile(
                  leading: const Icon(Icons.cloud_download,
                      color: AppTheme.primaryDark),
                  title: const Text('从云端恢复自选股'),
                  subtitle: const Text('从 COS 备份找回（重装/清数据后使用）',
                      style: TextStyle(fontSize: 12)),
                  trailing: const Icon(Icons.refresh, size: 18),
                  onTap: () async {
                    final wl = context.read<WatchlistProvider>();
                    final restored = await wl.restoreFromCloud();
                    if (context.mounted) {
                      final msg = restored > 0
                          ? '已恢复 $restored 只自选股'
                          : (wl.lastRestoreError != null
                              ? '恢复失败: ${wl.lastRestoreError}'
                              : '云端无自选股备份');
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                          content: Text(msg),
                          duration: const Duration(seconds: 4),
                        ),
                      );
                    }
                  },
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
