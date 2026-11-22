"""解析导入演示域数据：响应样本、历史映射版本、已领用任务与旧缓存。

这些表以下划线开头，属于领域内部状态，不会出现在通用模块列表/运营概览中。
可见模块里的示例行（响应解析规则、结果缓存策略、投递管道）也在这里补齐，
让「字段映射文件导入」这条链路一启动就有可核对的旧任务。
"""
from __future__ import annotations

from typing import Any

ENDPOINT = "ENDP-CSV-0001"
SAMPLE = {"gross": 100, "net": 80, "currency": "CNY"}
SEED_TIME = "2026-11-17 09:00:00"


def bootstrap(store: Any) -> None:
    """初始化隐藏域表，并追加与导入链路相关的可见模块行。"""
    for table in (
        "_parse_samples",
        "_parse_imports",
        "_parse_conflicts",
        "_mapping_versions",
        "_delivery_tasks",
        "_delivery_todos",
        "_delivery_runs",
        "_cache_entries",
    ):
        store.rows(table)

    # 同一端点的响应样本：gross/net 都是数字，currency 是字符串。
    store.rows("_parse_samples").append({
        "id": 1,
        "样本编号": "SAMP-CSV-0001",
        "端点": ENDPOINT,
        "样本": dict(SAMPLE),
        "来源": "端点响应样本",
        "采集时间": SEED_TIME,
    })

    # 历史版本 1：旧任务领用的 total <- $.gross。
    legacy_total = {
        "字段别名": "total",
        "JSON路径": "$.gross",
        "数据类型": "number",
        "默认值": None,
        "规则ID": 101,
        "样本原值": 100,
        "验证": "通过",
        "来源": "历史生效定义",
        "导入ID": None,
        "启效时间": SEED_TIME,
    }
    store.rows("_mapping_versions").append({
        "id": 1,
        "端点": ENDPOINT,
        "版本": 1,
        "定义": {"total": dict(legacy_total)},
        "定义指纹": "legacy-v1",
        "原因": "历史生效定义：total 从 gross 取值",
        "导入ID": None,
        "状态": "已生效",
        "创建时间": SEED_TIME,
    })

    # 可见的响应解析规则：旧定义仍处于生效中，供旧任务按原路径取值。
    store.rows("response_parse").append({
        "id": 101,
        "规则编号": "RESP-CSV-TOTAL-01",
        "目标端点": ENDPOINT,
        "JSON路径": "$.gross",
        "字段别名": "total",
        "数据类型": "number",
        "默认值": "",
        "解析顺序": "1",
        "映射描述": "历史生效定义：total 从 $.gross 取值",
        "映射版本": 1,
        "验证状态": "已验证",
        "样本原值": 100,
        "来源导入": "历史定义",
        "status": "生效中", "pending": False, "abnormal": False,
    })

    # 可见的结果缓存策略：旧缓存正在服务已领用原映射的投递。
    store.rows("data_cache").append({
        "id": 101,
        "策略编号": "DATA-CSV-0001",
        "目标端点": ENDPOINT,
        "缓存TTL": "300",
        "缓存键": f"parse:{ENDPOINT}:v1",
        "最大条目": "1000",
        "淘汰算法": "LRU",
        "命中次数": 1,
        "最后刷新": SEED_TIME,
        "映射版本": 1,
        "status": "生效中", "pending": False, "abnormal": False,
    })

    # 旧缓存条目：只含 total，值来自 gross。旧任务只会看到这一套列。
    store.rows("_cache_entries").append({
        "id": 1,
        "缓存编号": "CACHE-CSV-V1",
        "端点": ENDPOINT,
        "映射版本": 1,
        "导入ID": None,
        "定义指纹": "legacy-v1",
        "列": ["total"],
        "行": [{"total": 100}],
        "字段来源": {
            "total": {
                "JSON路径": "$.gross",
                "样本原值": 100,
                "输出值": 100,
                "规则ID": 101,
                "导入ID": None,
                "来源": "历史生效定义",
                "冲突ID": None,
                "冲突待核对": False,
            },
        },
        "确认范围": ["total"],
        "状态": "已就绪",
        "刷新时间": SEED_TIME,
    })

    # 可见投递管道：旧任务附着在这条管道上，领用版本 1。
    store.rows("data_delivery").append({
        "id": 101,
        "管道编号": "DATA-CSV-0001",
        "数据来源": ENDPOINT,
        "目标地址": "https://downstream.example.com/total",
        "投递格式": "JSON",
        "分区策略": "mapping=v1",
        "投递频率": "on_change",
        "压缩方式": "none",
        "最后投递": "",
        "映射版本": 1,
        "status": "投递中", "pending": True, "abnormal": False,
    })

    # 旧任务：已领用版本 1 的快照。后续确认/更正只能影响新任务。
    store.rows("_delivery_tasks").append({
        "id": 1,
        "任务编号": "TASK-CSV-LEGACY-01",
        "管道ID": 101,
        "端点": ENDPOINT,
        "领用版本": 1,
        "映射快照": {"total": dict(legacy_total)},
        "领用时间": SEED_TIME,
        "状态": "已领用",
        "说明": "旧任务：total 按原路径 $.gross 取值",
    })

    # 旧缓存已经服务过一次按原映射的投递，交付记录保留当时的字段来源。
    store.rows("_delivery_runs").append({
        "id": 1,
        "投递编号": "DELIV-CSV-0001",
        "管道ID": 101,
        "端点": ENDPOINT,
        "映射版本": 1,
        "缓存条目": "CACHE-CSV-V1",
        "定义指纹": "legacy-v1",
        "列": ["total"],
        "字段": {
            "total": {
                "值": 100,
                "JSON路径": "$.gross",
                "样本原值": 100,
                "来源": "历史生效定义",
                "交付状态": "已交付",
                "待核对": False,
                "冲突ID": None,
                "依据": "旧任务领用版本 1，按原路径取值",
            },
        },
        "待核对字段": [],
        "冲突": [],
        "映射定义": {"total": dict(legacy_total)},
        "状态": "已交付",
        "说明": "历史领用，按原映射取值",
        "投递时间": SEED_TIME,
    })
