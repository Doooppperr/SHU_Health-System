<template>
  <div class="workspace-page">
    <section class="page-intro"><div><p>公开评价</p><h2>用户评价</h2><span>仅显示当前分院已通过审核并公开的评价；机构回复也需要管理员审核。</span></div></section>
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
    <el-card shadow="never" v-loading="loading">
      <el-empty v-if="!loading && !items.length" description="当前分院暂时没有公开评价" />
      <div v-else class="comment-list">
        <article v-for="item in items" :key="item.id" class="comment-card">
          <div class="comment-card__head"><div><strong>{{ item.user?.username || "用户" }}</strong><el-rate :model-value="item.rating" disabled /></div><span>{{ formatDate(item.created_at) }}</span></div>
          <p>{{ item.content }}</p>
          <div v-if="item.reply" class="comment-reply">
            <div><strong>机构回复</strong><el-tag :type="statusType(item.reply.status)">{{ item.reply.status_label }}</el-tag></div>
            <p>{{ item.reply.content }}</p>
            <el-alert v-if="item.reply.status==='rejected'" :title="`驳回原因：${item.reply.review_note || '请修改后重新提交'}`" type="warning" show-icon :closable="false" />
          </div>
          <el-button v-if="!item.reply || item.reply.status==='rejected'" type="primary" plain @click="openReply(item)">{{ item.reply ? "修改并重新提交" : "回复评价" }}</el-button>
        </article>
      </div>
    </el-card>
    <el-dialog v-model="dialogVisible" title="回复用户评价" width="min(560px, 92vw)">
      <el-input v-model="replyContent" type="textarea" :rows="5" maxlength="1000" show-word-limit placeholder="请输入完整、友善的中文回复" />
      <template #footer><el-button @click="dialogVisible=false">取消</el-button><el-button type="primary" :loading="submitting" @click="submitReply">提交审核</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { fetchOrganizationComments, submitOrganizationReply } from "../../api/comments";
const loading=ref(false),submitting=ref(false),items=ref([]),errorMessage=ref(""),dialogVisible=ref(false),replyContent=ref(""),active=ref(null);
const statusType=(status)=>({approved:"success",pending:"warning",rejected:"danger"}[status]||"info");
const formatDate=(value)=>String(value||"").replace("T"," ").slice(0,16)||"—";
async function load(){loading.value=true;try{items.value=(await fetchOrganizationComments()).data.items||[];}catch(error){errorMessage.value=error?.response?.data?.message||"用户评价加载失败";}finally{loading.value=false;}}
function openReply(item){active.value=item;replyContent.value=item.reply?.status==="rejected"?item.reply.content:"";dialogVisible.value=true;}
async function submitReply(){if(!replyContent.value.trim())return ElMessage.warning("请填写回复内容");submitting.value=true;try{await submitOrganizationReply(active.value.id,replyContent.value.trim());ElMessage.success("回复已提交，等待管理员审核");dialogVisible.value=false;await load();}catch(error){ElMessage.error(error?.response?.data?.message||"回复提交失败");}finally{submitting.value=false;}}
onMounted(load);
</script>

<style scoped>
.comment-list{display:grid;gap:16px}.comment-card{padding:18px;border:1px solid var(--el-border-color-light);border-radius:14px}.comment-card__head,.comment-reply>div{display:flex;align-items:center;justify-content:space-between;gap:12px}.comment-card__head>div{display:flex;align-items:center;gap:12px}.comment-card__head span{color:var(--el-text-color-secondary)}.comment-reply{margin:14px 0;padding:14px;background:var(--el-fill-color-light);border-left:4px solid var(--el-color-primary);border-radius:8px}.comment-reply>div{justify-content:flex-start}
</style>
