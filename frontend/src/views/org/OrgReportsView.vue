<template>
  <div class="workspace-page report-workspace">
    <section class="report-hero">
      <div><p class="report-kicker">体检服务工作台</p><h2>接待与健康数据归档</h2><p>按实际业务进度处理受检者，从到检确认到健康数据正式交付。</p></div>
      <button type="button" class="capacity-pill" @click="capacityVisible=true"><span>每日接待能力</span><strong>{{ limited ? `${dailyLimit} 人` : "不限人数" }}</strong><small>点击调整</small></button>
    </section>

    <nav class="report-tabs" aria-label="体检管理分类">
      <button v-for="tab in tabs" :key="tab.value" type="button" :class="{active:view===tab.value}" @click="selectView(tab.value)"><span>{{tab.label}}</span><b>{{tab.count}}</b></button>
    </nav>

    <section v-if="!['history','shared'].includes(view)" class="report-toolbar">
      <div><strong>{{ currentTab.title }}</strong><small>{{ currentTab.description }}</small></div>
      <el-select v-if="view==='all'" v-model="status" clearable placeholder="筛选预约状态" @change="load"><el-option v-for="item in appointmentStatuses" :key="item.value" :label="item.label" :value="item.value"/></el-select>
    </section>

    <section v-if="!['history','shared'].includes(view)" class="appointment-card-list" v-loading="loading">
      <article v-for="item in visibleAppointments" :key="item.id" class="appointment-card">
        <div class="appointment-date"><strong>{{ formatDay(item.appointment_date) }}</strong><span>{{ formatMonth(item.appointment_date) }}</span></div>
        <div class="appointment-main">
          <div class="appointment-title"><div><h3>{{ item.user?.name || "受检者" }}</h3><p>{{ item.package_name || "体检服务" }}</p></div><el-tag :type="appointmentType(item.status)">{{appointmentLabel(item.status)}}</el-tag></div>
          <div class="appointment-meta"><span>健康身份码 {{item.user?.health_id || '—'}}</span><span>{{ reportProgress(item) }}</span></div>
          <div class="appointment-next"><small>下一步</small><strong>{{ nextActionText(item) }}</strong></div>
        </div>
        <div class="appointment-actions">
          <template v-if="item.status==='unfulfilled'"><el-button type="primary" @click="attend(item)">确认到检</el-button><el-button @click="invalidate(item)">未到检</el-button></template>
          <template v-else-if="item.status==='awaiting_report'&&!item.report_id"><el-button type="primary" @click="createManual(item)">录入检查结果</el-button><el-button @click="openOcr(item)">导入体检报告并识别</el-button></template>
          <template v-else-if="item.report_id"><el-button type="primary" plain @click="openDetailById(item.report_id)">{{ item.report_status==='published' ? '查看健康数据' : '继续完善结果' }}</el-button></template>
          <span v-else class="appointment-finished">当前无需处理</span>
        </div>
      </article>
      <el-empty v-if="!loading&&!visibleAppointments.length" :description="currentTab.empty" />
    </section>

    <section v-else class="archive-grid" v-loading="loading">
      <article v-for="item in displayedReports" :key="item.id" class="archive-card">
        <header><span>{{item.access_mode==='cross_branch_read_only'?'跨院只读':'已交付'}}</span><strong>{{item.exam_date}}</strong></header>
        <h3>{{item.subject_name_snapshot || '受检者'}}</h3>
        <p>{{item.source_branch?.name || item.institution?.name || '本机构'}} · {{item.source_branch?.branch_name || item.institution?.branch_name || '当前分院'}}</p>
        <footer><span>{{item.indicator_count || 0}} 项结构化指标</span><el-button link type="primary" @click="openDetailById(item.id)">查看完整内容</el-button></footer>
      </article>
      <el-empty v-if="!loading&&!displayedReports.length" :description="view==='shared'?'同机构暂时没有可共享的归档报告':'暂无已归档健康数据'" />
    </section>

    <el-drawer v-model="capacityVisible" title="每日接待能力" size="min(440px,94vw)">
      <div class="capacity-editor"><p>设置机构每天最多接受多少位受检者预约。降低上限不会取消已有预约。</p><el-switch v-model="limited" active-text="限制每日人数" inactive-text="不限制人数"/><el-input-number v-if="limited" v-model="dailyLimit" :min="1" :step="1"/><el-button type="primary" :loading="capacitySaving" @click="saveCapacity">保存接待能力</el-button></div>
    </el-drawer>

    <el-dialog v-model="ocrVisible" title="导入体检报告并识别" width="min(560px,94vw)">
      <el-alert title="系统只会创建待复核草稿，所有识别结果都需要工作人员确认后才能归档。" type="warning" show-icon :closable="false"/>
      <label class="upload-field"><strong>选择报告文件</strong><span>支持 PDF、PNG、JPG、WEBP</span><input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" @change="ocrFile=$event.target.files?.[0]||null"/></label>
      <template #footer><el-button @click="ocrVisible=false">取消</el-button><el-button type="primary" :loading="ocrLoading" @click="runOcr">导入并创建待复核数据</el-button></template>
    </el-dialog>

    <el-drawer v-model="detailVisible" title="健康数据生产" size="min(960px,98vw)" class="report-detail-drawer">
      <template v-if="current">
        <el-alert v-if="current.access_mode==='cross_branch_read_only'" title="该报告由同机构其他分院归档，当前仅可查看，不能修改或重新提交。" type="info" show-icon :closable="false"/>
        <section class="report-subject"><div><small>受检者</small><h3>{{current.subject_name_snapshot}}</h3><p>{{current.subject_health_id}} · {{current.exam_date}}</p></div><el-tag :type="reportType(current.status)">{{reportLabel(current.status)}}</el-tag></section>
        <el-steps :active="productionStep" finish-status="success" align-center class="production-steps"><el-step title="检查结果"/><el-step title="文字结论"/><el-step title="检查附件"/><el-step title="复核归档"/></el-steps>
        <el-alert :title="`本次服务可归档的健康领域：${allowedDomains.map(x=>x.name).join('、') || '等待配置'}`" type="info" :closable="false"/>

        <section class="production-section">
          <header><div><span>01</span><h3>结构化检查结果</h3></div><small>{{current.indicators?.length||0}} 项</small></header>
          <div v-if="current.status==='draft'" class="result-entry"><el-select v-model="indicatorForm.indicator_dict_id" filterable placeholder="选择标准指标"><el-option v-for="item in allowedIndicators" :key="item.id" :label="`${item.name}（${item.unit||'-'}）`" :value="item.id"/></el-select><el-input v-model="indicatorForm.value" placeholder="检查结果"/><el-button type="primary" @click="addIndicator">添加</el-button></div>
          <div class="result-list"><article v-for="item in current.indicators||[]" :key="item.id"><span><strong>{{item.indicator?.name}}</strong><small>{{item.normalized_unit||item.indicator?.unit||'无单位'}}</small></span><b>{{item.value}}</b><el-button v-if="current.status==='draft'" link type="danger" @click="removeIndicator(item)">删除</el-button></article><el-empty v-if="!(current.indicators||[]).length" description="尚未录入检查结果" :image-size="54"/></div>
        </section>

        <section class="production-section">
          <header><div><span>02</span><h3>专业文字结论</h3></div><small>{{current.text_results?.length||0}} 条</small></header>
          <div v-if="current.status==='draft'" class="text-entry"><el-select v-model="textForm.health_domain_id" placeholder="健康领域"><el-option v-for="d in allowedDomains" :key="d.id" :label="d.name" :value="d.id"/></el-select><el-input v-model="textForm.title" placeholder="结论标题"/><el-input v-model="textForm.body" type="textarea" :rows="2" placeholder="填写检查所见或专业结论"/><el-button @click="addText">添加结论</el-button></div>
          <article v-for="item in current.text_results||[]" :key="item.id" class="text-result"><div><strong>{{item.title}}</strong><p>{{item.body}}</p></div><el-button v-if="current.status==='draft'" link type="danger" @click="removeText(item)">删除</el-button></article>
        </section>

        <section class="production-section">
          <header><div><span>03</span><h3>检查影像与附件</h3></div><small>{{current.assets?.length||0}} 份</small></header>
          <div v-if="current.status==='draft'" class="asset-entry"><el-select v-model="assetForm.health_domain_id" placeholder="健康领域"><el-option v-for="d in allowedDomains" :key="d.id" :label="d.name" :value="d.id"/></el-select><input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" @change="assetFile=$event.target.files?.[0]||null"/><el-button @click="addAsset">上传检查附件</el-button></div>
          <article v-for="item in current.assets||[]" :key="item.id" class="asset-result"><span><strong>{{item.title}}</strong><small>{{item.annotation||'暂无机构批注'}}</small></span><div class="asset-actions"><el-button link type="primary" @click="viewAsset(item)">查看检查影像</el-button><el-button v-if="current.status==='draft'" link type="danger" @click="removeAsset(item)">删除</el-button></div></article>
        </section>

        <section class="production-final"><div><h3>复核与正式归档</h3><p>{{ finalGuidance }}</p></div><el-button v-if="current.status==='draft'" type="success" @click="lockReport(current.id)">完成复核并锁定</el-button><el-button v-else-if="current.status==='locked'" type="primary" @click="submitReport(current.id)">提交并交付给用户</el-button><el-tag v-else type="success">已正式交付</el-tag></section>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { fetchIndicatorDicts } from "../../api/indicators";
