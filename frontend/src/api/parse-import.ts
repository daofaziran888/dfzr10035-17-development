/** 响应解析导入：字段映射文件预检、确认、冲突更正与交付回查。 */
import { request } from './client'

export type MappingEntry = {
  序号: number
  字段别名: string
  JSON路径: string
  数据类型: string
  默认值: unknown
  样本原值: unknown
  验证状态: string
  校验说明: string
  结果: string
  规则ID: number | null
  启效时间: string | null
}

export type ImportRecord = {
  id: number
  导入编号: string
  文件名: string
  端点: string
  状态: string
  文件指纹: string
  条目: MappingEntry[]
  冲突字段: string[]
  已确认范围: string[]
  待核对范围: string[]
  生效版本: number | null
  中断说明?: string
  上传时间: string
  确认时间: string | null
}

export type ConflictCandidate = {
  JSON路径: string
  样本原值: unknown
  数据类型: string
  验证状态: string
}

export type ConflictRecord = {
  id: number
  冲突编号: string
  导入ID: number
  端点: string
  字段: string
  候选: ConflictCandidate[]
  原生效定义: Record<string, unknown> | null
  状态: string
  处理结论: Record<string, unknown> | null
  差异记录编号?: string
  创建时间: string
}

export type FieldSource = {
  值?: unknown
  JSON路径: string
  样本原值: unknown
  来源?: string
  交付状态?: string
  待核对?: boolean
  冲突ID?: number | null
  依据?: string
}

export type ImportDetail = {
  导入: ImportRecord
  冲突: ConflictRecord[]
  映射版本: Record<string, unknown> | null
  缓存结果: {
  缓存编号: string
  映射版本: number
  列: string[]
  行: Record<string, unknown>[]
  字段来源: Record<string, FieldSource>
  确认范围: string[]
  刷新时间: string
  } | null
  投递待办: {
  待办编号: string
  字段: string
  映射版本: number
  核对状态: string
  状态: string
  }[]
  重复提交?: boolean
  续办?: boolean
  中断?: { 已确认范围: string[]; 待核对范围: string[]; 说明: string }
  恢复核对?: { 字段别名: string; JSON路径: string; 样本原值: unknown; 验证状态: string }[]
}

export type DeliverySummary = {
  id: number
  投递编号: string
  映射版本: number
  缓存条目: string
  列: string[]
  字段: Record<string, FieldSource>
  待核对字段: string[]
  状态: string
  投递时间: string
}

export type DeliveryTrace = {
  投递: DeliverySummary & { 定义指纹: string; 说明: string }
  字段来源: Record<string, FieldSource>
  使用列: string[]
  待核对字段: string[]
  冲突回查: {
  冲突编号: string
  字段: string
  候选: ConflictCandidate[]
  原生效定义: Record<string, unknown> | null
  状态: string
  处理结论: Record<string, unknown> | null
  本次交付使用路径: string
  本次交付依据: string
  是否采用处理结论: boolean
  }[]
}

export type ImportState = {
  样本: { 样本编号: string; 端点: string; 样本: Record<string, unknown>; 采集时间: string }[]
  导入: (Omit<ImportRecord, '条目'> & { 条目数: number; 冲突数: number })[]
  交付: DeliverySummary[]
  映射版本: { 版本: number; 状态: string; 定义: Record<string, FieldSource>; 原因: string }[]
  缓存结果: {
  缓存编号: string
  映射版本: number
  列: string[]
  行: Record<string, unknown>[]
  字段来源: Record<string, FieldSource>
  }[]
  投递待办: { 待办编号: string; 字段: string; 映射版本: number; 核对状态: string; 状态: string }[]
  冲突: ConflictRecord[]
  历史任务: {
  任务编号: string
  端点: string
  领用版本: number
  状态: string
  说明: string
  映射快照: Record<string, FieldSource>
  }[]
  交付对比: CompareRow[]
  解析结果: {
  样本: Record<string, unknown>
  缓存条目?: string
  映射版本?: number
  行: Record<string, unknown>
  列: string[]
  }
}

export type CompareRow = {
  投递编号: string
  映射版本: number
  total: FieldSource | null
  currency: FieldSource | null
  待核对字段: string[]
  状态: string
  投递时间: string
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json()
  if (!response.ok) {
    const detail = (payload as { detail?: string }).detail
    throw new Error(detail || `接口返回 ${response.status}`)
  }
  return payload as T
}

export async function loadImportState(): Promise<ImportState> {
  return readJson<ImportState>(await request('/api/parse-imports/state'))
}

export async function uploadMapping(payload: {
  endpoint: string
  filename: string
  content: string
}): Promise<ImportDetail> {
  return readJson<ImportDetail>(
    await request('/api/parse-imports/upload', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  )
}

export async function loadImport(importId: number): Promise<ImportDetail> {
  return readJson<ImportDetail>(await request(`/api/parse-imports/${importId}`))
}

export async function confirmImport(
  importId: number,
  interruptAfter?: number | null,
): Promise<ImportDetail> {
  return readJson<ImportDetail>(
    await request(`/api/parse-imports/${importId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ interrupt_after: interruptAfter ?? null }),
    }),
  )
}

export async function resolveConflict(
  importId: number,
  conflictId: number,
  path: string,
  note?: string,
): Promise<ImportDetail> {
  return readJson<ImportDetail>(
    await request(`/api/parse-imports/${importId}/conflicts/${conflictId}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ path, note }),
    }),
  )
}

export async function runDelivery(pipelineId?: number): Promise<DeliveryTrace> {
  return readJson<DeliveryTrace>(
    await request('/api/parse-imports/deliveries/run', {
      method: 'POST',
      body: JSON.stringify({ pipeline_id: pipelineId ?? null }),
    }),
  )
}

export async function loadDelivery(runId: number): Promise<DeliveryTrace> {
  return readJson<DeliveryTrace>(await request(`/api/parse-imports/deliveries/${runId}`))
}

export async function compareDeliveries(): Promise<{ 交付: CompareRow[]; 数量: number }> {
  return readJson(await request('/api/parse-imports/deliveries/compare'))
}
