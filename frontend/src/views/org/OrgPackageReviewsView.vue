<template>
  <div class="workspace-page">
    <section class="page-intro"><div><p>套餐变更留痕</p><h2>信息审核</h2><span>套餐新增、修改、下架和恢复提交后不会立即生效；管理员审批通过后自动应用。</span></div></section>
    <el-card shadow="never">
      <el-table :data="items" v-loading="loading" empty-text="暂无审核记录">
        <el-table-column prop="package_name" label="套餐" min-width="170"/>
        <el-table-column label="操作" width="110"><template #default="s">{{ actionLabel(s.row.action) }}</template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="s"><el-tag :type="statusType(s.row.status)">{{ statusLabel(s.row.status) }}</el-tag></template></el-table-column>
        <el-table-column prop="requested_at" label="提交时间" min-width="180"/>
        <el-table-column prop="review_note" label="审核备注" min-width="180"/>
        <el-table-column label="操作" width="100"><template #default="s"><el-button v-if="s.row.status==='pending'" link type="danger" @click="withdraw(s.row)">撤回</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-if="pagination.total>pagination.page_size" v-model:current-page="pagination.page" :page-size="pagination.page_size" :total="pagination.total" layout="total, prev, pager, next" style="margin-top:16px;justify-content:flex-end" @current-change="load"/>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { fetchOrgPackageChangeRequests, withdrawOrgPackageChangeRequest } from "../../api/org";
const items=ref([]),loading=ref(false);
const pagination=reactive({page:1,page_size:15,total:0,pages:0});
const actionLabel=(v)=>({create:"新增",update:"修改",deactivate:"下架",reactivate:"恢复"}[v]||v);
const statusLabel=(v)=>({pending:"待审核",approved:"已通过",rejected:"已驳回",withdrawn:"已撤回"}[v]||v);
const statusType=(v)=>({approved:"success",rejected:"danger",withdrawn:"info",pending:"warning"}[v]);
async function load(){loading.value=true;try{const{data}=await fetchOrgPackageChangeRequests({page:pagination.page,page_size:15});items.value=data.items||[];Object.assign(pagination,data.pagination||{});}finally{loading.value=false;}}
async function withdraw(item){try{await ElMessageBox.confirm("撤回后可修改并重新提交，审核历史仍会保留。","撤回申请",{type:"warning"});await withdrawOrgPackageChangeRequest(item.id);ElMessage.success("申请已撤回");await load();}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"撤回失败");}}
onMounted(load);
</script>
