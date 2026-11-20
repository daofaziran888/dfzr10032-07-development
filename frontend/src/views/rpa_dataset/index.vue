<template>
  <section class="page" data-module="rpa_dataset">
    <header class="page-head">
      <div>
        <h2>RPA采集结果数据集</h2>
        <p class="page-desc">
          按任务定义从可重复读取页面提取字段，保留动作位置与执行批次；必填定位失败可追动作且不按空值交付，
          同一页面重试仅一条已确认记录进入接入通道，每个批次按其字段定义版本完整回查。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate(tab)">登记{{ tabLabels[tab] }}</button>
        <button class="btn" type="button" @click="exportRows">导出当前清单</button>
      </div>
    </header>

    <div class="tab-bar">
      <button
        v-for="item in tabs"
        :key="item.key"
        class="tab-btn"
        :class="{ active: tab === item.key }"
        type="button"
        @click="switchTab(item.key)"
      >
        {{ item.label }}
      </button>
    </div>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <!-- 字段定义版本 -->
    <table v-if="tab === 'def'" class="data-table">
      <thead>
        <tr>
          <th v-for="column in defColumns" :key="column">{{ column }}</th>
          <th>字段口径</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in defColumns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <button class="link" type="button" @click="openDetail(row)">查看字段（{{ row.字段定义?.length ?? 0 }}）</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="defColumns.length + 1" class="empty-state">暂无结果字段定义，可先登记字段定义版本</td>
        </tr>
      </tbody>
    </table>

    <!-- 采集结果记录 -->
    <table v-else-if="tab === 'result'" class="data-table">
      <thead>
        <tr>
          <th v-for="column in resultColumns" :key="column">{{ column }}</th>
          <th>采集值/失败</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-abnormal': row.abnormal }">
          <td v-for="column in resultColumns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <button class="link" type="button" @click="openDetail(row)">
              {{ row.abnormal ? `定位失败 ${row.失败明细?.length ?? 0} 项` : `字段 ${row.字段采集值?.length ?? 0} 项` }}
            </button>
          </td>
          <td class="row-actions">
            <button v-if="row.状态 === '提取中'" class="link" type="button" @click="openExtract(row)">提交字段提取</button>
            <button v-if="row.状态 === '已确认'" class="link" type="button" @click="runAction('确认结果', row)">确认结果</button>
            <button v-if="row.状态 === '已确认'" class="link" type="button" @click="openSubmit(row)">提交接入通道</button>
            <button v-if="row.状态 === '定位失败'" class="link" type="button" @click="runAction('重试提取', row)">重试提取</button>
            <button class="link" type="button" @click="openTrace(row)">批次回查</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="resultColumns.length + 2" class="empty-state">暂无采集结果记录，可先登记采集结果</td>
        </tr>
      </tbody>
    </table>

    <!-- 接入记录 -->
    <table v-else class="data-table">
      <thead>
        <tr>
          <th v-for="column in ingestColumns" :key="column">{{ column }}</th>
          <th>页面关联与快照</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in ingestColumns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>
            <button class="link" type="button" @click="openDetail(row)">页面 {{ row.页面标识 }} · 字段 {{ row.字段采集值?.length ?? 0 }} 项</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="ingestColumns.length + 1" class="empty-state">暂无接入记录，先在采集结果页按批次提交接入通道</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ tabLabels[tab] }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 登记弹窗 -->
    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记{{ tabLabels[tab] }}</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <template v-if="tab === 'def'">
              <label class="modal-field"><span>任务编号（须存在于RPA任务编排）</span>
                <input v-model="createFields.任务编号" required />
              </label>
              <label class="modal-field"><span>字段定义（每行：字段名,动作位置,定位器,是否必填）</span>
                <textarea v-model="defText" rows="6" placeholder="订单编号,动作1:读取订单表头,#order-no,是&#10;备注,动作4:展开备注栏,.remark-panel,否"></textarea>
              </label>
            </template>
            <template v-else-if="tab === 'result'">
              <label v-for="field in resultCreateFields" :key="field" class="modal-field">
                <span>{{ field }}</span>
                <input v-model="createFields[field]" :required="field !== '动作位置'" />
              </label>
              <p class="modal-tip">保存时自动冻结任务当前生效的字段定义版本与完整快照。</p>
            </template>
            <template v-else>
              <p class="modal-tip">接入记录由采集结果批次提交后自动生成，不支持手工登记。</p>
            </template>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button v-if="tab !== 'ingest'" class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- 字段提取弹窗 -->
    <Teleport to="body">
      <div v-if="extractTarget" class="modal-overlay" @click.self="extractTarget = null">
        <div class="modal-card">
          <header class="modal-head">
            <h3>提交字段提取 · {{ extractTarget.记录编号 }}</h3>
            <button class="btn ghost" type="button" @click="extractTarget = null">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitExtract">
            <p class="modal-tip">留空的字段视为定位失败；必填字段定位失败将带动作位置阻断交付，不按空值入库。</p>
            <label v-for="item in extractTarget.字段定义快照" :key="item.字段名" class="modal-field">
              <span>{{ item.字段名 }}{{ item.是否必填 ? '（必填）' : '' }} · {{ item.动作位置 || '动作位置未标注' }}</span>
              <input v-model="extractValues[item.字段名]" :placeholder="`定位器：${item.定位器}`" />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="extractTarget = null">取消</button>
              <button class="btn primary" type="submit">提交提取</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- 批次提交弹窗 -->
    <Teleport to="body">
      <div v-if="submitTarget" class="modal-overlay" @click.self="submitTarget = null">
        <div class="modal-card">
          <header class="modal-head">
            <h3>提交接入通道 · {{ submitTarget.执行批次 }}</h3>
            <button class="btn ghost" type="button" @click="submitTarget = null">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitBatch">
            <p class="modal-tip">按任务编号 + 执行批次整批提交；仍有提取中/定位失败记录时会被拦截，仅已确认记录生成与原页面关联的接入记录。</p>
            <label class="modal-field"><span>任务编号</span><input :value="submitTarget.任务编号" disabled /></label>
            <label class="modal-field"><span>执行批次</span><input :value="submitTarget.执行批次" disabled /></label>
            <label class="modal-field"><span>接入通道ID（数据接入通道模块的记录 id，如 3 = INGE-0003）</span>
              <input v-model="channelId" required />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="submitTarget = null">取消</button>
              <button class="btn primary" type="submit">提交批次</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- 明细/回查弹窗 -->
    <Teleport to="body">
      <div v-if="detail" class="modal-overlay" @click.self="detail = null">
        <div class="modal-card modal-wide">
          <header class="modal-head">
            <h3>{{ detailTitle }}</h3>
            <button class="btn ghost" type="button" @click="detail = null">&times;</button>
          </header>
          <div class="modal-body">
            <table class="data-table">
              <thead>
                <tr><th>字段名</th><th>动作位置</th><th>定位器</th><th>必填</th><th>采集值/提取状态</th></tr>
              </thead>
              <tbody>
                <tr v-for="(item, idx) in detailRows" :key="idx">
                  <td>{{ item.字段名 }}</td>
                  <td>{{ item.动作位置 || '—' }}</td>
                  <td>{{ item.定位器 || '—' }}</td>
                  <td>{{ item.是否必填 ? '是' : '否' }}</td>
                  <td :class="{ 'error-text': item.提取状态 === '定位失败' }">
                    {{ item.采集值 ?? '—' }}（{{ item.提取状态 }}）
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="detail.失败明细?.length" class="fail-box">
              <strong>必填/字段定位失败明细（可追具体动作）：</strong>
              <ul>
                <li v-for="(f, idx) in detail.失败明细" :key="idx">
                  {{ f.字段名 }} · 动作位置：{{ f.动作位置 || '未标注' }} · 定位器：{{ f.定位器 }} · {{ f.失败原因 }}
                </li>
              </ul>
            </div>
            <div v-if="traceData" class="trace-box">
              <strong>批次回查汇总：</strong>
              <p v-for="(v, k) in traceData.汇总" :key="k">{{ k }}：{{ v }}</p>
              <p v-if="traceData.字段定义版本?.length">
                采用字段定义：<span v-for="d in traceData.字段定义版本" :key="d.定义编号">
                  {{ d.定义编号 }}（V{{ d.定义版本 }}，{{ d.状态 }}）
                </span>
              </p>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/rpa_dataset'
