<template>
  <div style="padding:16px">
    <h1>借还记录</h1>
    <div class="muted" style="margin-bottom:8px">
      板内日期 {{ data.today }} ·
      跨日自动续借 <strong>{{ data.auto_renew ? '开' : '关' }}</strong>
    </div>
    <h3>逾期</h3>
    <p v-if="!data.overdue.length" class="muted">无</p>
    <div v-for="l in data.overdue" :key="'o'+l.id" class="item overdue">
      <strong>{{ l.title }}</strong> · {{ l.borrower }}
      <div class="muted">应还 {{ l.due_date }} · 逾期</div>
    </div>
    <h3>在借</h3>
    <p v-if="!data.active.length" class="muted">无</p>
    <div v-for="l in data.active" :key="'a'+l.id" class="item">
      <strong>{{ l.title }}</strong> · {{ l.borrower }}
      <div class="muted">
        应还 {{ l.due_date }}
        <span v-if="l.renewed_count" class="renewed-badge">自动续借 ×{{ l.renewed_count }}</span>
      </div>
    </div>
    <h3>已还</h3>
    <p v-if="!data.returned.length" class="muted">无</p>
    <div v-for="l in data.returned" :key="'r'+l.id" class="item returned">
      <strong>{{ l.title }}</strong> · {{ l.borrower }}
      <div class="muted">
        应还 {{ l.due_date }}
        <span v-if="l.renewed_count" class="renewed-badge">自动续借 ×{{ l.renewed_count }}</span>
      </div>
    </div>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const data = ref({ active: [], overdue: [], returned: [], today: '', auto_renew: false })
onMounted(async () => { data.value = await api('/loans') })
</script>