import { addOrgReportIndicator, addOrgTextResult, uploadOrgHealthAsset, deleteOrgTextResult, deleteOrgHealthAsset, attendOrgAppointment, createOrgReport, deleteOrgReportIndicator, fetchOrgAppointmentCapacity, fetchOrgAppointments, fetchOrgReport, fetchOrgReportAssetContent, fetchOrgReports, invalidateOrgAppointment, lockOrgReport, submitOrgReport, updateOrgAppointmentCapacity, uploadOrgReportOcr } from "../../api/org";

const route=useRoute(),router=useRouter();
const appointmentStatuses=[{value:"unfulfilled",label:"待到检"},{value:"awaiting_report",label:"待归档"},{value:"fulfilled",label:"已完成"},{value:"invalidated",label:"未到检"},{value:"cancelled",label:"已取消"}];
const appointmentLabel=(value)=>appointmentStatuses.find((item)=>item.value===value)?.label||"处理中";
const appointmentType=(value)=>({fulfilled:"success",invalidated:"danger",cancelled:"info",awaiting_report:"warning"}[value]||"");
const reportLabel=(value)=>({draft:"待完善",locked:"待提交",published:"已交付"}[value]||"尚未创建");
const reportType=(value)=>({draft:"warning",locked:"primary",published:"success"}[value]||"info");
const items=ref([]),archivedReports=ref([]),sharedReports=ref([]),loading=ref(false),status=ref(""),view=ref(["today","archive","all","history","shared"].includes(route.query.view)?route.query.view:"today"),tabCounts=reactive({today:0,archive:0,all:0}),limited=ref(false),dailyLimit=ref(20),capacitySaving=ref(false),capacityVisible=ref(false),ocrVisible=ref(false),ocrFile=ref(null),ocrLoading=ref(false),selectedAppointment=ref(null),detailVisible=ref(false),current=ref(null),indicators=ref([]),assetFile=ref(null);
const indicatorForm=reactive({indicator_dict_id:null,value:""}),textForm=reactive({health_domain_id:null,title:"",body:""}),assetForm=reactive({health_domain_id:null});
const allowedDomains=computed(()=>current.value?.package_version?.domains||[]);
const allowedIndicators=computed(()=>{const ids=new Set(allowedDomains.value.map((x)=>x.id));return indicators.value.filter((x)=>(x.domains||[]).some((domain)=>ids.has(domain.id)));});
const visibleAppointments=computed(()=>items.value);
const tabs=computed(()=>[{value:"today",label:"今日接待",count:tabCounts.today},{value:"archive",label:"待归档",count:tabCounts.archive},{value:"all",label:"全部预约",count:tabCounts.all},{value:"history",label:"本院归档",count:archivedReports.value.length},{value:"shared",label:"机构共享档案",count:sharedReports.value.length}]);
const displayedReports=computed(()=>view.value==="shared"?sharedReports.value:archivedReports.value);
const tabCopy={today:{title:"今天需要接待的受检者",description:"核对身份并确认到检后，开始本次体检流程。",empty:"今天暂无待接待预约"},archive:{title:"等待完善与归档",description:"补充检查结果、文字结论和附件，复核后正式交付。",empty:"当前没有待归档任务"},all:{title:"全部预约进度",description:"查看本机构所有预约及其当前服务状态。",empty:"暂无预约记录"}};
const currentTab=computed(()=>tabCopy[view.value]||tabCopy.all);
const productionStep=computed(()=>current.value?.status==="published"?4:current.value?.status==="locked"?3:Math.min(2,[(current.value?.indicators||[]).length,(current.value?.text_results||[]).length,(current.value?.assets||[]).length].filter(Boolean).length));
const finalGuidance=computed(()=>current.value?.status==="draft"?"确认结果、结论和附件无误后锁定。锁定后将不能继续修改。":current.value?.status==="locked"?"内容已锁定。提交后将永久归档并向用户交付。":"本份健康数据已正式交付，内容不可修改或撤下。");

