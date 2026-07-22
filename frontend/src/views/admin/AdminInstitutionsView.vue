<template>
  <div class="workspace-page">
    <section class="page-intro">
      <div><p>机构主体与分院治理</p><h2>机构与分院管理</h2><span>先建立机构主体，再添加分院；预约和套餐按分院隔离，归档报告在同机构内只读共享。</span></div>
      <div><el-button @click="organizationDialogVisible=true">新增机构主体</el-button><el-button type="primary" @click="openCreate">新增分院</el-button></div>
    </section>
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
    <el-card shadow="never" class="filter-card"><div class="filter-row"><label class="filter-field"><span class="filter-field-label">搜索机构</span><el-input v-model="keyword" clearable placeholder="机构名称、分院或区域" /></label><label class="filter-field filter-field--compact"><span class="filter-field-label">机构状态</span><el-select v-model="statusFilter"><el-option label="全部状态" value="all" /><el-option label="启用" value="active" /><el-option label="停用" value="inactive" /></el-select></label></div></el-card>
    <el-card shadow="never" class="table-card">
      <el-table :data="filteredItems" v-loading="loading" empty-text="暂无机构">
        <el-table-column label="机构与分院" min-width="240"><template #default="scope"><div class="table-primary"><strong>{{ scope.row.organization?.name || scope.row.name }}</strong><small>{{ scope.row.branch_name }}</small></div></template></el-table-column>
        <el-table-column prop="district" label="区域" width="120" />
        <el-table-column prop="address" label="地址" min-width="220" show-overflow-tooltip />
        <el-table-column label="套餐" width="90"><template #default="scope">{{ scope.row.package_count ?? 0 }}/{{ scope.row.total_package_count ?? scope.row.package_count ?? 0 }}</template></el-table-column>
        <el-table-column label="机构账号" min-width="140"><template #default="scope">{{ scope.row.administrator_count || 0 }} 个</template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="scope"><el-tag :type="scope.row.is_active ? 'success' : 'info'">{{ scope.row.is_active ? "启用" : "已停用" }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="330" fixed="right"><template #default="scope"><el-button link type="primary" @click="openEdit(scope.row)">编辑机构</el-button><el-button link @click="openPackages(scope.row)">查看套餐</el-button><el-button link @click="openGallery(scope.row)">相册</el-button><el-button v-if="scope.row.is_active" link type="danger" @click="deactivate(scope.row)">停用机构</el-button><el-button v-else link type="success" @click="restore(scope.row)">恢复机构</el-button></template></el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="organizationDialogVisible" title="新增机构主体" width="560px">
      <el-form label-position="top"><el-form-item label="机构品牌名称" required><el-input v-model="organizationForm.name" maxlength="120"/></el-form-item><el-form-item label="机构介绍"><el-input v-model="organizationForm.description" type="textarea" :rows="4"/></el-form-item><el-form-item label="服务特色"><el-select v-model="organizationForm.service_features" multiple allow-create filterable placeholder="输入特色后回车" style="width:100%"/></el-form-item></el-form>
      <template #footer><el-button @click="organizationDialogVisible=false">取消</el-button><el-button type="primary" :loading="organizationSaving" @click="saveOrganization">创建机构主体</el-button></template>
    </el-dialog>

    <el-dialog v-model="dialogVisible" :title="institutionForm.id ? '编辑分院' : '新增分院'" width="680px" :close-on-click-modal="false">
      <el-form :model="institutionForm" label-position="top" class="responsive-form-grid">
        <el-form-item label="所属机构主体" required><el-select v-model="institutionForm.organization_id" :disabled="Boolean(institutionForm.id)" style="width:100%"><el-option v-for="item in organizations" :key="item.id" :label="item.name" :value="item.id"/></el-select></el-form-item>
        <el-form-item label="分院 / 门店" required><el-input v-model="institutionForm.branch_name" maxlength="120" /></el-form-item>
        <el-form-item label="区域" required><el-input v-model="institutionForm.district" maxlength="80" /></el-form-item>
        <el-form-item label="咨询电话"><el-input v-model="institutionForm.consult_phone" maxlength="30" /></el-form-item>
        <el-form-item label="地址" required class="form-grid-full"><el-input v-model="institutionForm.address" maxlength="255" /></el-form-item>
        <el-form-item label="交通信息" class="form-grid-full"><el-input v-model="institutionForm.metro_info" maxlength="255" /></el-form-item>
        <el-form-item label="分机号"><el-input v-model="institutionForm.ext" maxlength="20" /></el-form-item>
        <el-form-item label="轮休日"><el-input v-model="institutionForm.closed_day" maxlength="20" /></el-form-item>
        <el-form-item label="机构简介" class="form-grid-full"><el-input v-model="institutionForm.description" type="textarea" :rows="4" maxlength="2000" show-word-limit /></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveInstitution">保存</el-button></template>
    </el-dialog>

    <el-drawer v-model="packageDrawerVisible" :title="`${selectedInstitution?.name || ''} · 当前套餐`" size="min(760px, 94vw)">
      <el-alert title="管理员在“审核记录”中通过或驳回机构申请，不能在这里直接修改套餐。" type="info" show-icon :closable="false" />
      <el-table :data="packages" v-loading="packagesLoading" empty-text="暂无套餐">
        <el-table-column prop="name" label="套餐" min-width="160" /><el-table-column prop="focus_area" label="重点方向" min-width="140" /><el-table-column label="价格" width="100"><template #default="scope">¥{{ Number(scope.row.price || 0).toFixed(2) }}</template></el-table-column><el-table-column label="状态" width="90"><template #default="scope"><el-tag :type="scope.row.is_active ? 'success' : 'info'">{{ scope.row.is_active ? "启用" : "停用" }}</el-tag></template></el-table-column><el-table-column label="详情" width="100"><template #default="scope"><el-button link type="primary" @click="openPackageDetail(scope.row)">查看详情</el-button></template></el-table-column>
      </el-table>
    </el-drawer>

    <el-drawer v-model="packageDetailVisible" title="套餐完整详情" size="min(620px,94vw)">
      <el-descriptions v-if="selectedPackageDetail" :column="1" border>
        <el-descriptions-item label="套餐名称">{{selectedPackageDetail.name}}</el-descriptions-item><el-descriptions-item label="套餐类型">{{packageTypeLabel(selectedPackageDetail.package_type)}}</el-descriptions-item><el-descriptions-item label="适用人群">{{selectedPackageDetail.audience||genderScopeLabel(selectedPackageDetail.gender_scope)}}</el-descriptions-item><el-descriptions-item label="价格">¥{{Number(selectedPackageDetail.price||0).toFixed(2)}}</el-descriptions-item><el-descriptions-item label="健康方向">{{(selectedPackageDetail.domains||[]).map(item=>item.name).join('、')||'未配置'}}</el-descriptions-item><el-descriptions-item label="重点内容">{{selectedPackageDetail.focus_area||'—'}}</el-descriptions-item><el-descriptions-item label="套餐介绍">{{selectedPackageDetail.description||'—'}}</el-descriptions-item><el-descriptions-item label="检查前须知">{{selectedPackageDetail.booking_notice||'—'}}</el-descriptions-item><el-descriptions-item label="当前状态">{{selectedPackageDetail.is_active?'启用':'停用'}}</el-descriptions-item><el-descriptions-item label="当前版本">{{selectedPackageDetail.version?.version_number?`第 ${selectedPackageDetail.version.version_number} 版`:'尚无正式版本'}}</el-descriptions-item>
      </el-descriptions>
    </el-drawer>

    <el-drawer v-model="galleryDrawerVisible" :title="`${galleryInstitution?.name || ''} · 相册管理`" size="min(920px, 96vw)">
      <div class="drawer-toolbar">
        <p>最多 8 张；拖拽排序后需保存，第一张自动成为封面。</p>
        <div><el-button :disabled="!galleryOrderChanged" :loading="galleryOrdering" @click="saveGalleryOrder">保存排序</el-button><el-button type="primary" :disabled="adminImages.length >= 8 || !galleryInstitution?.is_active" :loading="galleryUploading" @click="galleryFileInput?.click()">上传图片</el-button><input ref="galleryFileInput" hidden type="file" accept="image/jpeg,image/png,image/webp" @change="uploadGalleryImage" /></div>
      </div>
      <el-alert title="支持 JPEG、PNG、WebP，每张不超过 5 MB；服务端会验证真实格式并移除 EXIF。" type="info" show-icon :closable="false" />
      <div v-loading="galleryLoading" class="admin-gallery-stage">
        <el-empty v-if="!galleryLoading && adminImages.length === 0" description="暂无机构图片" />
        <div v-else class="gallery-grid">
          <article v-for="(image, index) in adminImages" :key="image.id" class="gallery-item" :class="{ 'is-dragging': galleryDragIndex === index }" draggable="true" @dragstart="galleryDragIndex=index" @dragover.prevent @drop="dropGalleryAt(index)" @dragend="galleryDragIndex=null">
            <div class="gallery-image-wrap"><img :src="image.image_url" :alt="`机构图片 ${index+1}`" /><el-tag v-if="index===0" type="success" effect="dark" class="gallery-cover-tag">封面</el-tag><span class="gallery-order">{{ index+1 }}</span></div>
            <div class="gallery-item-actions"><span>拖拽排序</span><div><el-button link :disabled="index===0" @click="moveGallery(index,-1)">前移</el-button><el-button link :disabled="index===adminImages.length-1" @click="moveGallery(index,1)">后移</el-button><el-button link type="danger" @click="removeGalleryImage(image)">删除</el-button></div></div>
          </article>
          <button v-if="adminImages.length<8 && galleryInstitution?.is_active" type="button" class="gallery-add" @click="galleryFileInput?.click()"><span>＋</span><strong>继续上传</strong><small>{{ adminImages.length }}/8 张</small></button>
        </div>
      </div>
    </el-drawer>

  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { createAdminOrganization, createAdminInstitution, createAdminPackage, deactivateAdminInstitution, deactivateAdminPackage, deleteAdminImage, fetchAdminImages, fetchAdminInstitutions, fetchAdminOrganizations, fetchAdminPackages, reorderAdminImages, restoreAdminInstitution, updateAdminInstitution, updateAdminPackage, uploadAdminImage } from "../../api/admin";

