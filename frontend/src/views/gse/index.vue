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
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type StatsKey = '在册车辆' | '待保养车辆' | '保养中车辆'

const ENDPOINT = '/api/gse'
const columns = ["车辆编号", "车辆类别", "适用作业", "停放区域", "上次保养日", "下次保养日", "责任人", "车辆状态"]
const actions = ["安排保养", "确认可用", "报废车辆"]
const statuses = ["待保养", "可用", "保养中", "已报废"]
const stats = ref<{ label: StatsKey; value: number }[]>([
  { label: '在册车辆', value: 0 },
  { label: '待保养车辆', value: 0 },
  { label: '保养中车辆', value: 0 },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

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

// 安排保养必须带上次保养日；下次保养日留空时由后端按车辆类别周期推算。
function promptMaintenanceDates(): Record<string, string> | null {
  const today = new Date().toISOString().slice(0, 10)
  const lastDate = window.prompt('请输入上次保养日（YYYY-MM-DD）', today)
  if (lastDate === null) return null
  if (!/^\d{4}-\d{2}-\d{2}$/.test(lastDate.trim())) {
    errorMessage.value = '上次保养日格式无效，应为 YYYY-MM-DD，保养未提交'
    return null
  }
  const nextDate = window.prompt('请输入下次保养日（YYYY-MM-DD，留空按车辆类别保养周期推算）', '')
  if (nextDate !== null && nextDate.trim() && !/^\d{4}-\d{2}-\d{2}$/.test(nextDate.trim())) {
    errorMessage.value = '下次保养日格式无效，应为 YYYY-MM-DD，保养未提交'
    return null
  }
  return {
    上次保养日: lastDate.trim(),
    ...(nextDate && nextDate.trim() ? { 下次保养日: nextDate.trim() } : {}),
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  const extra: Record<string, string> = {}
  if (action === '安排保养') {
    const dates = promptMaintenanceDates()
    if (dates === null) return
    Object.assign(extra, dates)
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, values: extra }),
    })
    const payload = await response.json().catch(() => null) as { ok?: boolean; message?: string } | null
    if (!response.ok || !payload || payload.ok === false) {
      throw new Error(payload?.message || '保障车辆动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保障车辆操作失败'
  }
}

async function reloadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) return
    const payload = (await response.json()) as Record<StatsKey, number>
    stats.value.forEach((item) => {
      item.value = Number(payload[item.label] ?? 0)
    })
  } catch {
    // 统计卡片加载失败不阻断列表，下次 reload 时会再试
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('保障车辆列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    await reloadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保障车辆列表读取失败'
  }
}

onMounted(reload)
</script>
