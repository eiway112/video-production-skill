# -*- coding: utf-8 -*-
"""门禁结果四态词汇：PASS / FAIL / UNTESTED / NOT_APPLICABLE。

存在理由：门禁的历史失效形态不是"缺检查项"，而是把"没发现问题"当成"检查完成
且通过"——零截图、抽帧失败、正则取不到测量值时，检查项被压成一个 bool 通过。
本模块让"未测"成为可阻断的独立终态，并与"按规则合法跳过"区分开。

裁定规则（所有消费方共用）：任一 FAIL 即 FAIL；无 FAIL 但有 UNTESTED 时整体为
UNTESTED，**不得**写成通过；NOT_APPLICABLE 既不计违规也不计通过。

新增门禁沿用本词汇表，不得另立同义状态名。
"""

PASS = "PASS"
FAIL = "FAIL"

# 检查项未能完成（抽帧失败、截图缺失、测量值解析不到）——阻断，可诊断
UNTESTED = "UNTESTED"
# 按声明规则合法不适用（场景过短、类型化豁免）——不阻断，也不得计为通过
NOT_APPLICABLE = "NOT_APPLICABLE"


def verdict(violations, untested):
    """整体裁定。violations / untested 传计数或列表（按真值判定）。"""
    if violations:
        return FAIL
    if untested:
        return UNTESTED
    return PASS


def count_statuses(results, key="status"):
    """按四态统计逐条结果，返回 {状态: 数量}（含零计数项，供报告直读）。"""
    counts = {PASS: 0, FAIL: 0, UNTESTED: 0, NOT_APPLICABLE: 0}
    for item in results:
        status = item.get(key)
        counts[status] = counts.get(status, 0) + 1
    return counts