const tabs = [
  { key: 'def', label: '字段定义版本' },
  { key: 'result', label: '采集结果' },
  { key: 'ingest', label: '接入记录' },
] as const
type TabKey = (typeof tabs)[number]['key']
const tabLabels: Record<TabKey, string> = { def: '字段定义版本', result: '采集结果', ingest: '接入记录' }

const tab = ref<TabKey>('result')
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})
const defText = ref('')
const extractTarget = ref<Row | null>(null)
const extractValues = ref<Record<string, string>>({})
const submitTarget = ref<Row | null>(null)
const channelId = ref('')
const detail = ref<Row | null>(null)
const traceData = ref<Row | null>(null)

const defColumns = ['定义编号', '任务编号', '定义版本', '状态', '创建时间']
const resultColumns = ['记录编号', '任务编号', '执行批次', '页面标识', '页面地址', '动作位置', '定义编号', '定义版本', '状态']
const ingestColumns = ['接入记录编号', '通道编号', '任务编号', '执行批次', '采集记录编号', '页面标识', '定义版本', '状态']
const resultCreateFields = ['任务编号', '执行批次', '页面标识', '页面地址', '动作位置']

const filterFields = computed(() => {
  if (tab.value === 'def') return ['任务编号', '状态']
  if (tab.value === 'ingest') return ['任务编号', '执行批次']
  return ['任务编号', '执行批次', '状态']
})

