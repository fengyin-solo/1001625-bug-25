<template>
  <section class="page" data-module="gse">
    <header class="page-head">
      <div>
        <h2>保障车辆管理</h2>
        <p class="page-desc">维护保障车辆，围绕车辆编号、车辆类别、适用作业、停放区域做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记保障车辆</button>
        <button class="btn" type="button" @click="exportRows">导出保障车辆清单</button>
      </div>
    </header>

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

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="!availableActions(row).includes(action)"
              :title="actionTip(row, action)"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无保障车辆数据，可先登记保障车辆</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条保障车辆记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="infoMessage" class="info-text">{{ infoMessage }}</span>
    </footer>

    <div v-if="scheduleTarget" class="modal-mask" @click.self="closeSchedule">
      <form class="modal-card" @submit.prevent="submitSchedule">
        <h3>安排保养 · {{ String(scheduleTarget['车辆编号'] ?? '') }}</h3>
        <p class="modal-hint">
          车辆类别「{{ String(scheduleTarget['车辆类别'] ?? '') }}」按
          {{ cycleDays(scheduleTarget) }} 天周期保养；下次保养日留空时按上次保养日自动推算。
        </p>
        <label class="modal-field">
          <span>上次保养日 <em>*</em></span>
          <input v-model="scheduleForm.last" type="date" required />
        </label>
        <label class="modal-field">
          <span>下次保养日（留空按周期推算）</span>
          <input v-model="scheduleForm.next" type="date" />
        </label>
        <p v-if="scheduleError" class="error-text">{{ scheduleError }}</p>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeSchedule">取消</button>
          <button class="btn primary" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '确认安排保养' }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type ActionResult = { ok: boolean; message: string; entry?: Row | null }

const ENDPOINT = '/api/gse'
const columns = ["车辆编号", "车辆类别", "适用作业", "停放区域", "上次保养日", "下次保养日", "责任人", "车辆状态"]
const actions = ["安排保养", "确认可用", "报废车辆"]
const CYCLE_KEYWORDS: Array<[string, number]> = [
  ['加油车', 45],
  ['除冰车', 30],
  ['牵引车', 90],
  ['摆渡车', 60],
  ['传送车', 60],
  ['行李车', 60],
  ['平台车', 90],
]
const DEFAULT_CYCLE_DAYS = 90

const rows = ref<Row[]>([])
const total = ref(0)
const fullRows = ref<Row[]>([])
const errorMessage = ref('')
const infoMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

// 统计数字与列表、详情同源（都取后端按保养日推导后的全量数据），保证数字一致。
const stats = computed(() => [
  { label: '在册车辆', value: fullRows.value.length },
  { label: '待保养车辆', value: fullRows.value.filter((row) => row['车辆状态'] === '待保养').length },
  { label: '保养中车辆', value: fullRows.value.filter((row) => row['车辆状态'] === '保养中').length },
])

const scheduleTarget = ref<Row | null>(null)
const scheduleForm = reactive({ last: '', next: '' })
const scheduleError = ref('')
const submitting = ref(false)

function cycleDays(row: Row): number {
  const category = String(row['车辆类别'] ?? '')
  const hit = CYCLE_KEYWORDS.find(([keyword]) => category.includes(keyword))
  return hit ? hit[1] : DEFAULT_CYCLE_DAYS
}

// 报废与保养互斥、保养中不能重复安排保养，不可用动作在前端先置灰。
function availableActions(row: Row): string[] {
  switch (row['车辆状态']) {
    case '待保养':
      return ['安排保养', '报废车辆']
    case '可用':
      return ['安排保养', '报废车辆']
    case '保养中':
      return ['确认可用']
    case '已报废':
      return []
    default:
      return actions
  }
}

function actionTip(row: Row, action: string): string {
  if (availableActions(row).includes(action)) {
    return ''
  }
  if (row['车辆状态'] === '已报废') {
    return '车辆已报废，与保养互斥，不能再操作'
  }
  if (row['车辆状态'] === '保养中' && action !== '确认可用') {
    return '车辆保养中，请先确认可用；重复安排保养不会重复生成记录'
  }
  if (action === '确认可用') {
    return '需先安排保养并完成后，才能确认可用'
  }
  return ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '保障车辆登记入口尚未接入审批流'
}

async function postAction(row: Row, body: Record<string, unknown>): Promise<ActionResult> {
  const response = await request(`${ENDPOINT}/${row.id}/actions`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return (await response.json()) as ActionResult
}

function openSchedule(row: Row) {
  scheduleTarget.value = row
  scheduleForm.last = String(row['上次保养日'] ?? '')
  scheduleForm.next = ''
  scheduleError.value = ''
}

function closeSchedule() {
  scheduleTarget.value = null
  scheduleError.value = ''
}

async function submitSchedule() {
  if (!scheduleTarget.value) {
    return
  }
  scheduleError.value = ''
  submitting.value = true
  try {
    const values: Record<string, string> = { action: '安排保养' }
    if (scheduleForm.last) {
      values['上次保养日'] = scheduleForm.last
    }
    if (scheduleForm.next) {
      values['下次保养日'] = scheduleForm.next
    }
    const result = await postAction(scheduleTarget.value, values)
    if (!result.ok) {
      scheduleError.value = result.message || '安排保养失败，请检查保养日期'
      return
    }
    infoMessage.value = result.message
    closeSchedule()
    await reload()
  } catch (error) {
    scheduleError.value = error instanceof Error ? error.message : '安排保养失败'
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  infoMessage.value = ''
  if (action === '安排保养') {
    openSchedule(row)
    return
  }
  try {
    const result = await postAction(row, { action })
    if (!result.ok) {
      errorMessage.value = result.message
      await reload()
      return
    }
    infoMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保障车辆操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const [listResponse, exportResponse] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${ENDPOINT}/export`),
    ])
    if (!listResponse.ok) {
      throw new Error('保障车辆列表读取失败')
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (exportResponse.ok) {
      const exported = await exportResponse.json()
      fullRows.value = Array.isArray(exported.items) ? exported.items : []
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保障车辆列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.info-text { color: #067647; }
.link:disabled { color: #9aa6b2; cursor: not-allowed; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal-card {
  width: 420px;
  background: #fff;
  border-radius: 8px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.modal-card h3 { margin: 0; font-size: 16px; }
.modal-hint { margin: 0; font-size: 12px; color: var(--muted); }
.modal-field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.modal-field em { color: #b42318; font-style: normal; }
.modal-field input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
