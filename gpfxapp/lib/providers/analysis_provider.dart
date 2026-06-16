import 'package:flutter/material.dart';

class AnalysisProvider extends ChangeNotifier {
  /// 当前选中的分析Tab
  int _selectedTabIndex = 0;
  int get selectedTabIndex => _selectedTabIndex;

  /// K线显示周期
  String _klinePeriod = '日线';
  String get klinePeriod => _klinePeriod;

  /// 是否显示技术指标
  bool _showMA = true;
  bool _showBollinger = false;
  bool _showRSI = false;

  bool get showMA => _showMA;
  bool get showBollinger => _showBollinger;
  bool get showRSI => _showRSI;

  void setTab(int index) {
    _selectedTabIndex = index;
    notifyListeners();
  }

  void setKlinePeriod(String period) {
    _klinePeriod = period;
    notifyListeners();
  }

  void toggleMA() {
    _showMA = !_showMA;
    notifyListeners();
  }

  void toggleBollinger() {
    _showBollinger = !_showBollinger;
    notifyListeners();
  }

  void toggleRSI() {
    _showRSI = !_showRSI;
    notifyListeners();
  }
}
