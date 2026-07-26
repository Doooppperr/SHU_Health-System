<template>
  <div class="workspace-page">
    <section class="page-intro"><div><p>机构成员入驻</p><h2>生成下一枚邀请码</h2><span>每次只保留一个可用邀请码；生成新码时旧的未使用码自动失效。</span></div></section>
    <el-alert title="邀请码明文仅在生成响应中展示一次，数据库只保存哈希。" type="warning" show-icon :closable="false"/>
    <el-card shadow="never">
      <el-table :data="rows" v-loading="loading">
        <el-table-column label="机构"><template #default="s"><strong>{{s.row.name}}</strong><br/><small>{{s.row.branch_name}}</small></template></el-table-column>
        <el-table-column label="机构账号"><template #default="s">{{s.row.administrator_count||0}} 个</template></el-table-column>
        <el-table-column label="当前邀请码"><template #default="s"><el-tag>{{statusLabel(s.row.invite?.status)}}</el-tag></template></el-table-column>
        <el-table-column width="190"><template #default="s"><el-button type="primary" link :disabled="!s.row.is_active" @click="issue(s.row)">生成下一枚邀请码</el-button></template></el-table-column>
      </el-table>
      <el-pagination v-if="pagination.total>pagination.page_size" v-model:current-page="pagination.page" :page-size="pagination.page_size" :total="pagination.total" layout="total, prev, pager, next" style="margin-top:16px;justify-content:flex-end" @current-change="load"/>
    </el-card>
    <el-dialog v-model="visible" title="邀请码已生成" width="520px" :close-on-click-modal="false">
      <el-alert title="关闭后无法再次查看，请立即安全保存。" type="warning" show-icon :closable="false"/>
      <div class="invite-code-box"><small>{{institutionName}}</small><strong>{{code}}</strong><el-button type="primary" @click="copy">复制邀请码</el-button></div>
      <template #footer><el-button type="primary" @click="visible=false">我已保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { fetchAdminInstitutions, issueInstitutionInvite } from "../../api/admin";

const rows=ref([]),loading=ref(false),visible=ref(false),code=ref(""),institutionName=ref("");
const pagination=reactive({page:1,page_size:15,total:0,pages:0});
const statusLabel=s=>({active:"可使用",used:"已使用",superseded:"已失效"}[s]||"未生成");
async function load(){loading.value=true;try{const{data}=await fetchAdminInstitutions({page:pagination.page,page_size:15});rows.value=data.items||[];Object.assign(pagination,data.pagination||{});}finally{loading.value=false;}}
async function issue(row){try{if(row.invite?.status==="active")await ElMessageBox.confirm("新邀请码会使旧码立即失效，确认生成？","生成下一枚邀请码",{type:"warning"});const{data}=await issueInstitutionInvite(row.id);code.value=data.invite_code;institutionName.value=`${row.name} · ${row.branch_name}`;visible.value=true;await load();}catch(e){if(e!=="cancel"&&e!=="close")ElMessage.error(e?.response?.data?.message||"生成失败");}}
async function copy(){try{await navigator.clipboard.writeText(code.value);ElMessage.success("已复制");}catch{ElMessage.warning("请手动复制");}}
onMounted(load);
</script>
