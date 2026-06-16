import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'providers/pc_data_provider.dart';
import 'theme/app_theme.dart';
import 'providers/stock_provider.dart';
import 'providers/watchlist_provider.dart';
import 'providers/analysis_provider.dart';
import 'providers/position_provider.dart';
import 'providers/performance_provider.dart';
import 'services/storage_service.dart';
import 'services/notification_service.dart';
import 'pages/home_page.dart';
import 'pages/search_page.dart';
import 'pages/stock_detail_page.dart';
import 'pages/strategy_page.dart';
import 'pages/settings_page.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // 初始化本地存储
  await StorageService.init();

  // 初始化通知服务
  await NotificationService().init();

  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => StockProvider()..init()),
        ChangeNotifierProvider(create: (_) => WatchlistProvider()..loadWatchlist()),
        ChangeNotifierProvider(create: (_) => AnalysisProvider()),
        ChangeNotifierProvider(create: (_) => PositionProvider()..loadPositions()),
        ChangeNotifierProvider(create: (_) => PerformanceProvider()),
        ChangeNotifierProvider(
          create: (ctx) {
            final provider = PcDataProvider()..init();
            // 关联WatchlistProvider，自动同步PC推荐股票
            final watchProvider = ctx.read<WatchlistProvider>();
            provider.linkWatchlistProvider(watchProvider);
            return provider;
          },
        ),
      ],
      child: MaterialApp(
        title: '股票分析',
        theme: AppTheme.theme,
        debugShowCheckedModeBanner: false,
        home: const MainScreen(),
      ),
    );
  }
}

class MainScreen extends StatefulWidget {
  const MainScreen({super.key});

  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  int _currentIndex = 0;
  late final PageController _pageController;

  @override
  void initState() {
    super.initState();
    _pageController = PageController();
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  void switchToTab(int index) {
    _pageController.jumpToPage(index);
    setState(() => _currentIndex = index);
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: _currentIndex != 0,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && _currentIndex == 0) {
          // 主页按返回键 → 退出App
          SystemNavigator.pop();
        }
      },
      child: Scaffold(
        body: PageView(
          controller: _pageController,
          onPageChanged: (index) => setState(() => _currentIndex = index),
          children: [
            HomePage(
              onNavigateToDetail: () => switchToTab(1),
              onNavigateToSearch: () => switchToTab(2),
            ),
            const StockDetailPage(),
            SearchPage(onNavigateToDetail: () => switchToTab(1)),
            const StrategyPage(),
            const SettingsPage(),
          ],
        ),
        bottomNavigationBar: BottomNavigationBar(
          currentIndex: _currentIndex,
          onTap: (index) => switchToTab(index),
          items: const [
            BottomNavigationBarItem(icon: Icon(Icons.home_outlined), activeIcon: Icon(Icons.home), label: '首页'),
            BottomNavigationBarItem(icon: Icon(Icons.show_chart_outlined), activeIcon: Icon(Icons.show_chart), label: '详情'),
            BottomNavigationBarItem(icon: Icon(Icons.search_outlined), activeIcon: Icon(Icons.search), label: '搜索'),
            BottomNavigationBarItem(icon: Icon(Icons.tune_outlined), activeIcon: Icon(Icons.tune), label: '策略'),
            BottomNavigationBarItem(icon: Icon(Icons.settings_outlined), activeIcon: Icon(Icons.settings), label: '设置'),
          ],
        ),
      ),
    );
  }
}