const stats = computed(() => {
  const data = rows.value
  if (tab.value === 'def') {
    return [
      { label: '定义版本数', value: data.length },
      { label: '生效中', value: data.filter((r) => r.状态 === '生效中').length },
      { label: '已归档', value: data.filter((r) => r.状态 === '已归档').length },
    ]
  }
  if (tab.value === 'ingest') {
    return [
      { label: '接入记录数', value: data.length },
      { label: '关联页面数', value: new Set(data.map((r) => r.页面标识)).size },
      { label: '涉及批次数', value: new Set(data.map((r) => r.执行批次)).size },
    ]
  }
  return [
    { label: '已确认', value: data.filter((r) => r.状态 === '已确认').length },
    { label: '定位失败', value: data.filter((r) => r.状态 === '定位失败').length },
    { label: '提取中', value: data.filter((r) => r.状态 === '提取中').length },
    { label: '已接入/已顶替', value: data.filter((r) => ['已接入', '已顶替'].includes(r.状态)).length },
  ]
})

const detailTitle = computed(() => {
  if (traceData.value) return `批次完整回查 · ${detail.value?.记录编号 ?? ''}`
  return detail.value?.定义编号 ? `字段定义明细 · ${detail.value.定义编号}` : `采集/接入明细 · ${detail.value?.记录编号 ?? detail.value?.接入记录编号 ?? ''}`
})
const detailRows = computed<Row[]>(() => detail.value?.字段采集值 ?? detail.value?.字段定义 ?? [])

function endpointFor(tabKey: TabKey): string {
  if (tabKey === 'def') return `${ENDPOINT}/field-definitions`
  if (tabKey === 'ingest') return `${ENDPOINT}/ingestion-records`
  return ENDPOINT
}

