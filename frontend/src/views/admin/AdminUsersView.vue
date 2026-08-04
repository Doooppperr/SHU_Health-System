<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div>
        <p>非健康内容管理</p>
        <h2>账号管理</h2>
        <span>普通用户支持查看基础资料、停用、恢复、管理员改密和级联删除。</span>
      </div>
    </section>

    <el-card shadow="never">
      <div class="filter-row">
        <el-input v-model="filters.q" placeholder="搜索用户名、姓名、健康身份码、邮箱或手机号" clearable @keyup.enter="applyFilters"/>
        <el-select v-model="filters.role" @change="applyFilters">
          <el-option label="全部角色" value=""/><el-option label="普通用户" value="user"/>
          <el-option label="机构账号" value="institution_admin"/><el-option label="管理员" value="admin"/>
        </el-select>
        <el-select v-model="filters.active" @change="applyFilters">
          <el-option label="全部状态" value=""/><el-option label="启用" value="true"/><el-option label="停用" value="false"/>
        </el-select>
        <el-button type="primary" @click="applyFilters">查询</el-button>
      </div>

      <el-table :data="items" v-loading="loading">
        <el-table-column prop="username" label="账号" min-width="140"/>
        <el-table-column label="角色" width="120"><template #default="s"><el-tag>{{roleName(s.row.role)}}</el-tag></template></el-table-column>
        <el-table-column label="机构" min-width="160"><template #default="s">{{s.row.managed_institution?.name||"-"}}</template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="s"><el-tag :type="s.row.is_active?'success':'info'">{{s.row.is_active?'启用':'停用'}}</el-tag></template></el-table-column>
        <el-table-column label="操作" min-width="290" fixed="right">
          <template #default="s">
            <template v-if="s.row.role==='user'">
              <el-button link type="primary" @click="showDetail(s.row)">基础资料</el-button>
              <el-button link type="primary" @click="openPassword(s.row)">修改密码</el-button>
              <el-button link @click="toggle(s.row)">{{s.row.is_active?'停用':'恢复'}}</el-button>
              <el-button link type="danger" @click="remove(s.row)">级联删除</el-button>
            </template>
            <el-button v-else-if="s.row.role==='institution_admin'" link type="danger" @click="removeStaff(s.row)">删除机构账号</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="pagination.total>pagination.page_size"
        v-model:current-page="pagination.page"
        :page-size="pagination.page_size"
        :total="pagination.total"
        layout="total, prev, pager, next"
        @current-change="load"
      />
    </el-card>

    <el-drawer v-model="detailVisible" title="用户基础资料" size="min(480px,94vw)">
      <el-descriptions v-if="detail" :column="1" border>
        <el-descriptions-item label="真实姓名">{{detail.real_name||"未完善"}}</el-descriptions-item>
        <el-descriptions-item label="健康身份码">{{detail.health_id||"未生成"}}</el-descriptions-item>
        <el-descriptions-item label="用户名">{{detail.username}}</el-descriptions-item>
        <el-descriptions-item label="邮箱">{{detail.email||"未填写"}}</el-descriptions-item>
        <el-descriptions-item label="电话">{{detail.phone||"未填写"}}</el-descriptions-item>
        <el-descriptions-item label="生日">{{detail.birth_date||"未填写"}}</el-descriptions-item>
        <el-descriptions-item label="性别">{{genderName(detail.gender)}}</el-descriptions-item>
        <el-descriptions-item label="账号状态">{{detail.is_active?"启用":"停用"}}</el-descriptions-item>
        <el-descriptions-item label="注册时间">{{formatTime(detail.created_at)}}</el-descriptions-item>
        <el-descriptions-item label="最近改密邮件">{{mailStatusName(detail.password_notification?.status)}}</el-descriptions-item>
      </el-descriptions>
      <el-alert title="管理员只能修正姓名、性别和出生日期，不能查看或修改用户健康档案。" type="info" show-icon :closable="false" style="margin-top:16px" />
      <el-button v-if="detail" type="primary" plain style="margin-top:16px" @click="openIdentityCorrection">修正实名信息</el-button>
      <el-button v-if="detail?.password_notification?.status==='failed'" type="warning" style="margin-top:16px" @click="retryDetailMail">重试密码通知邮件</el-button>
    </el-drawer>

    <el-dialog v-model="identityVisible" title="修正用户实名信息" width="min(520px,94vw)">
      <el-alert title="保存后仅通过邮件通知用户；发送失败会保留在发件箱中按队列策略重试。管理员仍不能查看用户健康档案。" type="warning" show-icon :closable="false" />
      <el-form label-position="top" style="margin-top:16px">
        <el-form-item label="真实姓名" required><el-input v-model.trim="identityForm.real_name" maxlength="80" /></el-form-item>
        <el-form-item label="性别" required>
          <el-select v-model="identityForm.gender" style="width:100%">
            <el-option label="男" value="male" /><el-option label="女" value="female" />
            <el-option label="其他" value="other" /><el-option label="不披露" value="undisclosed" />
          </el-select>
        </el-form-item>
        <el-form-item label="出生日期" required><el-date-picker v-model="identityForm.birth_date" type="date" value-format="YYYY-MM-DD" style="width:100%" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="identityVisible=false">取消</el-button><el-button type="primary" :loading="identitySaving" @click="saveIdentityCorrection">保存修正</el-button></template>
    </el-dialog>

    <el-dialog v-model="passwordVisible" title="管理员修改用户密码" width="min(500px,94vw)">
      <el-alert title="新密码将永久生效，旧登录令牌立即失效；邮件会将新密码明文通知用户。" type="warning" show-icon :closable="false"/>
      <el-form label-position="top" style="margin-top:18px">
        <el-form-item label="用户"><el-input :model-value="passwordTarget?.username" disabled/></el-form-item>
        <el-form-item label="新密码" required><el-input v-model="newPassword" type="password" show-password minlength="8" maxlength="128" placeholder="至少 8 位"/></el-form-item>
      </el-form>
      <el-alert v-if="deliveryMessage" :title="deliveryMessage" :type="deliveryStatus==='failed'?'error':'success'" show-icon :closable="false"/>
      <template #footer>
        <el-button v-if="deliveryStatus==='failed'" @click="retryMail">重试邮件通知</el-button>
        <el-button @click="passwordVisible=false">关闭</el-button>
        <el-button type="primary" :loading="passwordSaving" @click="savePassword">修改密码</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { deleteInstitutionAccount } from "../../api/admin";
