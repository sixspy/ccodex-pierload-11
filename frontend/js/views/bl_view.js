export const blViewTemplate = `
<section class="panel">
  <h2>提单册</h2>
  <p class="hint">到港提单须先登记，过磅票引用提单号；回执列与空桩/放行/对账/总览同态。</p>
  <div class="form-row">
    <div><label>提单号</label><input v-model="blForm.bl_code"/></div>
    <div><label>货描</label><input v-model="blForm.cargo_desc"/></div>
    <div><label>申报毛重(吨)</label><input v-model.number="blForm.declared_gross_t" type="number"/></div>
    <div><label>船名航次</label><input v-model="blForm.vessel_voyage"/></div>
  </div>
  <div class="actions">
    <button @click="registerBl">登记提单</button>
    <button class="secondary" @click="refresh">刷新</button>
  </div>
  <table class="data-table">
    <thead><tr><th>提单</th><th>货描</th><th>申报吨</th><th>航次</th><th>状态</th><th>海关回执</th></tr></thead>
    <tbody>
      <tr v-for="b in blList" :key="b.id">
        <td>{{ b.bl_code }}</td>
        <td>{{ b.cargo_desc }}</td>
        <td>{{ b.declared_gross_t }}</td>
        <td>{{ b.vessel_voyage }}</td>
        <td>{{ b.status }}</td>
        <td>
          <span class="badge" :class="b.customs_receipt_ready ? 'pass' : 'fail'">
            {{ b.customs_receipt_ready ? '回执已到' : '回执待回' }}
          </span>
        </td>
      </tr>
    </tbody>
  </table>
</section>
`;
