import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../theme/app_theme.dart';
import '../providers/stock_provider.dart';
import '../providers/watchlist_provider.dart';
import '../models/stock_info.dart';
import '../models/stock_quote.dart';

class SearchPage extends StatefulWidget {
  final VoidCallback? onNavigateToDetail;

  const SearchPage({super.key, this.onNavigateToDetail});

  @override
  State<SearchPage> createState() => _SearchPageState();
}

class _SearchPageState extends State<SearchPage>
    with AutomaticKeepAliveClientMixin {
  final _searchController = TextEditingController();
  int _rankingTab = 0; // 0=涨幅榜, 1=跌幅榜, 2=成交额榜

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadDiscoveryData());
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  void _onSearch(String value) {
    setState(() {});
    context.read<StockProvider>().search(value);
  }

  Future<void> _loadDiscoveryData() async {
    final provider = context.read<StockProvider>();
    await Future.wait([
      provider.loadTopGainers(),
      provider.loadVolumeLeaders(),
    ]);
    // 板块数据独立加载
    provider.loadSectors();
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return Scaffold(
      appBar: AppBar(
        title: const Text('发现股票'),
      ),
      body: Column(
        children: [
          // 搜索栏
          Container(
            padding: const EdgeInsets.all(16),
            color: Colors.white,
            child: TextField(
              controller: _searchController,
              decoration: InputDecoration(
                hintText: '输入股票代码或名称搜索...',
                prefixIcon: const Icon(Icons.search, color: AppTheme.textHint),
                suffixIcon: _searchController.text.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear, color: AppTheme.textHint),
                        onPressed: () {
                          _searchController.clear();
                          _onSearch('');
                        },
                      )
                    : null,
              ),
              textInputAction: TextInputAction.search,
              onChanged: _onSearch,
            ),
          ),

          // 内容区
          Expanded(
            child: _searchController.text.isNotEmpty
                ? _buildSearchResults()
                : _buildDiscovery(),
          ),
        ],
      ),
    );
  }

  // ==================== 搜索结果 ====================

  Widget _buildSearchResults() {
    return Consumer<StockProvider>(
      builder: (context, provider, _) {
        if (provider.isSearching) {
          return const Center(child: CircularProgressIndicator());
        }

        if (provider.searchResults.isEmpty) {
          return Center(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.search_off,
                    size: 48, color: AppTheme.textHint.withOpacity(0.5)),
                const SizedBox(height: 12),
                const Text('未找到匹配的股票',
                    style: TextStyle(color: AppTheme.textSecondary)),
              ],
            ),
          );
        }

        return ListView.separated(
          padding: const EdgeInsets.all(16),
          itemCount: provider.searchResults.length,
          separatorBuilder: (_, __) => const SizedBox(height: 8),
          itemBuilder: (context, index) {
            final stock = provider.searchResults[index];
            return _SearchResultCard(
              stock: stock,
              onTap: () {
                context.read<StockProvider>().loadStockDetail(stock);
                widget.onNavigateToDetail?.call();
              },
            );
          },
        );
      },
    );
  }

  // ==================== 发现股票（排行榜+板块） ====================

  Widget _buildDiscovery() {
    return Consumer<StockProvider>(
      builder: (context, provider, _) {
        return ListView(
          padding: const EdgeInsets.all(16),
          children: [
            // 排行榜标题
            const Row(
              children: [
                Icon(Icons.leaderboard, size: 18, color: AppTheme.primaryDark),
                SizedBox(width: 6),
                Text('市场排行',
                    style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: AppTheme.textPrimary)),
              ],
            ),
            const SizedBox(height: 12),

            // 排行Tab切换
            Row(
              children: [
                _buildChip('涨幅榜', 0),
                const SizedBox(width: 8),
                _buildChip('跌幅榜', 1),
                const SizedBox(width: 8),
                _buildChip('成交额榜', 2),
              ],
            ),
            const SizedBox(height: 12),

            // 排行列表
            _buildRankingList(provider),

            const SizedBox(height: 20),

            // 热门板块
            const Row(
              children: [
                Icon(Icons.category, size: 18, color: AppTheme.primaryDark),
                SizedBox(width: 6),
                Text('热门板块',
                    style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: AppTheme.textPrimary)),
              ],
            ),
            const SizedBox(height: 12),

            if (provider.sectorLoadFailed)
              _buildSectorError(context)
            else if (provider.sectors.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 20),
                child: Center(
                    child: Text('加载板块中...',
                        style: TextStyle(color: AppTheme.textHint))),
              )
            else
              _buildSectorGrid(provider.sectors),

            const SizedBox(height: 20),
          ],
        );
      },
    );
  }

  Widget _buildChip(String label, int index) {
    final selected = _rankingTab == index;
    return GestureDetector(
      onTap: () {
        setState(() => _rankingTab = index);
        final provider = context.read<StockProvider>();
        if (index == 0)
          provider.loadTopGainers();
        else if (index == 1)
          provider.loadTopLosers();
        else
          provider.loadVolumeLeaders();
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        decoration: BoxDecoration(
          color: selected ? AppTheme.primaryDark : Colors.white,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(
              color: selected
                  ? AppTheme.primaryDark
                  : AppTheme.textHint.withOpacity(0.3)),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected ? Colors.white : AppTheme.textSecondary,
            fontSize: 13,
            fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
          ),
        ),
      ),
    );
  }

  Widget _buildRankingList(StockProvider provider) {
    final list = _rankingTab == 0
        ? provider.topGainers
        : _rankingTab == 1
            ? provider.topLosers
            : provider.volumeLeaders;

    if (provider.isLoadingDiscovery && list.isEmpty) {
      return const Center(
          child: Padding(
        padding: EdgeInsets.all(20),
        child: CircularProgressIndicator(),
      ));
    }

    if (list.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 20),
        child: Center(
            child: Text('暂无数据', style: TextStyle(color: AppTheme.textHint))),
      );
    }

    return Column(
      children: list.take(10).toList().asMap().entries.map((entry) {
        final i = entry.key;
        final quote = entry.value;
        return _RankingItem(
            index: i + 1,
            quote: quote,
            onTap: () {
              final market = quote.market.isNotEmpty
                  ? quote.market
                  : (quote.code.startsWith('6') || quote.code.startsWith('9')
                      ? 'SH'
                      : 'SZ');
              final stock =
                  StockInfo(code: quote.code, name: quote.name, market: market);
              context.read<StockProvider>().loadStockDetail(stock);
              widget.onNavigateToDetail?.call();
            });
      }).toList(),
    );
  }

  Widget _buildSectorGrid(List<StockQuote> sectors) {
    return GridView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 3,
        childAspectRatio: 1.3,
        crossAxisSpacing: 8,
        mainAxisSpacing: 8,
      ),
      itemCount: sectors.length > 12 ? 12 : sectors.length,
      itemBuilder: (context, index) {
        final sector = sectors[index];
        return _SectorCard(
          name: sector.name,
          changePercent: sector.changePercent,
          onTap: () => _showSectorStocks(sector),
        );
      },
    );
  }

  void _showSectorStocks(StockQuote sector) {
    final provider = context.read<StockProvider>();
    provider.loadSectorStocks(sector.code, sector.name);
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => _SectorStockSheet(
        sectorName: sector.name,
        provider: provider,
        onTapStock: (quote) {
          Navigator.of(ctx).pop();
          final market = quote.market.isNotEmpty
              ? quote.market
              : (quote.code.startsWith('6') || quote.code.startsWith('9')
                  ? 'SH'
                  : 'SZ');
          final stock =
              StockInfo(code: quote.code, name: quote.name, market: market);
          context.read<StockProvider>().loadStockDetail(stock);
          widget.onNavigateToDetail?.call();
        },
      ),
    );
  }

  Widget _buildSectorError(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 20),
      child: Center(
        child: Column(
          children: [
            const Icon(Icons.cloud_off, size: 32, color: AppTheme.textHint),
            const SizedBox(height: 8),
            const Text('板块数据加载失败',
                style: TextStyle(color: AppTheme.textSecondary)),
            const SizedBox(height: 8),
            TextButton.icon(
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('重试'),
              onPressed: () {
                context.read<StockProvider>().loadSectors();
              },
            ),
          ],
        ),
      ),
    );
  }
}