import {
  changeUserPassword,
  correctUserBasicProfile,
  deleteUser,
  fetchUser,
  fetchUsers,
  retryUserPasswordNotification,
  updateUser,
} from "../../api/users";

const items=ref([]),loading=ref(false),filters=reactive({q:"",role:"",active:""});
const pagination=reactive({page:1,page_size:20,total:0,pages:0});
const detailVisible=ref(false),detail=ref(null),passwordVisible=ref(false),passwordTarget=ref(null);
const newPassword=ref(""),passwordSaving=ref(false),deliveryStatus=ref(""),deliveryMessage=ref("");
const identityVisible=ref(false),identitySaving=ref(false),identityForm=reactive({real_name:"",gender:"",birth_date:""});
const roleName=(role)=>({user:"普通用户",institution_admin:"机构账号",admin:"系统管理员"}[role]||role);
const genderName=(gender)=>({male:"男",female:"女",other:"其他/未填写"}[gender]||"未填写");
const formatTime=(value)=>value?new Date(value).toLocaleString("zh-CN"):"—";
const mailStatusName=(value)=>({pending:"等待发送",sending:"发送中",sent:"已发送",failed:"发送失败"}[value]||"尚未修改");

async function load(){
  loading.value=true;
  try{
    const {data}=await fetchUsers({page:pagination.page,page_size:20,q:filters.q.trim()||undefined,role:filters.role||undefined,active:filters.active||undefined});
    items.value=data.items||[];
    Object.assign(pagination,data.pagination||{});
  }finally{loading.value=false;}
}
async function applyFilters(){pagination.page=1;await load();}
async function showDetail(row){detail.value=(await fetchUser(row.id)).data.item;detailVisible.value=true;}
function openIdentityCorrection(){Object.assign(identityForm,{real_name:detail.value.real_name||"",gender:detail.value.gender||"",birth_date:detail.value.birth_date||""});identityVisible.value=true;}
async function saveIdentityCorrection(){if(!identityForm.real_name||!identityForm.gender||!identityForm.birth_date)return ElMessage.error("请完整填写姓名、性别和出生日期");identitySaving.value=true;try{const{data}=await correctUserBasicProfile(detail.value.id,{...identityForm});detail.value={...detail.value,...(data.item||{})};identityVisible.value=false;ElMessage.success("实名信息已修正，邮件通知已进入发送队列");await load();}catch(error){ElMessage.error(error?.response?.data?.message||"实名信息修正失败");}finally{identitySaving.value=false;}}
function openPassword(row){passwordTarget.value=row;newPassword.value="";deliveryStatus.value="";deliveryMessage.value="";passwordVisible.value=true;}
async function savePassword(){
  if(newPassword.value.length<8)return ElMessage.error("新密码至少 8 位");
  passwordSaving.value=true;
  try{
    const {data}=await changeUserPassword(passwordTarget.value.id,newPassword.value);
    deliveryStatus.value=data.delivery?.status||"pending";
    deliveryMessage.value=`密码已生效，邮件状态：${deliveryStatus.value==="pending"?"等待发送":deliveryStatus.value}`;
    newPassword.value="";
    ElMessage.success("密码已修改，旧令牌已注销");
  }catch(error){
    deliveryStatus.value="failed";
    deliveryMessage.value=error?.response?.data?.message||"密码修改失败";
  }finally{passwordSaving.value=false;}
}
async function retryMail(){try{await retryUserPasswordNotification(passwordTarget.value.id);deliveryStatus.value="pending";deliveryMessage.value="邮件已重新进入发送队列";ElMessage.success(deliveryMessage.value);}catch(error){ElMessage.error(error?.response?.data?.message||"重试失败");}}
async function retryDetailMail(){try{await retryUserPasswordNotification(detail.value.id);detail.value.password_notification.status="pending";ElMessage.success("邮件已重新进入发送队列");}catch(error){ElMessage.error(error?.response?.data?.message||"重试失败");}}
async function toggle(row){try{await updateUser(row.id,{is_active:!row.is_active});await load();}catch(error){ElMessage.error(error?.response?.data?.message||"状态更新失败");}}
async function remove(row){try{await ElMessageBox.confirm(`将永久删除 ${row.username} 及其资料、测量、登记、已匹配报告、亲友关系和评论，无法恢复。`,"确认级联删除",{type:"error",confirmButtonText:"永久删除"});await deleteUser(row.id);ElMessage.success("用户已完整删除");await load();}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"删除失败");}}
async function removeStaff(row){try{await ElMessageBox.confirm("仅删除登录账号，历史报告中的提交账号信息会保留。","删除机构账号",{type:"warning"});await deleteInstitutionAccount(row.id);await load();}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"删除失败");}}
onMounted(load);
</script>
