export const stubsViewTemplate = `
<section class="grid-2">
  <section class="panel">
    <h2>海关申报空桩</h2>
    <p class="hint">接口已挂载；回执未到只可“回执待回”，禁写正式完成态。回执到达在此登记后原子晋升。</p>
    <div class="form-row">
      <div><label>提单</label><input v-model="customsForm.bl_code"/></div>
      <div><label>行数</label><input v-model.number="customsForm.row_count" type="number"/></div>
    </div>
    <div class="actions"><button @click="enqueueCustoms">入队</button></div>
    <table class="data-table compact">
      <thead><tr><th>提单</th><th>空桩状态</th></tr></thead>
      <tbody>
        <tr v-for="j in customsJobs" :key="j.id">
          <td>{{ j.bl_code }}</td>
          <td><span class="badge" :class="j.status==='回执已到' ? 'pass' : 'fail'">{{ j.status }}</span></td>
        </tr>
      </tbody>
    </table>
  </section>
  <section class="panel">
    <h2>海关回执登记</h2>
    <p class="hint">回执到达登记口：正式完成态唯一承认的回执来源。</p>
    <div class="form-row">
      <div><label>提单号</label><input v-model="receiptForm.bl_code"/></div>
      <div><label>回执号</label><input v-model="receiptForm.receipt_ref"/></div>
    </div>
    <div class="form-row">
      <div><label>备注</label><input v-model="receiptForm.detail"/></div>
    </div>
    <div class="actions"><button @click="registerReceipt">登记回执</button></div>
    <table class="data-table compact">
      <thead><tr><th>提单</th><th>回执号</th><th>到达时间</th></tr></thead>
      <tbody>
        <tr v-for="r in customsReceipts" :key="r.id">
          <td>{{ r.bl_code }}</td>
          <td class="mono">{{ r.receipt_ref }}</td>
          <td>{{ r.received_at }}</td>
        </tr>
        <tr v-if="!customsReceipts.length"><td colspan="3" class="muted">暂无回执</td></tr>
      </tbody>
    </table>
  </section>
  <section class="panel">
    <h2>岸桥计划空桩</h2>
    <div class="form-row">
      <div><label>泊位</label><input v-model="craneForm.berth_slot"/></div>
      <div><label>票ID(可空)</label><input v-model.number="craneForm.ticket_id" type="number"/></div>
    </div>
    <div class="actions"><button @click="enqueueCrane">入队</button></div>
    <table class="data-table compact">
      <tr v-for="j in craneJobs" :key="j.id"><td>{{ j.berth_slot }}</td><td>{{ j.status }}</td></tr>
    </table>
  </section>
</section>
`;
