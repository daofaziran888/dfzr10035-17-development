"""字段映射文件导入领域服务：预检、确认、冲突更正、缓存刷新与投递回溯。

一条导入链路的状态都落在隐藏表里（下划线开头，不暴露成通用业务模块）：

- ``_parse_samples``     同一端点的响应样本，导入定义都拿它核对；
- ``_parse_imports``     每次文件导入及其条目、已确认范围、生效版本；
- ``_parse_conflicts``   同名冲突，保留全部候选路径与样本原值，直到人工更正；
- ``_mapping_versions``  不可变的映射版本快照，交付只引用的“一套可核对映射”；
- ``_cache_entries``     按映射版本刷新的解析结果，交付实际从这里取列；
- ``_delivery_todos``    启效结果同步出的投递待办；
- ``_delivery_tasks``    旧任务领用快照，历史交付继续按当时路径取值；
- ``_delivery_runs``     每次实际投递及其字段来源，供冲突结论回查。

可见模块也同步写入：response_parse 规则、data_cache 策略、data_delivery 管道。
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from datetime import datetime
from typing import Any

from app.store import store


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_path(sample: Any, path: str) -> tuple[bool, Any]:
    """解析 ``$.a.b`` / ``$["a"][0]`` 形式的 JSON 路径。"""
    text = (path or "").strip()
    if not text.startswith("$"):
        return False, None
    current = sample
    index = 1
    while index < len(text):
        char = text[index]
        if char == ".":
            index += 1
            start = index
            while index < len(text) and text[index] not in ".[":
                index += 1
            key = text[start:index]
            if not key:
                return False, None
            if not isinstance(current, dict) or key not in current:
                return False, None
            current = current[key]
        elif char == "[":
            end = text.find("]", index)
            if end < 0:
                return False, None
            token = text[index + 1:end].strip()
            index = end + 1
            if len(token) >= 2 and token[0] in "\"'" and token[-1] == token[0]:
                key = token[1:-1]
                if not isinstance(current, dict) or key not in current:
                    return False, None
                current = current[key]
            else:
                try:
                    position = int(token)
                except ValueError:
                    return False, None
                if not isinstance(current, list) or not (-len(current) <= position < len(current)):
                    return False, None
                current = current[position]
        else:
            return False, None
    return True, current


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _type_matches(declared: str, value: Any) -> bool:
    declared = (declared or "").strip().lower()
    if not declared:
        return True
    if declared in {"number", "int", "integer", "float", "double", "数字", "数值"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared in {"string", "str", "text", "文本", "字符串"}:
        return isinstance(value, str)
    if declared in {"boolean", "bool", "布尔"}:
        return isinstance(value, bool)
    if declared in {"array", "list", "数组"}:
        return isinstance(value, list)
    if declared in {"object", "dict", "对象"}:
        return isinstance(value, dict)
    return True


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def parse_mapping_content(content: str) -> tuple[str, list[dict[str, Any]]]:
    """解析 JSON 或 CSV 字段映射文件，返回 (端点, 规范化条目)。"""
    text = (content or "").lstrip("﻿").strip()
    if not text:
        raise ValueError("映射文件为空")
    endpoint = ""
    raw_mappings: list[dict[str, Any]] = []
    if text[0] in "[{":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"映射文件不是合法 JSON：{exc.msg}") from exc
        if isinstance(payload, dict):
            endpoint = str(_pick(payload, "endpoint", "端点", "目标端点") or "")
            candidate = _pick(payload, "mappings", "fields", "映射", "字段映射")
            if candidate is None and ("field" in payload or "字段" in payload):
                candidate = [payload]
            if not isinstance(candidate, list):
                raise ValueError("映射文件缺少 mappings 数组")
            raw_mappings = [item for item in candidate if isinstance(item, dict)]
        elif isinstance(payload, list):
            raw_mappings = [item for item in payload if isinstance(item, dict)]
        else:
            raise ValueError("映射文件顶层必须是对象或数组")
    else:
        endpoint = ""
        data_lines: list[str] = []
        # CSV 里允许用 #endpoint 注释行声明端点，其余 # 注释行忽略。
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#endpoint"):
                endpoint = stripped.split(":", 1)[-1].strip()
                continue
            if stripped.startswith("#") or not stripped:
                continue
            data_lines.append(line)
        reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
        for row in reader:
            if row:
                raw_mappings.append(dict(row))
    if not raw_mappings:
        raise ValueError("映射文件没有可用条目")

    mappings: list[dict[str, Any]] = []
    for item in raw_mappings:
        field = str(_pick(item, "field", "字段", "字段别名", "别名") or "").strip()
        path = str(_pick(item, "path", "JSON路径", "路径", "json_path") or "").strip()
        if not field or not path:
            raise ValueError("每条映射都必须包含字段与 JSON 路径")
        if not path.startswith("$"):
            raise ValueError(f"字段 {field} 的 JSON 路径必须以 $ 开头：{path}")
        declared = str(_pick(item, "type", "数据类型", "类型") or "").strip()
        default = _pick(item, "default", "默认值")
        mappings.append({
            "字段别名": field,
            "JSON路径": path,
            "数据类型": declared,
            "默认值": default,
        })
    return endpoint, mappings


class ParseImportService:
    """字段映射导入与预检。"""

    # ---------- 查询 ----------

    def samples(self) -> list[dict[str, Any]]:
        return [dict(row) for row in store.rows("_parse_samples")]

    def _sample_row(self, endpoint: str) -> dict[str, Any] | None:
        for row in store.rows("_parse_samples"):
            if row.get("端点") == endpoint:
                return row
        return None

    def _import_by_id(self, import_id: int) -> dict[str, Any] | None:
        for row in store.rows("_parse_imports"):
            if int(row.get("id", 0)) == int(import_id):
                return row
        return None

    def _version(self, number: int) -> dict[str, Any] | None:
        for row in store.rows("_mapping_versions"):
            if int(row.get("版本", 0)) == int(number):
                return row
        return None

    def _latest_version(self, endpoint: str) -> dict[str, Any] | None:
        candidates = [
            row for row in store.rows("_mapping_versions")
            if row.get("端点") == endpoint and row.get("状态") in {"已生效", "冲突待更正"}
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: int(row.get("版本", 0)))

    def _cache_for_version(self, endpoint: str, number: int) -> dict[str, Any] | None:
        for row in store.rows("_cache_entries"):
            if row.get("端点") == endpoint and int(row.get("映射版本", 0)) == int(number):
                return row
        return None

    def _conflicts_for(self, import_id: int) -> list[dict[str, Any]]:
        return [
            row for row in store.rows("_parse_conflicts")
            if int(row.get("导入ID", 0)) == int(import_id)
        ]

    def _todos_for(self, import_id: int) -> list[dict[str, Any]]:
        return [
            row for row in store.rows("_delivery_todos")
            if int(row.get("导入ID", 0) or 0) == int(import_id)
        ]

    def _delivery_rows(self) -> list[dict[str, Any]]:
        return sorted(store.rows("_delivery_runs"), key=lambda row: int(row.get("id", 0)))

    def list_imports(self) -> list[dict[str, Any]]:
        return [self._import_summary(row) for row in store.rows("_parse_imports")]

    def _import_summary(self, imp: dict[str, Any]) -> dict[str, Any]:
        conflicts = self._conflicts_for(int(imp["id"]))
        return {
            "id": imp["id"],
            "导入编号": imp["导入编号"],
            "文件名": imp["文件名"],
            "端点": imp["端点"],
            "状态": imp["状态"],
            "文件指纹": imp["文件指纹"],
            "条目数": len(imp["条目"]),
            "冲突数": len(conflicts),
            "已确认范围": list(imp.get("已确认范围", [])),
            "待核对范围": list(imp.get("待核对范围", [])),
            "冲突字段": list(imp.get("冲突字段", [])),
            "生效版本": imp.get("生效版本"),
            "中断说明": imp.get("中断说明", ""),
            "上传时间": imp.get("上传时间"),
            "确认时间": imp.get("确认时间"),
        }

    def get_import(self, import_id: int) -> dict[str, Any] | None:
        imp = self._import_by_id(import_id)
        if imp is None:
            return None
        version = self._version(int(imp["生效版本"])) if imp.get("生效版本") else None
        cache = self._cache_for_version(imp["端点"], int(version["版本"])) if version else None
        return {
            "导入": copy.deepcopy(imp),
            "冲突": copy.deepcopy(self._conflicts_for(import_id)),
            "映射版本": copy.deepcopy(version),
            "缓存结果": copy.deepcopy(cache),
            "投递待办": copy.deepcopy(self._todos_for(import_id)),
        }

    def state(self) -> dict[str, Any]:
        versions = sorted(
            (copy.deepcopy(row) for row in store.rows("_mapping_versions")),
            key=lambda row: int(row.get("版本", 0)),
        )
        sample = self.samples()
        first_sample = sample[0]["样本"] if sample else {}
        return {
            "样本": sample,
            "导入": self.list_imports(),
            "交付": self.list_deliveries(),
            "映射版本": versions,
            "缓存结果": copy.deepcopy(store.rows("_cache_entries")),
            "投递待办": copy.deepcopy(store.rows("_delivery_todos")),
            "冲突": copy.deepcopy(store.rows("_parse_conflicts")),
            "历史任务": self.list_tasks(),
            "交付对比": self.compare_deliveries()["交付"],
            "解析结果": self.cached_result(first_sample),
        }

    def cached_result(self, sample: dict[str, Any]) -> dict[str, Any]:
        """按最新已就绪缓存展示解析结果；旧缓存仍服务已领用任务。"""
        rows = [
            row for row in store.rows("_cache_entries")
            if row.get("状态") == "已就绪"
        ]
        if not rows:
            return {"样本": sample, "结果": {}}
        latest = max(rows, key=lambda row: int(row.get("映射版本", 0)))
        return {
            "样本": sample,
            "缓存条目": latest["缓存编号"],
            "映射版本": latest["映射版本"],
            "行": copy.deepcopy(latest["行"]),
            "列": list(latest["列"]),
        }

    def list_tasks(self) -> list[dict[str, Any]]:
        tasks = []
        for task in store.rows("_delivery_tasks"):
            tasks.append({
                "id": task["id"],
                "任务编号": task["任务编号"],
                "端点": task["端点"],
                "领用版本": task["领用版本"],
                "领用时间": task["领用时间"],
                "状态": task["状态"],
                "说明": task.get("说明", ""),
                "映射快照": copy.deepcopy(task.get("映射快照", {})),
            })
        return tasks

    # ---------- 导入预检 ----------

    def upload(
        self,
        *,
        endpoint: str,
        filename: str,
        content: str | None = None,
        mappings: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        endpoint = (endpoint or "").strip()
        sample_row = self._sample_row(endpoint)
        if sample_row is None:
            return None, f"端点 {endpoint or '（空）'} 没有同端点响应样本，无法预检"
        if mappings:
            content = json.dumps({"endpoint": endpoint, "mappings": mappings}, ensure_ascii=False)
        if not (content or "").strip():
            return None, "映射文件为空"
        try:
            file_endpoint, parsed = parse_mapping_content(content or "")
        except ValueError as exc:
            return None, str(exc)
        if file_endpoint and file_endpoint != endpoint:
            return None, f"文件声明的端点 {file_endpoint} 与导入端点 {endpoint} 不一致"

        # 同一端点 + 同一组映射只允许存在一条导入记录。
        canonical = sorted(
            (
                {
                    "字段": item["字段别名"],
                    "路径": item["JSON路径"],
                    "类型": item["数据类型"],
                    "默认值": item["默认值"],
                }
                for item in parsed
            ),
            key=lambda item: (item["字段"], item["路径"], item["类型"] or ""),
        )
        fingerprint = _fingerprint({"endpoint": endpoint, "mappings": canonical})
        for existing in store.rows("_parse_imports"):
            if existing["文件指纹"] != fingerprint:
                continue
            existing_id = int(existing["id"])
            if existing["状态"] == "确认中断":
                # 重提同一文件从上次中断处继续；已确认范围先重新核对，不会重复启效。
                result, resume_message = self.confirm(existing_id)
                if result is not None:
                    result["重复提交"] = True
                    result["续办"] = True
                    return result, (
                        f"同一文件已导入（{existing['导入编号']}），从确认中断处恢复；"
                        f"{resume_message}"
                    )
            result = self.get_import(existing_id) or {}
            result["重复提交"] = True
            result["续办"] = False
            return result, (
                f"同一文件已导入（{existing['导入编号']}），"
                "未重复登记，也不会重复启效"
            )

        sample = sample_row["样本"]
        import_id = _next_id(store.rows("_parse_imports"))
        entries: list[dict[str, Any]] = []
        for order, mapping in enumerate(parsed, start=1):
            entry = {
                "序号": order,
                "字段别名": mapping["字段别名"],
                "JSON路径": mapping["JSON路径"],
                "数据类型": mapping["数据类型"],
                "默认值": mapping["默认值"],
                "样本原值": None,
                "验证状态": "未验证",
                "校验说明": "",
                "结果": "待核对",
                "规则ID": None,
                "启效时间": None,
            }
            self._verify_entry(entry, sample)
            entries.append(entry)

        # 同一字段出现多个可验证路径时，全部保留为候选，不允许任何一条静默覆盖。
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            if entry["验证状态"] == "已验证":
                grouped.setdefault(entry["字段别名"], []).append(entry)
        conflict_fields: list[str] = []
        now = _now()
        for field, candidates in grouped.items():
            paths = {entry["JSON路径"] for entry in candidates}
            if len(paths) < 2:
                continue
            conflict_fields.append(field)
            for entry in candidates:
                entry["验证状态"] = "同名冲突"
                entry["校验说明"] = (
                    f"同名冲突：{len(paths)} 个 JSON 路径都映射到 {field}，"
                    "保留全部候选，等待人工更正"
                )
            conflict_id = _next_id(store.rows("_parse_conflicts"))
            previous = self._previous_definition(endpoint, field)
            conflict = {
                "id": conflict_id,
                "冲突编号": f"CONF-CSV-{conflict_id:04d}",
                "导入ID": import_id,
                "端点": endpoint,
                "字段": field,
                "候选": [
                    {
                        "JSON路径": entry["JSON路径"],
                        "样本原值": entry["样本原值"],
                        "数据类型": entry["数据类型"] or _infer_type(entry["样本原值"]),
                        "验证状态": "已验证",
                    }
                    for entry in candidates
                ],
                "原生效定义": copy.deepcopy(previous),
                "状态": "待更正",
                "处理结论": None,
                "创建时间": now,
            }
            store.rows("_parse_conflicts").append(conflict)
            self._record_schema_diff_conflict(conflict)

        status = "预检完成（存在同名冲突）" if conflict_fields else "预检完成"
        imp = {
            "id": import_id,
            "导入编号": f"IMP-CSV-{import_id:04d}",
            "文件名": filename or "mapping.json",
            "文件指纹": fingerprint,
            "端点": endpoint,
            "状态": status,
            "条目": entries,
            "冲突字段": conflict_fields,
            "已确认范围": [],
            "待核对范围": list(conflict_fields),
            "生效版本": None,
            "上传时间": now,
            "确认时间": None,
            "重复提交": False,
        }
        store.rows("_parse_imports").append(imp)
        result = self.get_import(import_id) or {}
        verified = sum(1 for entry in entries if entry["验证状态"] == "已验证")
        unverified = sum(1 for entry in entries if entry["验证状态"] == "未验证")
        if conflict_fields:
            message = (
                f"预检完成：{len(entries)} 条映射（已验证 {verified}，未验证 {unverified}），"
                f"{len(conflict_fields)} 个字段存在同名冲突"
                f"（{'、'.join(conflict_fields)}）；冲突候选与样本原值已保留，"
                "未验证的字段不会覆盖已生效定义"
            )
        elif unverified:
            message = (
                f"预检完成：{len(entries)} 条映射，其中 {unverified} 条未能用同端点样本验证，"
                "未验证的字段不会启效"
            )
        else:
            message = f"预检完成：{len(entries)} 条映射均可用同端点样本核对"
        return result, message

    def _previous_definition(self, endpoint: str, field: str) -> dict[str, Any] | None:
        # 只回溯真正生效过的版本；冲突待更正的版本本身不是可沿用的定义。
        versions = sorted(
            (
                row for row in store.rows("_mapping_versions")
                if row.get("端点") == endpoint and row.get("状态") == "已生效"
            ),
            key=lambda row: int(row.get("版本", 0)),
            reverse=True,
        )
        for version in versions:
            definition = version["定义"].get(field)
            if definition is None:
                continue
            return {
                "JSON路径": definition.get("JSON路径"),
                "样本原值": definition.get("样本原值"),
                "映射版本": version["版本"],
                "来源": definition.get("来源", "历史生效定义"),
            }
        return None

    def _verify_entry(self, entry: dict[str, Any], sample: dict[str, Any]) -> None:
        found, value = _resolve_path(sample, entry["JSON路径"])
        entry["样本原值"] = copy.deepcopy(value) if found else None
        if not found:
            entry["验证状态"] = "未验证"
            entry["校验说明"] = "同端点样本中不存在该 JSON 路径"
            return
        if value is None:
            entry["验证状态"] = "未验证"
            entry["校验说明"] = "路径存在但样本原值为空"
            return
        if not _type_matches(entry.get("数据类型", ""), value):
            entry["验证状态"] = "未验证"
            entry["校验说明"] = (
                f"样本类型 {_infer_type(value)} 与声明类型 {entry.get('数据类型')} 不一致"
            )
            return
        entry["验证状态"] = "已验证"
        if not entry.get("数据类型"):
            entry["数据类型"] = _infer_type(value)
        entry["校验说明"] = f"已用同端点样本核对，原值 {json.dumps(value, ensure_ascii=False)}"

    def _record_schema_diff_conflict(self, conflict: dict[str, Any]) -> None:
        rows = store.rows("schema_diff")
        candidates = " | ".join(
            f"{item['JSON路径']}={json.dumps(item['样本原值'], ensure_ascii=False)}"
            for item in conflict["候选"]
        )
        previous = conflict.get("原生效定义") or {}
        row = {
            "id": _next_id(rows),
            "记录编号": f"SCHE-CONF-{conflict['id']:04d}",
            "关联端点": conflict["端点"],
            "变更字段": conflict["字段"],
            "旧定义": (
                f"{previous['JSON路径']}={json.dumps(previous['样本原值'], ensure_ascii=False)}"
                if previous else "无生效定义"
            ),
            "新定义": candidates,
            "差异等级": "WARNING",
            "影响评估": f"导入{conflict['导入ID']}出现同名冲突，候选路径与原值已保留",
            "检测时间": _now()[:10],
            "冲突编号": conflict["冲突编号"],
            "status": "评估中", "pending": True, "abnormal": False,
        }
        rows.append(row)
        conflict["差异记录编号"] = row["记录编号"]

    # ---------- 确认与启效 ----------

    def confirm(
        self,
        import_id: int,
        *,
        interrupt_after: int | None = None,
        operator: str = "数据工程组",
    ) -> tuple[dict[str, Any] | None, str]:
        imp = self._import_by_id(import_id)
        if imp is None:
            return None, f"导入记录 {import_id} 不存在"
        if imp["状态"] == "已生效":
            result = self.get_import(import_id) or {}
            return result, "该导入已生效，重复确认不会再次启效"
        if imp["状态"] == "冲突待更正" and imp.get("生效版本"):
            result = self.get_import(import_id) or {}
            fields = "、".join(
                row["字段"] for row in self._conflicts_for(import_id) if row["状态"] == "待更正"
            )
            return result, (
                f"已确认范围已启效（映射版本 {imp['生效版本']}），"
                f"{fields} 仍待冲突更正；重复确认不会再次启效"
            )

        sample_row = self._sample_row(imp["端点"])
        if sample_row is None:
            return None, "同端点响应样本已不存在，无法确认"
        sample = sample_row["样本"]

        resumed = imp["状态"] == "确认中断"
        verify_report: list[dict[str, Any]] = []
        if resumed:
            # 中断恢复时先重新核对上一次已经确认过的范围，避免只凭状态继续。
            for entry in imp["条目"]:
                if entry["字段别名"] not in imp.get("已确认范围", []):
                    continue
                self._verify_entry(entry, sample)
                verify_report.append({
                    "字段别名": entry["字段别名"],
                    "JSON路径": entry["JSON路径"],
                    "样本原值": entry["样本原值"],
                    "验证状态": entry["验证状态"],
                })

        applied_now = 0
        # 可独立启效的字段先确认；同名冲突字段留到人工更正后再启效。
        ordered = sorted(
            imp["条目"],
            key=lambda entry: (entry["字段别名"] in imp.get("冲突字段", []), entry["序号"]),
        )
        for entry in ordered:
            if entry["结果"] in {"已确认", "已生效"}:
                continue
            if entry["验证状态"] != "已验证":
                continue
            if entry["字段别名"] in imp.get("冲突字段", []):
                continue
            entry["结果"] = "已确认"
            entry["启效时间"] = _now()
            if entry["字段别名"] not in imp["已确认范围"]:
                imp["已确认范围"].append(entry["字段别名"])
            if entry["字段别名"] in imp["待核对范围"]:
                imp["待核对范围"].remove(entry["字段别名"])
            applied_now += 1
            if interrupt_after is not None and applied_now >= interrupt_after:
                imp["状态"] = "确认中断"
                imp["中断说明"] = (
                    f"确认在已处理 {applied_now} 条后中断，"
                    f"已确认范围：{'、'.join(imp['已确认范围']) or '无'}"
                )
                imp["确认时间"] = _now()
                result = self.get_import(import_id) or {}
                result["中断"] = {
                    "已确认范围": list(imp["已确认范围"]),
                    "待核对范围": list(imp["待核对范围"]),
                    "说明": imp["中断说明"],
                }
                return result, (
                    f"确认已中断；已确认范围 {('、'.join(imp['已确认范围']) or '无')} 已保存，"
                    "重新提交同一文件不会重复启效，可再次确认继续"
                )

        if not imp.get("生效版本") and not any(
            entry["结果"] == "已确认" for entry in imp["条目"]
        ):
            # 一条都没通过样本核对：不生成新版本，已生效的映射保持原样。
            imp["状态"] = "无可用映射"
            imp["确认时间"] = _now()
            result = self.get_import(import_id) or {}
            result["恢复核对"] = verify_report
            return result, (
                "没有任何字段通过同端点样本核对，未启效也未改写现有映射；"
                "请修正 JSON 路径或数据类型后重新上传"
            )

        unresolved = [row for row in self._conflicts_for(import_id) if row["状态"] == "待更正"]
        version = self._publish_version(imp, operator=operator, unresolved=unresolved)
        imp["确认时间"] = _now()
        imp["状态"] = "冲突待更正" if version["状态"] == "冲突待更正" else "已生效"
        result = self.get_import(import_id) or {}
        result["恢复核对"] = verify_report
        if version["状态"] == "冲突待更正":
            pending_fields = "、".join(
                row["字段"] for row in self._conflicts_for(import_id) if row["状态"] == "待更正"
            ) or "同名字段"
            return result, (
                f"已确认的字段已启效并刷新缓存（版本 {version['版本']}）；"
                f"{pending_fields} 同名冲突仍待更正，冲突候选与样本原值保留，原生效定义继续沿用"
            )
        if version["状态"] == "已生效":
            return result, (
                f"导入已生效（映射版本 {version['版本']}），缓存已按确认记录刷新，"
                "投递待办已同步"
            )
        return result, f"导入已处理，映射版本 {version['版本']}"

    def _publish_version(
        self,
        imp: dict[str, Any],
        *,
        operator: str,
        unresolved: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # 部分生效的版本只追加一次；更正冲突时必须产出新版本，不能把旧定义回写成结论。
        force_new = bool(imp.pop("_force_new_version", False))
        if imp.get("生效版本") and not force_new:
            existing = self._version(int(imp["生效版本"]))
            if existing is not None:
                return existing

        endpoint = imp["端点"]
        base = self._latest_version(endpoint)
        definition: dict[str, dict[str, Any]] = copy.deepcopy(base["定义"]) if base else {}
        if base:
            for info in definition.values():
                info.setdefault("来源", "沿用生效定义")
                info["沿用自版本"] = base["版本"]

        for field, info in definition.items():
            info["冲突ID"] = None
            info["冲突待核对"] = False
        # 端点级待更正冲突：任何来源的同名冲突都让该字段保持待核对，不能被别的导入顺手清掉。
        pending_by_field = {
            row["字段"]: row
            for row in store.rows("_parse_conflicts")
            if row.get("端点") == endpoint and row["状态"] == "待更正"
        }
        for field, conflict in pending_by_field.items():
            if field in definition:
                definition[field]["冲突ID"] = conflict["id"]
                definition[field]["冲突待核对"] = True

        for entry in imp["条目"]:
            if entry["结果"] != "已确认":
                continue
            field = entry["字段别名"]
            if field in pending_by_field:
                # 仍有未解决冲突的字段不得覆盖当前生效定义。
                continue
            definition[field] = {
                "字段别名": field,
                "JSON路径": entry["JSON路径"],
                "数据类型": entry["数据类型"],
                "默认值": entry["默认值"],
                "规则ID": entry.get("规则ID"),
                "样本原值": entry["样本原值"],
                "验证": "通过",
                "来源": "导入确认",
                "导入ID": imp["id"],
                "冲突ID": entry.get("冲突ID"),
                "冲突待核对": False,
                "启效时间": _now(),
            }
            entry["结果"] = "已生效"

        version_no = max(
            (int(row.get("版本", 0)) for row in store.rows("_mapping_versions")),
            default=0,
        ) + 1
        # 版本状态以定义里是否还有待核对字段为准：跨导入的同名冲突同样算未解决。
        still_pending = any(info.get("冲突待核对") for info in definition.values())
        status = "冲突待更正" if still_pending else "已生效"
        fingerprint = _fingerprint({
            "endpoint": endpoint,
            "definition": {
                field: {"path": info.get("JSON路径"), "value": info.get("样本原值")}
                for field, info in sorted(definition.items())
            },
        })
        version = {
            "id": _next_id(store.rows("_mapping_versions")),
            "端点": endpoint,
            "版本": version_no,
            "定义": definition,
            "定义指纹": fingerprint,
            "原因": (
                f"导入{imp['导入编号']}确认启效"
                + ("（同名冲突待更正）" if unresolved else "")
            ),
            "导入ID": imp["id"],
            "状态": status,
            "创建时间": _now(),
        }
        store.rows("_mapping_versions").append(version)
        imp["生效版本"] = version_no

        # 只为本次真正确认的字段建/更新规则；沿用字段保留原规则。
        for field, info in definition.items():
            if info.get("来源") == "导入确认" and not info.get("规则ID"):
                rule = self._ensure_rule(imp["端点"], field, info, version_no)
                info["规则ID"] = rule["id"]
            elif info.get("来源") == "导入确认":
                self._touch_rule_version(imp["端点"], field, info, version_no)

        cache = self._refresh_cache(imp, version)
        self._sync_todos(imp, version, cache)
        self._sync_cache_policy(imp["端点"], version, cache)
        self._sync_delivery_pipeline(imp["端点"], version)
        self._record_audit(imp, version, operator)
        return version

    def _ensure_rule(
        self,
        endpoint: str,
        field: str,
        info: dict[str, Any],
        version_no: int,
    ) -> dict[str, Any]:
        rows = store.rows("response_parse")
        for rule in rows:
            if (
                rule.get("目标端点") == endpoint
                and rule.get("字段别名") == field
                and rule.get("JSON路径") == info.get("JSON路径")
                and rule.get("status") == "生效中"
            ):
                rule["映射版本"] = version_no
                rule["验证状态"] = "已验证"
                rule["样本原值"] = info.get("样本原值")
                info["规则ID"] = rule["id"]
                return rule
        rule_id = _next_id(rows)
        order = sum(1 for row in rows if row.get("目标端点") == endpoint) + 1
        rule = {
            "id": rule_id,
            "规则编号": f"RESP-CSV-{field.upper()}-{rule_id:02d}",
            "目标端点": endpoint,
            "JSON路径": info.get("JSON路径"),
            "字段别名": field,
            "数据类型": info.get("数据类型") or _infer_type(info.get("样本原值")),
            "默认值": info.get("默认值") if info.get("默认值") is not None else "",
            "解析顺序": str(order),
            "映射描述": f"导入确认：{field} 从 {info.get('JSON路径')} 取值（版本 {version_no}）",
            "映射版本": version_no,
            "验证状态": "已验证",
            "样本原值": info.get("样本原值"),
            "来源导入": info.get("导入ID"),
            "冲突ID": info.get("冲突ID"),
            "status": "生效中", "pending": False, "abnormal": False,
        }
        # 同一字段若换了新路径，旧规则保留为历史但不再生效，已领用任务仍可用快照。
        for existing in rows:
            if (
                existing.get("目标端点") == endpoint
                and existing.get("字段别名") == field
                and existing.get("JSON路径") != info.get("JSON路径")
                and existing.get("status") == "生效中"
            ):
                existing["status"] = "已废弃"
                existing["pending"] = False
        rows.append(rule)
        return rule

    def _touch_rule_version(
        self,
        endpoint: str,
        field: str,
        info: dict[str, Any],
        version_no: int,
    ) -> None:
        for rule in store.rows("response_parse"):
            if int(rule.get("id", 0)) == int(info.get("规则ID") or 0):
                rule["映射版本"] = version_no
                rule["样本原值"] = info.get("样本原值")
                return

    def _refresh_cache(self, imp: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
        existing = self._cache_for_version(imp["端点"], int(version["版本"]))
        if existing is not None:
            return existing
        sample_row = self._sample_row(imp["端点"])
        sample = sample_row["样本"] if sample_row else {}
        columns: list[str] = []
        values: dict[str, Any] = {}
        sources: dict[str, dict[str, Any]] = {}
        for field, info in version["定义"].items():
            path = info.get("JSON路径") or ""
            found, current = _resolve_path(sample, path)
            value = copy.deepcopy(current) if found else info.get("样本原值")
            columns.append(field)
            values[field] = value
            sources[field] = {
                "JSON路径": path,
                "样本原值": copy.deepcopy(info.get("样本原值")),
                "输出值": copy.deepcopy(value),
                "规则ID": info.get("规则ID"),
                "导入ID": info.get("导入ID"),
                "来源": info.get("来源", "沿用生效定义"),
                "冲突ID": info.get("冲突ID"),
                "冲突待核对": bool(info.get("冲突待核对")),
            }
        cache = {
            "id": _next_id(store.rows("_cache_entries")),
            "缓存编号": f"CACHE-CSV-V{version['版本']}",
            "端点": imp["端点"],
            "映射版本": version["版本"],
            "导入ID": imp["id"],
            "定义指纹": version["定义指纹"],
            "列": columns,
            "行": [values],
            "字段来源": sources,
            "确认范围": list(imp.get("已确认范围", [])),
            "状态": "已就绪",
            "刷新时间": _now(),
        }
        store.rows("_cache_entries").append(cache)
        return cache

    def _sync_todos(
        self,
        imp: dict[str, Any],
        version: dict[str, Any],
        cache: dict[str, Any],
    ) -> None:
        rows = store.rows("_delivery_todos")
        for todo in rows:
            if (
                todo.get("端点") == imp["端点"]
                and todo.get("状态") == "待投递"
                and int(todo.get("映射版本", 0)) != int(version["版本"])
            ):
                todo["状态"] = "已被新版本替代"
        for field in cache["列"]:
            duplicate = any(
                todo.get("端点") == imp["端点"]
                and todo.get("字段") == field
                and int(todo.get("映射版本", 0)) == int(version["版本"])
                for todo in rows
            )
            if duplicate:
                continue
            source = cache["字段来源"][field]
            todo_id = _next_id(rows)
            rows.append({
                "id": todo_id,
                "待办编号": f"TODO-CSV-{todo_id:04d}",
                "端点": imp["端点"],
                "字段": field,
                "映射版本": version["版本"],
                "缓存条目": cache["缓存编号"],
                "导入ID": imp["id"],
                "冲突ID": source.get("冲突ID"),
                "核对状态": "冲突待核对" if source.get("冲突待核对") else "已验证",
                "状态": "待投递",
                "投递编号": None,
                "创建时间": _now(),
            })

    def _sync_cache_policy(
        self,
        endpoint: str,
        version: dict[str, Any],
        cache: dict[str, Any],
    ) -> None:
        for policy in store.rows("data_cache"):
            if policy.get("目标端点") != endpoint:
                continue
            policy["映射版本"] = version["版本"]
            policy["缓存键"] = f"parse:{endpoint}:v{version['版本']}"
            policy["最后刷新"] = cache["刷新时间"]
            policy["命中次数"] = int(policy.get("命中次数", 0) or 0) + 1

    def _sync_delivery_pipeline(self, endpoint: str, version: dict[str, Any]) -> None:
        for delivery in store.rows("data_delivery"):
            if delivery.get("数据来源") == endpoint:
                delivery["映射版本"] = version["版本"]
                delivery["分区策略"] = f"mapping=v{version['版本']}"

    def _record_audit(
        self,
        imp: dict[str, Any],
        version: dict[str, Any],
        operator: str,
    ) -> None:
        rows = store.rows("pipeline_audit")
        rows.append({
            "id": _next_id(rows),
            "记录编号": f"PIPE-IMPORT-{imp['id']:04d}",
            "管道名称": f"响应解析导入 {imp['导入编号']}",
            "操作人": operator,
            "操作类型": "字段映射启效",
            "变更摘要": (
                f"版本 {version['版本']}："
                + "、".join(sorted(version["定义"].keys()))
            ),
            "操作时间": _now(),
            "来源节点": "response_parse",
            "审计结论": "已记录",
            "status": "已记录", "pending": False, "abnormal": False,
        })

    # ---------- 冲突更正 ----------

    def resolve_conflict(
        self,
        import_id: int,
        conflict_id: int,
        *,
        chosen_path: str,
        note: str | None = None,
        operator: str = "数据工程组",
    ) -> tuple[dict[str, Any] | None, str]:
        imp = self._import_by_id(import_id)
        if imp is None:
            return None, f"导入记录 {import_id} 不存在"
        conflict = next(
            (row for row in self._conflicts_for(import_id) if int(row["id"]) == int(conflict_id)),
            None,
        )
        if conflict is None:
            return None, f"冲突 {conflict_id} 不属于导入 {import_id}"
        if conflict["状态"] == "已更正":
            result = self.get_import(import_id) or {}
            return result, (
                f"冲突 {conflict['冲突编号']} 已更正，不能重复更正；"
                "现有交付仍按各自领用的映射版本取值"
            )
        candidates = conflict["候选"]
        chosen = next((item for item in candidates if item["JSON路径"] == chosen_path), None)
        if chosen is None:
            paths = "、".join(item["JSON路径"] for item in candidates)
            return None, f"更正路径必须是冲突候选之一：{paths}"

        field = conflict["字段"]
        chosen_entry = next(
            (
                entry for entry in imp["条目"]
                if entry["字段别名"] == field and entry["JSON路径"] == chosen_path
            ),
            None,
        )
        if chosen_entry is None:
            return None, f"导入条目中找不到 {field} 与 {chosen_path} 的映射"
        for entry in imp["条目"]:
            if entry["字段别名"] != field or entry["JSON路径"] == chosen_path:
                continue
            entry["结果"] = "已否决"
            entry["校验说明"] = f"同名冲突由人工更正：{field} 采用 {chosen_path}"

        chosen_entry["结果"] = "已确认"
        chosen_entry["冲突ID"] = conflict["id"]
        old_definition = conflict.get("原生效定义")
        resolution = {
            "选择路径": chosen_path,
            "选择样本原值": chosen["样本原值"],
            "未选择路径": [
                {"JSON路径": item["JSON路径"], "样本原值": item["样本原值"]}
                for item in candidates if item["JSON路径"] != chosen_path
            ],
            "原生效定义": copy.deepcopy(old_definition),
            "结论": (
                f"{field} 采用 {chosen_path}（样本原值 "
                f"{json.dumps(chosen['样本原值'], ensure_ascii=False)}）"
                + (
                    "，不再沿用原定义 "
                    f"{old_definition['JSON路径']}"
                    if old_definition else ""
                )
            ),
            "说明": (note or "").strip(),
            "处理人": operator,
            "处理时间": _now(),
            "导入ID": import_id,
        }
        conflict["状态"] = "已更正"
        conflict["处理结论"] = resolution
        if field in imp["待核对范围"]:
            imp["待核对范围"].remove(field)
        if field not in imp["已确认范围"]:
            imp["已确认范围"].append(field)

        unresolved = [row for row in self._conflicts_for(import_id) if row["状态"] == "待更正"]
        # 更正会产出新版本；不能复用导入原来的部分生效版本。
        imp["生效版本"] = None
        imp["_force_new_version"] = True
        version = self._publish_version(imp, operator=operator, unresolved=unresolved)
        resolution["生效版本"] = version["版本"]
        imp["状态"] = "冲突待更正" if version["状态"] == "冲突待更正" else "已生效"
        self._confirm_schema_diff(conflict)
        if version["状态"] == "冲突待更正":
            result = self.get_import(import_id) or {}
            pending = "、".join(
                row["字段"] for row in self._conflicts_for(import_id) if row["状态"] == "待更正"
            ) or "其他导入的同名字段"
            return result, (
                f"冲突 {conflict['冲突编号']} 已更正：{resolution['结论']}；"
                f"映射版本 {version['版本']} 已启效，缓存与投递待办已刷新；"
                f"{pending} 仍有同名冲突待更正"
            )
        result = self.get_import(import_id) or {}
        return result, (
            f"冲突 {conflict['冲突编号']} 已更正：{resolution['结论']}；"
            f"映射版本 {version['版本']} 已生效，缓存与投递待办已刷新"
        )

    def _confirm_schema_diff(self, conflict: dict[str, Any]) -> None:
        code = conflict.get("差异记录编号")
        for row in store.rows("schema_diff"):
            if row.get("记录编号") == code:
                row["status"] = "已确认"
                row["pending"] = False
                row["影响评估"] = conflict["处理结论"]["结论"]
                return

    # ---------- 投递与回查 ----------

    def _resolve_pipeline(self, pipeline_id: int | None) -> dict[str, Any] | None:
        rows = store.rows("data_delivery")
        if pipeline_id is not None:
            return next((row for row in rows if int(row.get("id", 0)) == int(pipeline_id)), None)
        cached_endpoints = {row.get("端点") for row in store.rows("_cache_entries")}
        with_cache = [row for row in rows if row.get("数据来源") in cached_endpoints]
        if with_cache:
            return with_cache[0]
        return rows[0] if rows else None

    def run_delivery(
        self,
        *,
        pipeline_id: int | None = None,
        note: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        pipeline = self._resolve_pipeline(pipeline_id)
        if pipeline is None:
            if pipeline_id is not None:
                return None, f"投递管道 {pipeline_id} 不存在"
            return None, "没有可用的投递管道"
        endpoint = pipeline.get("数据来源", "")
        cache_rows = [
            row for row in store.rows("_cache_entries")
            if row.get("端点") == endpoint and row.get("状态") == "已就绪"
        ]
        if not cache_rows:
            return None, f"端点 {endpoint} 还没有已确认的缓存结果，不能投递"
        cache = max(cache_rows, key=lambda row: int(row.get("映射版本", 0)))
        version = self._version(int(cache["映射版本"]))
        if version is None:
            return None, "缓存结果对应的映射版本不存在"
        # 只回查缓存快照建立时记下的冲突，后续导入不会改写历史交付的结论。
        linked_ids = {
            int(source["冲突ID"])
            for source in cache["字段来源"].values()
            if source.get("冲突ID")
        }
        conflicts = [
            row for row in store.rows("_parse_conflicts")
            if row.get("端点") == endpoint
            and row.get("字段") in cache["列"]
            and int(row.get("id", 0)) in linked_ids
        ]
        conflict_by_field = {row["字段"]: row for row in conflicts}

        fields: dict[str, dict[str, Any]] = {}
        pending_fields: list[str] = []
        for field in cache["列"]:
            source = cache["字段来源"][field]
            origin = source.get("来源", "沿用生效定义")
            conflict = conflict_by_field.get(field)
            pending = bool(conflict and conflict["状态"] == "待更正")
            if pending:
                basis = (
                    f"同名冲突待更正，沿用原生效定义 {source['JSON路径']}"
                    if origin != "导入确认"
                    else f"同名冲突待更正（{conflict['冲突编号']}）"
                )
                pending_fields.append(field)
            elif origin == "导入确认":
                basis = "导入确认后启效"
            else:
                basis = f"沿用版本 {version['版本']} 的生效定义"
            fields[field] = {
                "值": copy.deepcopy(cache["行"][0].get(field)),
                "JSON路径": source["JSON路径"],
                "样本原值": source.get("样本原值"),
                "来源": origin,
                "交付状态": "已交付",
                "待核对": pending,
                "冲突ID": conflict["id"] if conflict else source.get("冲突ID"),
                "依据": basis,
            }

        run_id = _next_id(store.rows("_delivery_runs"))
        run = {
            "id": run_id,
            "投递编号": f"DELIV-CSV-{run_id:04d}",
            "管道ID": pipeline["id"],
            "端点": endpoint,
            "映射版本": cache["映射版本"],
            "缓存条目": cache["缓存编号"],
            "定义指纹": cache["定义指纹"],
            "列": list(cache["列"]),
            "字段": fields,
            "待核对字段": pending_fields,
            "冲突": [conflict["id"] for conflict in conflicts],
            "映射定义": copy.deepcopy(version["定义"]),
            "状态": "已交付",
            "说明": (note or "按缓存结果取列").strip(),
            "投递时间": _now(),
        }
        store.rows("_delivery_runs").append(run)

        # 实际投递完成后回写待办和管道；历史交付记录保持不可变。
        for todo in store.rows("_delivery_todos"):
            if (
                todo.get("端点") == endpoint
                and int(todo.get("映射版本", 0)) == int(cache["映射版本"])
                and todo.get("状态") == "待投递"
            ):
                todo["状态"] = "已投递"
                todo["投递编号"] = run["投递编号"]
                todo["投递时间"] = run["投递时间"]
        pipeline["最后投递"] = run["投递时间"]
        pipeline["映射版本"] = cache["映射版本"]
        pipeline["分区策略"] = f"mapping=v{cache['映射版本']}"
        pipeline["status"] = "投递中"
        pipeline["pending"] = True
        self._log_delivery(run)
        trace = self.trace_delivery(run_id)
        return trace, (
            f"投递 {run['投递编号']} 已按映射版本 {run['映射版本']} 的缓存结果取列"
            + (f"；{'、'.join(pending_fields)} 仍待冲突核对" if pending_fields else "")
        )

    def _log_delivery(self, run: dict[str, Any]) -> None:
        rows = store.rows("call_log")
        rows.append({
            "id": _next_id(rows),
            "记录编号": f"CALL-DELIV-PARSE-{run['id']:04d}",
            "接口端点": run["端点"],
            "请求参数": f"mapping=v{run['映射版本']};columns={','.join(run['列'])}",
            "响应状态": "200",
            "响应耗时(ms)": 120,
            "返回行数": 1,
            "错误信息": "",
            "调用时间": run["投递时间"],
            "status": "成功", "pending": False, "abnormal": False,
        })

    def list_deliveries(self) -> list[dict[str, Any]]:
        return [self._run_summary(row) for row in self._delivery_rows()]

    def _run_summary(self, run: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": run["id"],
            "投递编号": run["投递编号"],
            "端点": run["端点"],
            "映射版本": run["映射版本"],
            "缓存条目": run["缓存条目"],
            "列": list(run["列"]),
            "字段": copy.deepcopy(run["字段"]),
            "待核对字段": list(run.get("待核对字段", [])),
            "状态": run["状态"],
            "投递时间": run["投递时间"],
        }

    def _run_by_id(self, run_id: int) -> dict[str, Any] | None:
        for row in store.rows("_delivery_runs"):
            if int(row.get("id", 0)) == int(run_id):
                return row
        return None

    def trace_delivery(self, run_id: int) -> dict[str, Any] | None:
        run = self._run_by_id(run_id)
        if run is None:
            return None
        version = self._version(int(run["映射版本"]))
        cache = self._cache_for_version(run["端点"], int(run["映射版本"]))
        trace_conflicts: list[dict[str, Any]] = []
        for conflict_id in run.get("冲突", []):
            conflict = next(
                (row for row in store.rows("_parse_conflicts") if int(row["id"]) == int(conflict_id)),
                None,
            )
            if conflict is None:
                continue
            field = conflict["字段"]
            used = run["字段"].get(field, {})
            resolution = conflict.get("处理结论")
            trace_conflicts.append({
                "冲突编号": conflict["冲突编号"],
                "字段": field,
                "候选": copy.deepcopy(conflict["候选"]),
                "原生效定义": copy.deepcopy(conflict.get("原生效定义")),
                "状态": conflict["状态"],
                "处理结论": copy.deepcopy(resolution),
                "本次交付使用路径": used.get("JSON路径"),
                "本次交付依据": used.get("依据"),
                "是否采用处理结论": bool(
                    resolution and resolution.get("选择路径") == used.get("JSON路径")
                ),
            })
        return {
            "投递": copy.deepcopy(run),
            "映射版本": copy.deepcopy(version),
            "缓存结果": copy.deepcopy(cache),
            "字段来源": copy.deepcopy(run["字段"]),
            "使用列": list(run["列"]),
            "待核对字段": list(run.get("待核对字段", [])),
            "冲突回查": trace_conflicts,
            "历史兼容说明": (
                "本交付使用领用时的映射快照，后续版本变更不影响已投递字段来源"
                if run.get("说明") == "历史领用，按原映射取值"
                else "本交付只使用单一映射版本及其缓存结果"
            ),
        }

    def compare_deliveries(self) -> dict[str, Any]:
        runs = self._delivery_rows()
        rows = []
        for run in runs:
            fields = run["字段"]
            rows.append({
                "投递编号": run["投递编号"],
                "映射版本": run["映射版本"],
                "total": copy.deepcopy(fields.get("total")),
                "currency": copy.deepcopy(fields.get("currency")),
                "待核对字段": list(run.get("待核对字段", [])),
                "状态": run["状态"],
                "投递时间": run["投递时间"],
            })
        return {"交付": rows, "数量": len(rows)}


parse_import_service = ParseImportService()
