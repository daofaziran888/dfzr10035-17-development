<template>
  <section class="import-panel" data-testid="parse-import-panel">
    <header class="import-head">
      <div>
        <h3>字段映射导入</h3>
        <p class="page-desc">
          同一端点的映射文件先用同端点响应样本预检；同名冲突保留全部路径与样本原值，
          未验证的字段不会覆盖已生效定义。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="import-refresh" type="button" @click="reload">刷新</button>
        <button class="btn" data-testid="import-deliver" type="button" @click="deliver">
          运行一笔投递
        </button>
      </div>
    </header>

    <p v-if="message" class="import-message" data-testid="import-message">{{ message }}</p>

    <form class="import-form" @submit.prevent="upload">
      <label>
        <span>端点</span>
        <input v-model="endpoint" data-testid="import-endpoint" />
      </label>
      <label>
        <span>文件名</span>
        <input v-model="filename" data-testid="import-filename" />
      </label>
      <label class="import-content">
        <span>映射文件内容（JSON 或 CSV）</span>
        <textarea v-model="content" data-testid="import-content" rows="6" />
      </label>
      <div class="page-actions">
        <button class="btn primary" data-testid="import-upload" type="submit" :disabled="busy">
          预检映射文件
        </button>
        <button class="btn" data-testid="import-sample" type="button" @click="fillSample">
          填入同名冲突样例
        </button>
      </div>
    </form>

    <div v-if="sample" class="import-sample">
      <span class="import-label">同端点响应样本</span>
      <code data-testid="import-sample-value">{{ JSON.stringify(sample.样本) }}</code>
    </div>

    <div class="import-grid">
      <section>
        <h4>已确认导入记录</h4>
        <table v-if="state?.导入.length" class="data-table">
          <thead>
            <tr>
              <th>导入编号</th><th>文件名</th><th>状态</th><th>已确认范围</th>
              <th>待核对范围</th><th>冲突数</th><th>生效版本</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in state?.导入 ?? []" :key="row.id" :data-testid="`import-row-${row.id}`">
              <td>{{ row.导入编号 }}</td>
              <td>{{ row.文件名 }}</td>
              <td>{{ row.状态 }}</td>
              <td>{{ row.已确认范围.join('、') || '—' }}</td>
              <td>{{ row.待核对范围.join('、') || '—' }}</td>
              <td>{{ row.冲突数 }}</td>
              <td>{{ row.生效版本 ?? '—' }}</td>
              <td class="row-actions">
                <button class="link" type="button" :data-testid="`import-confirm-${row.id}`" @click="confirm(row.id)">
                  确认启效
                </button>
                <button class="link" type="button" :data-testid="`import-open-${row.id}`" @click="open(row.id)">
                  查看预检
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="import-hint">还没有导入记录，可先上传同一端点的字段映射文件。</p>
      </section>

      <section v-if="state" class="import-cache">
        <h4>缓存解析结果</h4>
        <p class="import-hint">
          样本 {{ JSON.stringify(state.解析结果.样本) }} →
          {{ JSON.stringify(state.解析结果.行) }}
          （缓存 {{ state.解析结果.缓存条目 ?? '—' }}，版本 {{ state.解析结果.映射版本 ?? '—' }}）
        </p>
        <p v-for="task in state.历史任务" :key="task.任务编号" class="import-hint" data-testid="import-legacy-task">
          历史任务 {{ task.任务编号 }} 领用版本 {{ task.领用版本 }}：
          <span v-for="(source, field) in task.映射快照" :key="field">
            {{ field }}←{{ source.JSON路径 }}= {{ render(source.样本原值) }}
          </span>
          ，后续确认与更正不改写该任务取值
        </p>
      </section>
    </div>

    <div v-if="detail" class="import-detail" data-testid="import-detail">
      <h4>{{ detail.导入.导入编号 }} · 预检明细</h4>
      <table class="data-table">
        <thead>
          <tr><th>字段</th><th>JSON路径</th><th>样本原值</th><th>验证状态</th><th>结果</th></tr>
        </thead>
        <tbody>
          <tr
            v-for="entry in detail.导入.条目"
            :key="`${entry.字段别名}-${entry.JSON路径}`"
            :data-testid="`import-entry-${entry.字段别名}-${entry.JSON路径}`"
          >
            <td>{{ entry.字段别名 }}</td>
            <td>{{ entry.JSON路径 }}</td>
            <td>{{ render(entry.样本原值) }}</td>
            <td>{{ entry.验证状态 }}</td>
            <td>{{ entry.结果 }}</td>
          </tr>
        </tbody>
      </table>

      <div v-for="conflict in detail.冲突" :key="conflict.id" class="import-conflict" :data-testid="`conflict-${conflict.id}`">
        <header>
          <strong>{{ conflict.冲突编号 }} · {{ conflict.字段 }}</strong>
          <span>{{ conflict.状态 }}</span>
        </header>
        <p>候选路径（全部保留）：</p>
        <ul>
          <li v-for="item in conflict.候选" :key="item.JSON路径">
            {{ item.JSON路径 }} = {{ render(item.样本原值) }}（{{ item.数据类型 }}）
          </li>
        </ul>
        <p v-if="conflict.原生效定义">
          原生效定义：{{ conflict.原生效定义.JSON路径 }} = {{ render(conflict.原生效定义.样本原值) }}
        </p>
        <div v-if="conflict.状态 === '待更正'" class="page-actions">
          <button
            v-for="item in conflict.候选"
            :key="`resolve-${item.JSON路径}`"
            class="btn"
            type="button"
            :data-testid="`resolve-${conflict.id}-${item.JSON路径}`"
            @click="resolve(conflict.id, item.JSON路径)"
          >
            采用 {{ item.JSON路径 }}
          </button>
        </div>
        <p v-else-if="conflict.处理结论" data-testid="conflict-resolution">
          处理结论：{{ conflict.处理结论.结论 }}
        </p>
      </div>

      <p v-if="detail.中断" class="import-hint" data-testid="import-interrupt">
        {{ detail.中断.说明 }}
      </p>
      <p v-if="detail.恢复核对 && detail.恢复核对.length" class="import-hint" data-testid="import-recover">
        恢复核对：
        <span v-for="item in detail.恢复核对" :key="item.字段别名">
          {{ item.字段别名 }}={{ render(item.样本原值) }}（{{ item.验证状态 }}）
        </span>
      </p>
    </div>

    <div v-if="state" class="import-state">
      <h4>映射版本与缓存结果</h4>
      <table class="data-table">
        <thead>
          <tr><th>版本</th><th>状态</th><th>字段路径</th><th>原因</th></tr>
        </thead>
        <tbody>
          <tr v-for="version in state.映射版本" :key="version.版本" :data-testid="`version-row-${version.版本}`">
            <td>{{ version.版本 }}</td>
            <td>{{ version.状态 }}</td>
            <td>
              <span v-for="(source, field) in version.定义" :key="field">
                {{ field }}←{{ source.JSON路径 }}
              </span>
            </td>
            <td>{{ version.原因 }}</td>
          </tr>
        </tbody>
      </table>

      <h4>交付对比</h4>
      <table class="data-table">
        <thead>
          <tr><th>投递编号</th><th>映射版本</th><th>total</th><th>currency</th><th>待核对</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in state.交付" :key="item.id" :data-testid="`delivery-row-${item.id}`">
            <td>{{ item.投递编号 }}</td>
            <td>{{ item.映射版本 }}</td>
            <td>{{ describe(item.字段.total) }}</td>
            <td>{{ describe(item.字段.currency) }}</td>
            <td>{{ item.待核对字段.join('、') || '—' }}</td>
            <td>
              <button class="link" type="button" :data-testid="`delivery-trace-${item.id}`" @click="trace(item.id)">
                回查来源
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="traceResult" data-testid="delivery-trace">
        回查 {{ traceResult.投递.投递编号 }}：
        <span v-for="conflict in traceResult.冲突回查" :key="conflict.冲突编号">
          {{ conflict.冲突编号 }} 本次使用 {{ conflict.本次交付使用路径 }}，
          处理结论选择 {{ conflict.处理结论?.选择路径 ?? '—' }}，
          采用结论：{{ conflict.是否采用处理结论 ? '是' : '否' }}
        </span>
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  confirmImport,
  loadDelivery,
  loadImport,
  loadImportState,
  resolveConflict,
  runDelivery,
  uploadMapping,
} from '@/api/parse-import'
import type { DeliveryTrace, FieldSource, ImportDetail, ImportState } from '@/api/parse-import'

