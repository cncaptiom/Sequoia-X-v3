import 'package:flutter/material.dart';
import '../models/position.dart';
import '../models/stock_info.dart';
import '../services/storage_service.dart';

class PositionProvider extends ChangeNotifier {
  final StorageService _storage = StorageService();

  List<Position> _positions = [];

  List<Position> get positions => _positions;

  /// 加载持仓
  Future<void> loadPositions() async {
    _positions = await _storage.getPositions();
    notifyListeners();
  }

  /// 添加持仓
  Future<void> addPosition(Position position) async {
    _positions.add(position);
    await _save();
    notifyListeners();
  }

  /// 更新持仓
  Future<void> updatePosition(int index, Position position) async {
    if (index >= 0 && index < _positions.length) {
      _positions[index] = position;
      await _save();
      notifyListeners();
    }
  }

  /// 删除持仓
  Future<void> removePosition(String code) async {
    _positions.removeWhere((p) => p.code == code);
    await _save();
    notifyListeners();
  }

  /// 获取某股票的持仓（没有则 null）
  Position? getPosition(String code) {
    try {
      return _positions.firstWhere((p) => p.code == code);
    } catch (_) {
      return null;
    }
  }

  Future<void> _save() async {
    await _storage.savePositions(_positions);
  }
}