const items=ref([]),organizations=ref([]),loading=ref(false),saving=ref(false),errorMessage=ref(""),keyword=ref(""),statusFilter=ref("all"),dialogVisible=ref(false),organizationDialogVisible=ref(false),organizationSaving=ref(false);
const organizationForm=reactive({name:"",description:"",service_features:[]});
const institutionForm=reactive({id:null,organization_id:null,branch_name:"",district:"",address:"",metro_info:"",consult_phone:"",ext:"",closed_day:"",description:""});
const filteredItems=computed(()=>items.value.filter((item)=>{const text=`${item.name} ${item.branch_name} ${item.district}`.toLowerCase();const matchesKeyword=text.includes(keyword.value.trim().toLowerCase());const matchesStatus=statusFilter.value==="all"||(statusFilter.value==="active"?item.is_active:!item.is_active);return matchesKeyword&&matchesStatus;}));
const packageDrawerVisible=ref(false),selectedInstitution=ref(null),packages=ref([]),packagesLoading=ref(false),packageDialogVisible=ref(false),packageSaving=ref(false); const packageForm=reactive({id:null,name:"",focus_area:"",gender_scope:"all",price:0,description:""});
const packageDetailVisible=ref(false),selectedPackageDetail=ref(null);const packageTypeLabel=(value)=>({special:"专项套餐",combined:"组合套餐"}[value]||"体检套餐");const genderScopeLabel=(value)=>({all:"不限性别",male:"男性",female:"女性",female_all:"女性人群"}[value]||"不限性别");function openPackageDetail(item){selectedPackageDetail.value=item;packageDetailVisible.value=true;}
const galleryDrawerVisible=ref(false),galleryInstitution=ref(null),adminImages=ref([]),galleryLoading=ref(false),galleryUploading=ref(false),galleryOrdering=ref(false),galleryOrderChanged=ref(false),galleryDragIndex=ref(null),galleryFileInput=ref(null);
function resetInstitution(){Object.assign(institutionForm,{id:null,organization_id:organizations.value[0]?.id||null,branch_name:"",district:"",address:"",metro_info:"",consult_phone:"",ext:"",closed_day:"",description:""});}
function openCreate(){resetInstitution();dialogVisible.value=true;}
function openEdit(item){Object.keys(institutionForm).forEach((key)=>institutionForm[key]=item[key]??(key==="id"?null:""));dialogVisible.value=true;}
async function load(){loading.value=true;try{const[a,b]=await Promise.all([fetchAdminInstitutions(),fetchAdminOrganizations()]);items.value=a.data.items||[];organizations.value=b.data.items||[];}catch(error){errorMessage.value=error?.response?.data?.message||"机构列表加载失败";}finally{loading.value=false;}}
async function saveOrganization(){if(!organizationForm.name.trim())return ElMessage.error("请填写机构品牌名称");organizationSaving.value=true;try{await createAdminOrganization({name:organizationForm.name.trim(),description:organizationForm.description.trim()||null,service_features:organizationForm.service_features});Object.assign(organizationForm,{name:"",description:"",service_features:[]});organizationDialogVisible.value=false;ElMessage.success("机构主体已创建，现在可以添加分院");await load();}catch(error){ElMessage.error(error?.response?.data?.message||"机构主体创建失败");}finally{organizationSaving.value=false;}}
async function saveInstitution(){if(!institutionForm.organization_id||!institutionForm.branch_name.trim()||!institutionForm.district.trim()||!institutionForm.address.trim()){ElMessage.error("请选择机构主体并填写分院、区域和地址");return;}saving.value=true;const payload=Object.fromEntries(Object.entries(institutionForm).filter(([key])=>key!=="id").map(([key,value])=>[key,typeof value==="string"?(value.trim()||null):value]));try{if(institutionForm.id)await updateAdminInstitution(institutionForm.id,payload);else await createAdminInstitution(payload);ElMessage.success(institutionForm.id?"分院已更新":"分院已创建");dialogVisible.value=false;await load();}catch(error){ElMessage.error(error?.response?.data?.message||"分院保存失败");}finally{saving.value=false;}}
async function deactivate(item){try{await ElMessageBox.confirm("停用机构会禁止其机构账号登录，并使未使用邀请码失效；历史业务数据保留。","停用机构",{type:"warning",confirmButtonText:"确认停用",cancelButtonText:"取消"});await deactivateAdminInstitution(item.id);ElMessage.success("机构已停用");await load();}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"停用失败");}}
async function restore(item){try{await restoreAdminInstitution(item.id);ElMessage.success("机构已恢复，原有机构账号可继续登录");await load();}catch(error){ElMessage.error(error?.response?.data?.message||"恢复失败");}}
async function openPackages(item){selectedInstitution.value=item;packageDrawerVisible.value=true;await loadPackages();}
async function loadPackages(){if(!selectedInstitution.value)return;packagesLoading.value=true;try{const{data}=await fetchAdminPackages(selectedInstitution.value.id);packages.value=data.items||[];}catch(error){ElMessage.error(error?.response?.data?.message||"套餐加载失败");}finally{packagesLoading.value=false;}}
function resetPackage(){Object.assign(packageForm,{id:null,name:"",focus_area:"",gender_scope:"all",price:0,description:""});}
function openPackageCreate(){resetPackage();packageDialogVisible.value=true;}
function openPackageEdit(item){Object.assign(packageForm,{id:item.id,name:item.name||"",focus_area:item.focus_area||"",gender_scope:item.gender_scope||"all",price:Number(item.price||0),description:item.description||""});packageDialogVisible.value=true;}
async function savePackage(){if(!packageForm.name.trim()||!packageForm.focus_area.trim()){ElMessage.error("请填写套餐名称和重点方向");return;}packageSaving.value=true;const payload={name:packageForm.name.trim(),focus_area:packageForm.focus_area.trim(),gender_scope:packageForm.gender_scope,price:Number(packageForm.price),description:packageForm.description.trim()||null};try{if(packageForm.id)await updateAdminPackage(selectedInstitution.value.id,packageForm.id,payload);else await createAdminPackage(selectedInstitution.value.id,payload);ElMessage.success("套餐已保存");packageDialogVisible.value=false;await Promise.all([loadPackages(),load()]);}catch(error){ElMessage.error(error?.response?.data?.message||"套餐保存失败");}finally{packageSaving.value=false;}}
async function deactivatePackage(item){try{await deactivateAdminPackage(selectedInstitution.value.id,item.id);ElMessage.success("套餐已停用");await loadPackages();}catch(error){ElMessage.error(error?.response?.data?.message||"停用失败");}}
async function restorePackage(item){try{await updateAdminPackage(selectedInstitution.value.id,item.id,{is_active:true});ElMessage.success("套餐已恢复");await loadPackages();}catch(error){ElMessage.error(error?.response?.data?.message||"恢复失败");}}
async function openGallery(item){galleryInstitution.value=item;galleryDrawerVisible.value=true;await loadGallery();}
async function loadGallery(){if(!galleryInstitution.value)return;galleryLoading.value=true;try{const{data}=await fetchAdminImages(galleryInstitution.value.id);adminImages.value=[...(data.items||[])].sort((a,b)=>(a.sort_order??0)-(b.sort_order??0));galleryOrderChanged.value=false;}catch(error){ElMessage.error(error?.response?.data?.message||"机构相册加载失败");}finally{galleryLoading.value=false;}}
function moveGallery(index,delta){const target=index+delta;if(target<0||target>=adminImages.value.length)return;const next=[...adminImages.value];[next[index],next[target]]=[next[target],next[index]];adminImages.value=next;galleryOrderChanged.value=true;}
function dropGalleryAt(target){if(galleryDragIndex.value===null||galleryDragIndex.value===target)return;const next=[...adminImages.value];const[moved]=next.splice(galleryDragIndex.value,1);next.splice(target,0,moved);adminImages.value=next;galleryDragIndex.value=null;galleryOrderChanged.value=true;}
async function uploadGalleryImage(event){const file=event.target.files?.[0];event.target.value="";if(!file)return;if(!['image/jpeg','image/png','image/webp'].includes(file.type)){ElMessage.error("仅支持 JPEG、PNG、WebP 图片");return;}if(file.size>5*1024*1024){ElMessage.error("图片不能超过 5 MB");return;}if(adminImages.value.length>=8){ElMessage.warning("每家机构最多 8 张图片");return;}galleryUploading.value=true;try{await uploadAdminImage(galleryInstitution.value.id,file);ElMessage.success("图片已上传");await Promise.all([loadGallery(),load()]);}catch(error){ElMessage.error(error?.response?.data?.message||"上传失败");}finally{galleryUploading.value=false;}}
async function saveGalleryOrder(){galleryOrdering.value=true;try{await reorderAdminImages(galleryInstitution.value.id,adminImages.value.map((item)=>item.id));ElMessage.success("图片顺序已保存");await loadGallery();}catch(error){ElMessage.error(error?.response?.data?.message||"排序保存失败");}finally{galleryOrdering.value=false;}}
async function removeGalleryImage(image){try{await ElMessageBox.confirm("删除后无法恢复，确认删除该机构图片？","删除图片",{type:"warning",confirmButtonText:"删除",cancelButtonText:"取消"});await deleteAdminImage(galleryInstitution.value.id,image.id);ElMessage.success("图片已删除");await Promise.all([loadGallery(),load()]);}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"删除失败");}}
onMounted(load);
</script>