const SAMPLE_ENDPOINT = 'ENDP-CSV-0001'

const endpoint = ref(SAMPLE_ENDPOINT)
const filename = ref('mapping.json')
const content = ref('')
const busy = ref(false)
const message = ref('')
const detail = ref<ImportDetail | null>(null)
const state = ref<ImportState | null>(null)
const traceResult = ref<DeliveryTrace | null>(null)

const sample = ref<{ 端点: string; 样本: Record<string, unknown> } | null>(null)

function render(value: unknown): string {
  if (value === null || value === undefined) return '—'
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function describe(source?: FieldSource): string {
  if (!source) return '未交付'
  const pending = source.待核对 ? '（待核对）' : ''
  return `${render(source.值)} ← ${source.JSON路径}${pending}`
}

function fillSample() {
  endpoint.value = SAMPLE_ENDPOINT
  filename.value = 'mapping.json'
  content.value = JSON.stringify(
    {
      endpoint: SAMPLE_ENDPOINT,
      mappings: [
        { field: 'total', path: '$.gross', type: 'number' },
        { field: 'total', path: '$.net', type: 'number' },
        { field: 'currency', path: '$.currency', type: 'string' },
      ],
    },
    null,
    2,
  )
}

async function reload() {
  message.value = ''
  try {
    state.value = await loadImportState()
    const first = state.value.样本[0] ?? null
    sample.value = first ? { 端点: first.端点, 样本: first.样本 } : null
    if (detail.value) {
      detail.value = await loadImport(detail.value.导入.id)
    }
  } catch (error) {
    message.value = error instanceof Error ? error.message : '导入状态读取失败'
  }
}

async function upload() {
  busy.value = true
  message.value = ''
  try {
    detail.value = await uploadMapping({
      endpoint: endpoint.value.trim(),
      filename: filename.value.trim(),
      content: content.value,
    })
    await reload()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '映射文件预检失败'
  } finally {
    busy.value = false
  }
}

async function open(importId: number) {
  message.value = ''
  try {
    detail.value = await loadImport(importId)
  } catch (error) {
    message.value = error instanceof Error ? error.message : '预检明细读取失败'
  }
}

async function confirm(importId: number) {
  message.value = ''
  try {
    detail.value = await confirmImport(importId)
    await reload()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '确认失败'
  }
}

async function resolve(conflictId: number, path: string) {
  if (!detail.value) return
  message.value = ''
  try {
    detail.value = await resolveConflict(detail.value.导入.id, conflictId, path)
    await reload()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '冲突更正失败'
  }
}

async function deliver() {
  message.value = ''
  try {
    traceResult.value = await runDelivery()
    await reload()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '投递失败'
  }
}

async function trace(runId: number) {
  message.value = ''
  try {
    traceResult.value = await loadDelivery(runId)
  } catch (error) {
    message.value = error instanceof Error ? error.message : '交付回查失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.import-panel { margin-top: 28px; padding-top: 16px; border-top: 2px solid var(--border); }
.import-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.import-head h3 { margin: 0; }
.import-message { color: var(--brand); font-size: 13px; }
.import-form { display: grid; gap: 10px; margin: 12px 0; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.import-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.import-form input, .import-form textarea { border: 1px solid var(--border); border-radius: 6px; padding: 8px; font-family: inherit; font-size: 13px; }
.import-content textarea { font-family: monospace; }
.import-sample { font-size: 12px; color: var(--muted); margin-bottom: 10px; }
.import-grid { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 16px; }
.import-label { margin-right: 8px; }
.import-detail, .import-state { margin-top: 16px; }
.import-conflict { border: 1px solid #f0c36d; background: #fffbea; border-radius: 8px; padding: 10px 12px; margin: 10px 0; }
.import-conflict header { display: flex; justify-content: space-between; }
.import-conflict ul { margin: 6px 0; padding-left: 18px; font-size: 13px; }
.import-hint { font-size: 13px; color: var(--muted); }
.import-state h4 { margin: 16px 0 6px; }
</style>