function dateParts(value){const match=String(value||"").match(/^(\d{4})-(\d{2})-(\d{2})/);return match?{day:match[3],month:`${Number(match[2])} 月`}:{day:"—",month:"日期待核对"};} function formatDay(value){return dateParts(value).day;} function formatMonth(value){return dateParts(value).month;}
function reportProgress(item){return item.report_status?`健康数据：${reportLabel(item.report_status)}`:"尚未建立健康数据";}
function nextActionText(item){if(item.status==="unfulfilled")return"核对受检者并确认到检";if(item.status==="awaiting_report"&&!item.report_id)return"建立并录入本次检查结果";if(item.report_status==="draft")return"继续完善并复核检查结果";if(item.report_status==="locked")return"提交正式归档";if(item.status==="fulfilled")return"本次服务已完成";return"无需继续处理";}
async function selectView(value){view.value=value;status.value="";await router.replace({query:{...route.query,view:value}});await load();}
async function load(){loading.value=true;try{const appointmentParams={view:view.value};if(view.value==="all"&&status.value)appointmentParams.status=status.value;const [appointmentResponse,reportResponse,sharedResponse]=await Promise.all([fetchOrgAppointments(appointmentParams),fetchOrgReports({status:"published",scope:"branch"}),fetchOrgReports({scope:"organization"})]);items.value=appointmentResponse.data.items||[];Object.assign(tabCounts,appointmentResponse.data.tab_counts||{});archivedReports.value=reportResponse.data.items||[];sharedReports.value=(sharedResponse.data.items||[]).filter((item)=>item.access_mode==="cross_branch_read_only");}catch(error){ElMessage.error(error?.response?.data?.message||"体检任务加载失败");}finally{loading.value=false;}}
async function loadCapacity(){try{const{data}=await fetchOrgAppointmentCapacity();limited.value=!data.unlimited;if(data.daily_appointment_limit)dailyLimit.value=data.daily_appointment_limit;}catch{ElMessage.error("接待能力加载失败");}}
async function saveCapacity(){capacitySaving.value=true;try{await updateOrgAppointmentCapacity(limited.value?dailyLimit.value:null);ElMessage.success(limited.value?`每日接待能力已设为 ${dailyLimit.value} 人`:"已设为不限人数");capacityVisible.value=false;}catch(error){ElMessage.error(error?.response?.data?.message||"保存失败");}finally{capacitySaving.value=false;}}
async function attend(item){try{await ElMessageBox.confirm("请确认已经核对受检者身份且本人已到检。确认后用户不能再取消本次预约。","确认到检",{type:"warning",confirmButtonText:"已核对，确认到检"});await attendOrgAppointment(item.id);ElMessage.success("已确认到检，可以开始录入检查结果");await load();}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"操作失败");}}
async function invalidate(item){try{await ElMessageBox.confirm("标记未到检后，本次预约将结束且不能继续录入健康数据。","确认未到检",{type:"warning",confirmButtonText:"确认未到检"});await invalidateOrgAppointment(item.id);ElMessage.success("已记录为未到检");await load();}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"操作失败");}}
async function createManual(item){try{const{data}=await createOrgReport({appointment_id:item.id});ElMessage.success("已建立待完善健康数据");await load();await openDetailById(data.item.id);}catch(error){ElMessage.error(error?.response?.data?.message||"建立失败");}}
function openOcr(item){selectedAppointment.value=item;ocrFile.value=null;ocrVisible.value=true;}
async function runOcr(){if(!ocrFile.value){ElMessage.error("请选择体检报告文件");return;}ocrLoading.value=true;try{const{data}=await uploadOrgReportOcr(ocrFile.value,{appointment_id:selectedAppointment.value.id});ocrVisible.value=false;ElMessage.success(`已建立待复核健康数据，识别到 ${data.item.indicator_count} 项结果`);await load();await openDetailById(data.item.id);}catch(error){ElMessage.error(error?.response?.data?.message||"报告识别失败");}finally{ocrLoading.value=false;}}
async function openDetailById(id){current.value=(await fetchOrgReport(id)).data.item;const first=current.value?.package_version?.domains?.[0]?.id;textForm.health_domain_id=first||null;assetForm.health_domain_id=first||null;indicatorForm.indicator_dict_id=allowedIndicators.value[0]?.id||null;detailVisible.value=true;}
async function addIndicator(){if(!indicatorForm.indicator_dict_id||!String(indicatorForm.value).trim())return ElMessage.error("请选择指标并填写检查结果");try{await addOrgReportIndicator(current.value.id,indicatorForm);indicatorForm.value="";await openDetailById(current.value.id);}catch(error){ElMessage.error(error?.response?.data?.message||"添加失败");}}
async function removeIndicator(item){try{await deleteOrgReportIndicator(current.value.id,item.id);await openDetailById(current.value.id);}catch(error){ElMessage.error(error?.response?.data?.message||"删除失败");}}
async function addText(){if(!textForm.health_domain_id||!textForm.title.trim()||!textForm.body.trim())return ElMessage.error("请完整填写结论所属领域、标题和内容");try{await addOrgTextResult(current.value.id,textForm);Object.assign(textForm,{health_domain_id:allowedDomains.value[0]?.id||null,title:"",body:""});await openDetailById(current.value.id);}catch(error){ElMessage.error(error?.response?.data?.message||"添加结论失败");}}
async function removeText(item){try{await deleteOrgTextResult(current.value.id,item.id);await openDetailById(current.value.id);}catch(error){ElMessage.error(error?.response?.data?.message||"删除失败");}}
async function addAsset(){if(!assetFile.value)return ElMessage.error("请选择检查附件");if(!assetForm.health_domain_id)return ElMessage.error("请选择附件所属健康领域");try{await uploadOrgHealthAsset(current.value.id,assetFile.value,{health_domain_id:assetForm.health_domain_id,title:assetFile.value.name,modality:"image"});assetFile.value=null;await openDetailById(current.value.id);}catch(error){ElMessage.error(error?.response?.data?.message||"附件上传失败");}}
async function viewAsset(item){try{const response=await fetchOrgReportAssetContent(current.value.id,item.id);const url=URL.createObjectURL(response.data);window.open(url,"_blank","noopener,noreferrer");window.setTimeout(()=>URL.revokeObjectURL(url),60000);}catch(error){ElMessage.error(error?.response?.data?.message||"检查影像读取失败");}}
async function removeAsset(item){try{await deleteOrgHealthAsset(current.value.id,item.id);await openDetailById(current.value.id);}catch(error){ElMessage.error(error?.response?.data?.message||"删除失败");}}
async function lockReport(id){try{await ElMessageBox.confirm("请再次核对本次检查结果。锁定后将不能继续修改。","完成复核并锁定",{type:"warning",confirmButtonText:"确认锁定"});await lockOrgReport(id);ElMessage.success("内容已锁定，等待正式提交");await load();await openDetailById(id);}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"锁定失败");}}
async function submitReport(id){try{await ElMessageBox.confirm("提交后健康数据将永久归档并向用户交付，不能修改或撤下。","提交正式归档",{type:"warning",confirmButtonText:"确认提交并交付"});await submitOrgReport(id);ElMessage.success("健康数据已正式交付");detailVisible.value=false;await load();}catch(error){if(error!=="cancel"&&error!=="close")ElMessage.error(error?.response?.data?.message||"提交失败");}}
onMounted(async()=>{try{const response=await fetchIndicatorDicts();indicators.value=response.data.items||[];indicatorForm.indicator_dict_id=indicators.value[0]?.id||null;}catch{ElMessage.error("标准指标加载失败");}await Promise.all([load(),loadCapacity()]);});
</script>