// ==================== 排行项 ====================

class _RankingItem extends StatelessWidget {
  final int index;
  final StockQuote quote;
  final VoidCallback onTap;

  const _RankingItem(
      {required this.index, required this.quote, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final color = AppTheme.priceColor(quote.changePercent);
    return InkWell(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: const BoxDecoration(
          border:
              Border(bottom: BorderSide(color: Color(0xFFF0F0F0), width: 0.5)),
        ),
        child: Row(
          children: [
            SizedBox(
              width: 24,
              child: Text(
                '$index',
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.bold,
                  color: index <= 3
                      ? AppTheme.primaryDark
                      : AppTheme.textSecondary,
                ),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(quote.name,
                      style: const TextStyle(
                          fontSize: 14, fontWeight: FontWeight.w500)),
                  Text(quote.code,
                      style: const TextStyle(
                          fontSize: 11, color: AppTheme.textHint)),
                ],
              ),
            ),
            Text(quote.priceText,
                style:
                    const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
            const SizedBox(width: 12),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: color.withOpacity(0.1),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text(
                quote.changePercentText,
                style: TextStyle(
                    fontSize: 12, fontWeight: FontWeight.bold, color: color),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ==================== 板块卡片 ====================

class _SectorCard extends StatelessWidget {
  final String name;
  final double changePercent;
  final VoidCallback onTap;

  const _SectorCard(
      {required this.name, required this.changePercent, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final isUp = changePercent >= 0;
    final color = isUp ? AppTheme.upColor : AppTheme.downColor;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: color.withOpacity(0.2)),
        ),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(name,
                style:
                    const TextStyle(fontSize: 12, fontWeight: FontWeight.w500),
                textAlign: TextAlign.center,
                maxLines: 1,
                overflow: TextOverflow.ellipsis),
            const SizedBox(height: 4),
            Text(
              '${isUp ? "+" : ""}${changePercent.toStringAsFixed(2)}%',
              style: TextStyle(
                  fontSize: 13, fontWeight: FontWeight.bold, color: color),
            ),
          ],
        ),
      ),
    );
  }
}

// ==================== 板块成分股底部弹窗 ====================

class _SectorStockSheet extends StatelessWidget {
  final String sectorName;
  final StockProvider provider;
  final void Function(StockQuote quote) onTapStock;