function switchTab(key: TabKey) {
  tab.value = key
  filters.value = {}
  void reload()
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${endpointFor(tab.value)}/export`, '_blank')
}

function openCreate(tabKey: TabKey) {
  if (tabKey === 'ingest') {
    errorMessage.value = '接入记录由批次提交自动生成，不支持手工登记'
    return
  }
  errorMessage.value = ''
  createFields.value = {}
  defText.value = ''
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    let body: Record<string, any> = { ...createFields.value }
    if (tab.value === 'def') {
      const 字段定义 = defText.value.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
        const [字段名, 动作位置 = '', 定位器 = '', required = '是'] = line.split(',').map((s) => s.trim())
        return { 字段名, 动作位置, 定位器, 是否必填: required !== '否' }
      })
      body = { 任务编号: createFields.value.任务编号, 字段定义 }
    }
    const response = await request(endpointFor(tab.value), {
      method: 'POST',
      body: JSON.stringify({ values: body }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) throw new Error(data.message || '登记失败')
    showCreateModal.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

function openExtract(row: Row) {
  extractTarget.value = row
  extractValues.value = Object.fromEntries((row.字段定义快照 ?? []).map((i: Row) => [i.字段名, '']))
}

async function submitExtract() {
  if (!extractTarget.value) return
  errorMessage.value = ''
  try {
    const values: Record<string, string | null> = {}
    for (const [k, v] of Object.entries(extractValues.value)) {
      values[k] = v.trim() === '' ? null : v
    }
    const response = await request(`${ENDPOINT}/${extractTarget.value.id}/extract`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const data = await response.json()
    if (!data.ok) {
      // 必填定位失败属于业务可读结果：刷新列表展示失败态，同时把原因提示在页脚。
      errorMessage.value = data.message
      extractTarget.value = null
      await reload()
      return
    }
    extractTarget.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段提取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) throw new Error(data.message || '动作未生效')
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

function openTrace(row: Row) {
  void loadTrace(row.任务编号, row.执行批次, row)
}

async function loadTrace(taskCode: string, batchNo: string, row?: Row) {
  errorMessage.value = ''
  try {
    const query = new URLSearchParams({ task_code: taskCode, batch_no: batchNo })
    const response = await request(`${ENDPOINT}/trace?${query.toString()}`)
    if (!response.ok) throw new Error('批次回查失败')
    traceData.value = await response.json()
    detail.value = row ?? { 记录编号: `${taskCode}/${batchNo}` }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次回查失败'
  }
}

function openDetail(row: Row) {
  traceData.value = null
  detail.value = row
}

async function submitBatch() {
  if (!submitTarget.value) return
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/submit`, {
      method: 'POST',
      body: JSON.stringify({
        values: {
          任务编号: submitTarget.value.任务编号,
          执行批次: submitTarget.value.执行批次,
          通道ID: channelId.value,
        },
      }),
    })
    const data = await response.json()
    if (!response.ok || !data.ok) throw new Error(data.message || '批次提交失败')
    submitTarget.value = null
    await reload()
    errorMessage.value = data.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次提交失败'
  }
}

// 采集结果页提供批次提交入口
function openSubmit(row: Row) {
  submitTarget.value = row
  channelId.value = ''
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(
    Object.fromEntries(Object.entries(filters.value).filter(([, v]) => v?.trim())),
  ).toString()
  try {
    const response = await request(`${endpointFor(tab.value)}?${query}`)
    if (!response.ok) throw new Error('列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.tab-bar { display: flex; gap: 8px; margin-bottom: 12px; }
.tab-btn { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 6px 16px; cursor: pointer; font-size: 13px; }
.tab-btn.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.row-abnormal { background: #fef3f2; }
.modal-wide { width: 760px; }
.modal-tip { font-size: 12px; color: var(--muted); margin: 0; }
.modal-field textarea { padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; font-family: inherit; }
.fail-box { border: 1px solid #fda29b; background: #fef3f2; border-radius: 8px; padding: 10px 12px; font-size: 13px; }
.fail-box ul { margin: 6px 0 0; padding-left: 18px; }
.trace-box { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; font-size: 13px; }
.trace-box p { margin: 4px 0; }
</style>