<style scoped>
.report-workspace{display:grid;gap:18px}.report-hero{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:26px;border:1px solid #dbe8e6;border-radius:18px;background:linear-gradient(135deg,#f0faf7,#fff)}.report-kicker{margin:0;color:var(--workspace-accent);font-size:12px;font-weight:800;letter-spacing:.08em}.report-hero h2{margin:5px 0 8px;color:#173f42;font-size:30px}.report-hero p:last-child{margin:0;color:#647a7d}.capacity-pill{display:grid;gap:2px;min-width:156px;padding:13px 16px;border:1px solid #cfe1de;border-radius:13px;color:#496365;background:#fff;cursor:pointer;text-align:left}.capacity-pill strong{color:#15695f;font-size:20px}.capacity-pill small{color:#849294}.report-tabs{display:flex;gap:7px;padding:6px;border:1px solid #e0e8e7;border-radius:13px;background:#fff;overflow-x:auto}.report-tabs button{display:flex;align-items:center;justify-content:center;gap:8px;min-width:140px;padding:11px 15px;border:0;border-radius:9px;color:#687c7e;background:transparent;cursor:pointer;font-weight:700;white-space:nowrap}.report-tabs button.active{color:#0b685d;background:#e9f5f2}.report-tabs b{display:grid;place-items:center;min-width:22px;height:22px;padding:0 5px;border-radius:99px;background:rgba(25,99,91,.09);font-size:11px}.report-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px}.report-toolbar div{display:grid;gap:3px}.report-toolbar strong{color:#24494c}.report-toolbar small{color:#718486}.report-toolbar :deep(.el-select){width:190px}.appointment-card-list{display:grid;gap:12px;min-height:160px}.appointment-card{display:grid;grid-template-columns:74px minmax(0,1fr) auto;gap:18px;align-items:center;padding:18px;border:1px solid #dfe8e7;border-radius:15px;background:#fff}.appointment-date{display:grid;place-items:center;padding:10px;border-right:1px solid #edf1f0}.appointment-date strong{color:#165f57;font-size:28px}.appointment-date span{color:#758688;font-size:12px}.appointment-main{display:grid;gap:10px}.appointment-title{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.appointment-title h3,.appointment-title p{margin:0}.appointment-title h3{color:#1f4649}.appointment-title p{margin-top:3px;color:#627779;font-size:13px}.appointment-meta{display:flex;gap:16px;flex-wrap:wrap;color:#718385;font-size:12px}.appointment-next{display:flex;align-items:center;gap:8px}.appointment-next small{padding:3px 7px;border-radius:5px;color:#6c7d7f;background:#f1f4f4}.appointment-next strong{color:#405b5d;font-size:13px}.appointment-actions{display:flex;flex-direction:column;align-items:stretch;gap:8px;min-width:170px}.appointment-actions :deep(.el-button){margin:0}.appointment-finished{color:#879496;font-size:12px;text-align:center}.archive-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.archive-card{padding:18px;border:1px solid #dfe8e7;border-radius:15px;background:#fff}.archive-card header,.archive-card footer{display:flex;align-items:center;justify-content:space-between;gap:10px}.archive-card header span{padding:4px 8px;border-radius:99px;color:#15695f;background:#e9f5f2;font-size:11px}.archive-card h3{margin:18px 0 4px;color:#24484b}.archive-card p{margin:0 0 18px;color:#728385;font-size:12px}.archive-card footer{padding-top:12px;border-top:1px solid #edf1f0;color:#687b7d;font-size:12px}.capacity-editor{display:grid;gap:20px}.capacity-editor p{margin:0;color:#627678;line-height:1.7}.capacity-editor :deep(.el-input-number){width:100%}.upload-field{display:grid;gap:8px;margin-top:18px;padding:18px;border:1px dashed #afc9c5;border-radius:12px;background:#f7fbfa}.upload-field span{color:#738587;font-size:12px}.report-subject{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:18px;border:1px solid #dce8e6;border-radius:14px;background:#f6fbfa}.report-subject h3,.report-subject p{margin:3px 0}.report-subject p,.report-subject small{color:#6f8183}.production-steps{margin:24px 0}.production-section{display:grid;gap:14px;margin-top:16px;padding:18px;border:1px solid #e0e8e7;border-radius:14px}.production-section>header{display:flex;align-items:center;justify-content:space-between}.production-section>header>div{display:flex;align-items:center;gap:9px}.production-section>header span{display:grid;place-items:center;width:30px;height:30px;border-radius:9px;color:#17695e;background:#e9f5f2;font-size:11px;font-weight:800}.production-section h3{margin:0;color:#294c4f}.production-section>header small{color:#718385}.result-entry{display:grid;grid-template-columns:minmax(220px,1fr) minmax(120px,.5fr) auto;gap:8px}.result-list{display:grid;gap:7px}.result-list article,.asset-result,.text-result{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 13px;border-radius:10px;background:#f7f9f9}.result-list article>span,.asset-result>span{display:grid}.result-list small,.asset-result small{color:#7a898b;font-size:11px}.result-list b{margin-left:auto;color:#1d5752}.text-entry{display:grid;grid-template-columns:150px minmax(180px,.6fr) minmax(260px,1fr) auto;gap:8px;align-items:start}.text-result{align-items:flex-start}.text-result p{margin:5px 0 0;color:#627577;line-height:1.6}.asset-entry{display:grid;grid-template-columns:160px minmax(220px,1fr) auto;align-items:center;gap:8px}.production-final{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:18px;padding:20px;border-radius:14px;background:#edf7f4}.production-final h3,.production-final p{margin:0}.production-final p{margin-top:6px;color:#617577;line-height:1.6}@media(max-width:1050px){.archive-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.appointment-card{grid-template-columns:64px minmax(0,1fr)}.appointment-actions{grid-column:2;flex-direction:row}.text-entry{grid-template-columns:1fr 1fr}.text-entry :deep(.el-textarea),.text-entry :deep(.el-button){grid-column:1/-1}}@media(max-width:680px){.report-hero{align-items:flex-start;flex-direction:column;padding:20px}.capacity-pill{width:100%}.report-toolbar{align-items:flex-start;flex-direction:column}.report-toolbar :deep(.el-select){width:100%}.appointment-card{grid-template-columns:1fr}.appointment-date{display:flex;justify-content:flex-start;gap:8px;border-right:0;border-bottom:1px solid #edf1f0}.appointment-date strong{font-size:20px}.appointment-actions{grid-column:1;flex-direction:column}.archive-grid{grid-template-columns:1fr}.result-entry,.text-entry,.asset-entry{grid-template-columns:1fr}.text-entry :deep(.el-textarea),.text-entry :deep(.el-button){grid-column:auto}.production-final{align-items:flex-start;flex-direction:column}.production-final :deep(.el-button){width:100%}}
.asset-actions{display:flex;align-items:center;gap:4px}.asset-actions :deep(.el-button){margin:0}@media(max-width:680px){.asset-result{align-items:flex-start;flex-direction:column}}
:global(html[data-theme="dark"]) .report-hero,
:global(html[data-theme="dark"]) .capacity-pill,
:global(html[data-theme="dark"]) .report-tabs,
:global(html[data-theme="dark"]) .appointment-card,
:global(html[data-theme="dark"]) .archive-card,
:global(html[data-theme="dark"]) .report-subject,
:global(html[data-theme="dark"]) .production-section,
:global(html[data-theme="dark"]) .result-list article,
:global(html[data-theme="dark"]) .asset-result,
:global(html[data-theme="dark"]) .text-result{border-color:var(--color-border);color:var(--color-text);background:var(--color-surface)}
:global(html[data-theme="dark"]) .report-hero h2,
:global(html[data-theme="dark"]) .appointment-title h3,
:global(html[data-theme="dark"]) .archive-card h3,
:global(html[data-theme="dark"]) .production-section h3{color:var(--color-text)}
:global(html[data-theme="dark"]) .production-final{background:var(--color-accent-soft)}
</style>
