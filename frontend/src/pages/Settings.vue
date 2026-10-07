<template>
  <div style="padding:16px;max-width:520px">
    <h1>设置</h1>

    <div class="item setting-row">
      <div>
        <strong>邻里板名称</strong>
        <div class="muted">显示在看板顶部</div>
      </div>
      <input v-model="boardName" placeholder="邻里板名称" />
    </div>

    <div class="item setting-row">
      <div>
        <strong>跨日自动续借</strong>
        <div class="muted">
          打开后，应还日当天结束仍未归还的物品自动把应还日顺延一天并保持在借；
          关闭则到期即按逾期现算，不另加一天。
        </div>
      </div>
      <label class="switch">
        <input type="checkbox" v-model="autoRenew" />
        <span>{{ autoRenew ? '开' : '关' }}</span>
      </label>
    </div>

    <button @click="save" :disabled="saving">{{ saving ? '提交中…' : '保存设置' }}</button>
    <span v-if="msg" class="save-msg" :class="{ ok: !error }">{{ msg }}</span>
  </div>
</template>
<script setup>
import { ref, inject, onMounted } from 'vue'
import { api } from '../api'
const boardName = ref('')
const autoRenew = ref(false)
const saving = ref(false)
const msg = ref('')
const error = ref(false)
const reloadBoard = inject('reloadBoard')

onMounted(async () => {
  const s = await api('/settings')
  boardName.value = s.board_name || ''
  autoRenew.value = s.auto_renew === 'on'
})

async function save() {
  saving.value = true; msg.value = ''; error.value = false
  try {
    // 开关提交与续借注入是后端同一事务：返回 applied 即本次立刻续上的笔数。
    const r = await api('/settings', {
      method: 'POST',
      body: JSON.stringify({ board_name: boardName.value, auto_renew: autoRenew.value }),
    })
    msg.value = r.applied > 0 ? `已保存，立刻续借 ${r.applied} 笔（截至 ${r.today}）` : `已保存（${r.today}）`
    await reloadBoard()  // 顶细条逾期/在借数与新规则同步，不经过“先逾期再改回”
  } catch (e) {
    error.value = true
    msg.value = '保存失败，已回滚：' + e.message
  } finally {
    saving.value = false
  }
}
</script>