  const _SectorStockSheet(
      {required this.sectorName,
      required this.provider,
      required this.onTapStock});

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      initialChildSize: 0.7,
      maxChildSize: 0.9,
      minChildSize: 0.4,
      expand: false,
      builder: (ctx, scrollController) {
        return Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
              child: Row(
                children: [
                  Expanded(
                      child: Text(sectorName,
                          style: const TextStyle(
                              fontSize: 18, fontWeight: FontWeight.bold))),
                  IconButton(
                    icon: const Icon(Icons.close),
                    onPressed: () => Navigator.of(ctx).pop(),
                  ),
                ],
              ),
            ),
            const Divider(height: 1),
            Expanded(
              child: provider.isLoadingSectorStocks
                  ? const Center(child: CircularProgressIndicator())
                  : provider.sectorStocks.isEmpty
                      ? const Center(
                          child: Text('暂无成分股数据',
                              style: TextStyle(color: AppTheme.textHint)))
                      : ListView.builder(
                          controller: scrollController,
                          itemCount: provider.sectorStocks.length,
                          itemBuilder: (ctx, index) {
                            final stock = provider.sectorStocks[index];
                            return _RankingItem(
                                index: index + 1,
                                quote: stock,
                                onTap: () => onTapStock(stock));
                          },
                        ),
            ),
          ],
        );
      },
    );
  }
}

// ==================== 搜索结果卡片 ====================

class _SearchResultCard extends StatelessWidget {
  final StockInfo stock;
  final VoidCallback? onTap;

  const _SearchResultCard({required this.stock, this.onTap});

  @override
  Widget build(BuildContext context) {
    return Consumer<WatchlistProvider>(
      builder: (context, watchlist, _) {
        final isWatch = watchlist.watchlist
            .any((s) => s.code == stock.code && s.market == stock.market);

        return Card(
          margin: EdgeInsets.zero,
          child: ListTile(
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            leading: Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: AppTheme.primaryLight.withOpacity(0.3),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Center(
                child: Text(
                  stock.exchange[0],
                  style: const TextStyle(
                    color: AppTheme.primaryDark,
                    fontWeight: FontWeight.bold,
                    fontSize: 16,
                  ),
                ),
              ),
            ),
            title: Text(stock.name,
                style: const TextStyle(fontWeight: FontWeight.w600)),
            subtitle: Text('${stock.code} · ${stock.exchange}',
                style: const TextStyle(color: AppTheme.textHint, fontSize: 12)),
            trailing: IconButton(
              icon: Icon(
                isWatch ? Icons.star : Icons.star_border,
                color: isWatch ? AppTheme.signalBuy : AppTheme.textHint,
              ),
              onPressed: () {
                if (isWatch) {
                  watchlist.remove(stock);
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                        content: Text('已取消关注 ${stock.name}'),
                        duration: const Duration(seconds: 1)),
                  );
                } else {
                  watchlist.add(stock);
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                        content: Text('已添加 ${stock.name} 到自选'),
                        duration: const Duration(seconds: 1)),
                  );
                }
              },
            ),
            onTap: onTap,
          ),
        );
      },
    );
  }
}
