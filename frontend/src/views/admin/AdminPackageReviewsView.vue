<template>
  <div class="workspace-page">
    <section class="page-intro"><div><p>机构套餐治理</p><h2>审核记录</h2><span>查看完整套餐内容和变更前后对比后再作出审核决定。</span></div></section>
    <el-card shadow="never">
      <el-select v-model="status" @change="load"><el-option label="全部状态" value=""/><el-option label="待审核" value="pending"/><el-option label="已通过" value="approved"/><el-option label="已驳回" value="rejected"/><el-option label="已撤回" value="withdrawn"/></el-select>
      <el-table :data="items" v-loading="loading" empty-text="暂无审核记录">
        <el-table-column label="机构" min-width="190"><template #default="s">{{s.row.institution?.name}} · {{s.row.institution?.branch_name}}</template></el-table-column>
        <el-table-column prop="package_name" label="套餐" min-width="150"/><el-table-column label="变更类型" width="110"><template #default="s">{{s.row.action_label||actionLabel(s.row.action)}}</template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="s"><el-tag :type="statusType(s.row.status)">{{s.row.status_label||statusLabel(s.row.status)}}</el-tag></template></el-table-column>
        <el-table-column label="申请时间" min-width="160"><template #default="s">{{formatTime(s.row.requested_at)}}</template></el-table-column>
        <el-table-column label="操作" width="230"><template #default="s"><el-button link type="primary" @click="openDetail(s.row)">查看详情</el-button><template v-if="s.row.status==='pending'"><el-button link type="success" @click="approve(s.row)">通过</el-button><el-button link type="danger" @click="reject(s.row)">驳回</el-button></template><span v-else>{{s.row.review_note||''}}</span></template></el-table-column>
      </el-table>
    </el-card>
    <el-drawer v-model="detailVisible" title="套餐变更完整详情" size="min(920px,96vw)">
      <template v-if="current">
        <el-alert :title="`${current.institution?.name} · ${current.institution?.branch_name}｜${current.action_label}`" type="info" show-icon :closable="false" />
        <div class="package-compare">
          <section v-if="current.before_details"><h3>变更前</h3><PackageDetails :value="current.before_details" /></section>
          <section><h3>{{current.before_details?'拟变更为':'拟新增内容'}}</h3><PackageDetails :value="current.proposed_details||current.before_details" /></section>
        </div>
        <div v-if="current.status==='pending'" style="display:flex;justify-content:flex-end;gap:12px;margin-top:20px"><el-button type="danger" plain @click="reject(current)">驳回</el-button><el-button type="primary" @click="approve(current)">确认通过</el-button></div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { defineComponent, h, onMounted, ref } from "vue";
import { ElDescriptions, ElDescriptionsItem, ElMessage, ElMessageBox } from "element-plus";
import { approveAdminPackageChangeRequest, fetchAdminPackageChangeRequests, rejectAdminPackageChangeRequest } from "../../api/admin";

const fields=[["套餐名称","name"],["套餐类型","package_type_label"],["适用人群","audience"],["适用性别","gender_scope_label"],["价格","price"],["健康方向","health_domains"],["重点内容","focus_area"],["套餐介绍","description"],["检查前须知","booking_notice"],["状态","status_label"]];
const PackageDetails=defineComponent({props:{value:{type:Object,default:()=>({})}},setup(props){return()=>h(ElDescriptions,{column:1,border:true},()=>fields.map(([label,key])=>h(ElDescriptionsItem,{label},()=>{const value=props.value?.[key];if(key==="price")return `¥${Number(value||0).toFixed(2)}`;if(Array.isArray(value))return value.join("、")||"未配置";return value||"—";})));}});
const items=ref([]),loading=ref(false),status=ref(""),detailVisible=ref(false),current=ref(null);
const actionLabel=(v)=>({create:"新增套餐",update:"修改套餐",deactivate:"下架套餐",reactivate:"恢复套餐"}[v]||"套餐变更");
const statusLabel=(v)=>({pending:"待审核",approved:"已通过",rejected:"已驳回",withdrawn:"已撤回"}[v]||"处理中");
const statusType=(v)=>({approved:"success",rejected:"danger",withdrawn:"info",pending:"warning"}[v]);
const formatTime=(value)=>String(value||"").replace("T"," ").slice(0,16)||"—";
function openDetail(item){current.value=item;detailVisible.value=true;}
async function load(){loading.value=true;try{items.value=(await fetchAdminPackageChangeRequests(status.value?{status:status.value}:{})).data.items||[];}finally{loading.value=false;}}
async function approve(item){try{await ElMessageBox.confirm("已核对套餐完整详情，确认通过后将立即生效。","通过套餐变更",{type:"warning",confirmButtonText:"确认通过"});await approveAdminPackageChangeRequest(item.id);ElMessage.success("申请已通过并生效");detailVisible.value=false;await load();}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"审批失败");}}
async function reject(item){try{const{value}=await ElMessageBox.prompt("请填写明确的中文驳回原因，便于机构修改。","驳回套餐变更",{inputType:"textarea",confirmButtonText:"确认驳回",inputValidator:(text)=>Boolean(text?.trim())||"请填写驳回原因"});await rejectAdminPackageChangeRequest(item.id,value.trim());ElMessage.success("申请已驳回");detailVisible.value=false;await load();}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"驳回失败");}}
onMounted(load);
</script>

<style scoped>.package-compare{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin-top:18px}.package-compare h3{margin:0 0 10px}@media(max-width:760px){.package-compare{grid-template-columns:1fr}}</style>
