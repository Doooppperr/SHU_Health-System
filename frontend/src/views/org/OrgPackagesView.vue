<template>
  <div class="workspace-page package-workspace">
    <section class="package-hero">
      <div>
        <p class="package-kicker">服务设计中心</p>
        <h2>体检服务</h2>
        <p>用健康领域、适用人群和检查前须知描述服务。所有变更经平台审核后对用户生效。</p>
      </div>
      <el-button type="primary" @click="openCreate">设计新服务</el-button>
    </section>

    <section class="package-summary" aria-label="服务概况">
      <article><strong>{{ packageSummary.total }}</strong><span>全部服务</span></article>
      <article><strong>{{ activeCount }}</strong><span>预约中</span></article>
      <article><strong>{{ pendingCount }}</strong><span>等待平台审核</span></article>
    </section>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
    <section class="package-grid" v-loading="loading">
      <article v-for="item in items" :key="item.id" class="package-card" :class="{ 'is-inactive': !item.is_active }">
        <header>
          <div>
            <div class="package-badges">
              <span>{{ packageTypeLabel(item.package_type) }}</span>
              <span v-if="item.version?.version_number" class="package-version">当前第 {{ item.version.version_number }} 版</span>
              <el-tag :type="item.is_active ? 'success' : 'info'" size="small">{{ item.is_active ? "预约中" : "已下架" }}</el-tag>
              <el-tag v-if="item.pending_request" type="warning" size="small">变更审核中</el-tag>
            </div>
            <h3>{{ item.name }}</h3>
            <p>{{ item.focus_area || "综合健康评估" }}</p>
          </div>
          <strong class="package-price"><small>¥</small>{{ Number(item.price || 0).toFixed(0) }}</strong>
        </header>

        <div class="package-domains">
          <span v-for="domain in item.domains || []" :key="domain.id">{{ domain.name }}</span>
          <span v-if="!(item.domains || []).length" class="is-muted">等待配置健康领域</span>
        </div>
        <dl>
          <div><dt>适用人群</dt><dd>{{ item.audience || genderLabel(item.gender_scope) }}</dd></div>
          <div><dt>服务说明</dt><dd>{{ item.description || "以机构实际完成的检查与结论为准。" }}</dd></div>
          <div><dt>检查前须知</dt><dd>{{ item.booking_notice || "预约成功后请按机构通知做好检查准备。" }}</dd></div>
        </dl>
        <footer>
          <el-button type="primary" plain :disabled="!!item.pending_request" @click="openEdit(item)">编辑服务</el-button>
          <el-button v-if="item.is_active" type="danger" link :disabled="!!item.pending_request" @click="deactivate(item)">申请下架</el-button>
          <el-button v-else type="success" link :disabled="!!item.pending_request" @click="restore(item)">申请恢复</el-button>
        </footer>
      </article>
      <el-empty v-if="!loading && !items.length" description="还没有体检服务，先设计第一项服务吧" />
    </section>
    <el-pagination v-if="pagination.total>pagination.page_size" v-model:current-page="pagination.page" :page-size="pagination.page_size" :total="pagination.total" layout="total, prev, pager, next" @current-change="load"/>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑体检服务' : '设计体检服务'" width="min(760px, 94vw)" :close-on-click-modal="false" @closed="step=0">
      <el-steps :active="step" finish-status="success" simple class="package-steps">
        <el-step title="基本信息" />
        <el-step title="健康领域" />
        <el-step title="须知与预览" />
      </el-steps>

      <el-form :model="form" label-position="top" class="package-form">
        <section v-show="step===0">
          <el-form-item label="服务名称" required><el-input v-model="form.name" maxlength="120" placeholder="让用户一眼看懂这项服务" /></el-form-item>
          <div class="package-form-grid">
            <el-form-item label="主要关注方向" required><el-input v-model="form.focus_area" maxlength="120" placeholder="例如：心脑血管风险筛查" /></el-form-item>
            <el-form-item label="服务价格（元）" required><el-input-number v-model="form.price" :min="0" :precision="2" :step="10" /></el-form-item>
          </div>
          <el-form-item label="适用人群范围" required><el-select v-model="form.gender_scope"><el-option v-for="option in genderOptions" :key="option.value" :label="option.label" :value="option.value" /></el-select></el-form-item>
          <el-form-item label="适用人群说明"><el-input v-model="form.audience" maxlength="120" placeholder="例如：关注血压、血脂变化的职场人群" /></el-form-item>
        </section>

        <section v-show="step===1">
          <el-form-item label="服务类型" required>
            <el-radio-group v-model="form.package_type" @change="form.domain_ids=[]">
              <el-radio-button value="special">专项服务</el-radio-button>
              <el-radio-button value="combined">组合服务</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <p class="form-guidance">{{ form.package_type==='special' ? '专项服务聚焦一个健康领域。' : '组合服务至少覆盖两个健康领域。' }}</p>
          <el-form-item label="覆盖的健康领域" required>
            <el-select v-model="form.domain_ids" multiple :multiple-limit="form.package_type==='special'?1:8" placeholder="请选择本服务关注的健康方向">
              <el-option v-for="domain in domains" :key="domain.id" :label="domain.name" :value="domain.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="报告锁定前必须上传的附件槽位">
            <el-select v-model="form.required_asset_type_ids" multiple filterable placeholder="可选；未选择则不强制附件">
              <el-option v-for="type in availableAssetTypes" :key="type.id" :label="type.name" :value="type.id"/>
            </el-select>
          </el-form-item>
          <el-alert title="健康领域用于约束本次体检可归档的结果范围，不代表套餐包含固定指标清单。" type="info" show-icon :closable="false" />
        </section>

        <section v-show="step===2">
          <el-form-item label="用户可见的服务说明"><el-input v-model="form.description" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="说明服务特点和用户能够获得的内容" /></el-form-item>
          <el-form-item label="预约及检查前须知"><el-input v-model="form.booking_notice" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="例如空腹要求、证件和到院时间" /></el-form-item>
          <article class="package-preview">
            <span>{{ packageTypeLabel(form.package_type) }}</span>
            <h3>{{ form.name || "服务名称预览" }}</h3>
            <p>{{ selectedDomainNames || "请选择健康领域" }}</p>
            <strong>¥ {{ Number(form.price || 0).toFixed(0) }}</strong>
            <small>{{ form.audience || genderLabel(form.gender_scope) }} · {{ form.booking_notice || "预约后请查看检查前须知" }}</small>
          </article>
        </section>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible=false">取消</el-button>
        <el-button v-if="step>0" @click="step--">上一步</el-button>
        <el-button v-if="step<2" type="primary" @click="nextStep">下一步</el-button>
        <el-button v-else type="primary" :loading="saving" @click="save">提交平台审核</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import { createOrgPackage, deactivateOrgPackage, fetchOrgPackages, fetchOrgReportAssetTypes, reactivateOrgPackage, updateOrgPackage } from "../../api/org";
import { fetchHealthDomains } from "../../api/health";

const genderOptions = [{ value: "all", label: "不限人群" }, { value: "male", label: "男性" }, { value: "female", label: "女性" }, { value: "female_all", label: "女性全龄" }];
const genderLabel = (value) => genderOptions.find((item) => item.value === value)?.label || "不限人群";
const packageTypeLabel = (value) => value === "combined" ? "组合服务" : "专项服务";
const items = ref([]);
const domains = ref([]);
const assetTypes = ref([]);
const loading = ref(false);
const saving = ref(false);
const errorMessage = ref("");
const dialogVisible = ref(false);
const step = ref(0);
const pagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });
const packageSummary = reactive({ total: 0, active: 0, pending: 0 });
const form = reactive({ id: null, name: "", focus_area: "", gender_scope: "all", price: 0, description: "", package_type: "special", domain_ids: [], required_asset_type_ids: [], audience: "", booking_notice: "" });
const activeCount = computed(() => packageSummary.active);
const pendingCount = computed(() => packageSummary.pending);
const selectedDomainNames = computed(() => domains.value.filter((item) => form.domain_ids.includes(item.id)).map((item) => item.name).join("、"));
const availableAssetTypes = computed(() => assetTypes.value.filter((item) => form.domain_ids.includes(item.domain_id)));

function reset() { Object.assign(form, { id: null, name: "", focus_area: "", gender_scope: "all", price: 0, description: "", package_type: "special", domain_ids: [], required_asset_type_ids: [], audience: "", booking_notice: "体检前一天保持清淡饮食，具体要求以预约成功后的通知为准。" }); step.value = 0; }
function openCreate() { reset(); dialogVisible.value = true; }
function openEdit(item) { Object.assign(form, { id: item.id, name: item.name || "", focus_area: item.focus_area || "", gender_scope: item.gender_scope || "all", price: Number(item.price || 0), description: item.description || "", package_type: item.package_type || "special", domain_ids: (item.domains || []).map((x) => x.id), required_asset_type_ids: (item.version?.asset_requirements||[]).filter((x)=>x.is_required).map((x)=>x.asset_type_id), audience: item.audience || "", booking_notice: item.booking_notice || "" }); step.value = 0; dialogVisible.value = true; }
function validateBasic() { if (!form.name.trim() || !form.focus_area.trim()) { ElMessage.error("请填写服务名称和主要关注方向"); return false; } if (Number(form.price) < 0) { ElMessage.error("请填写正确的服务价格"); return false; } return true; }
function validateDomains() { const valid = form.package_type === "special" ? form.domain_ids.length === 1 : form.domain_ids.length >= 2; if (!valid) ElMessage.error(form.package_type === "special" ? "专项服务需要选择一个健康领域" : "组合服务至少选择两个健康领域"); return valid; }
function nextStep() { if (step.value === 0 && !validateBasic()) return; if (step.value === 1 && !validateDomains()) return; step.value += 1; }
async function load() { loading.value = true; errorMessage.value = ""; try { const { data } = await fetchOrgPackages({ page: pagination.page, page_size: 15 }); items.value = data.items || []; Object.assign(pagination, data.pagination || {}); Object.assign(packageSummary, data.summary || { total: items.value.length, active: items.value.filter((item)=>item.is_active).length, pending: items.value.filter((item)=>item.pending_request).length }); } catch (error) { errorMessage.value = error?.response?.data?.message || "体检服务加载失败"; } finally { loading.value = false; } }
async function save() { if (!validateBasic() || !validateDomains()) return; saving.value = true; const payload = { name: form.name.trim(), focus_area: form.focus_area.trim(), gender_scope: form.gender_scope, price: Number(form.price), description: form.description.trim() || null, package_type: form.package_type, domain_ids: form.domain_ids, required_asset_type_ids: form.required_asset_type_ids.filter((id)=>availableAssetTypes.value.some((item)=>item.id===id)), audience: form.audience.trim() || null, booking_notice: form.booking_notice.trim() || null }; try { if (form.id) await updateOrgPackage(form.id, payload); else await createOrgPackage(payload); ElMessage.success("服务变更已提交平台审核"); dialogVisible.value = false; await load(); } catch (error) { ElMessage.error(error?.response?.data?.message || "提交失败"); } finally { saving.value = false; } }
async function deactivate(item) { try { await ElMessageBox.confirm("审核通过后，该服务将停止接受新预约，历史预约不受影响。", "申请下架服务", { type: "warning", confirmButtonText: "提交申请", cancelButtonText: "取消" }); await deactivateOrgPackage(item.id); ElMessage.success("下架申请已提交"); await load(); } catch (error) { if (error !== "cancel" && error !== "close") ElMessage.error(error?.response?.data?.message || "申请失败"); } }
async function restore(item) { try { await reactivateOrgPackage(item.id); ElMessage.success("恢复预约申请已提交"); await load(); } catch (error) { ElMessage.error(error?.response?.data?.message || "申请失败"); } }
onMounted(async () => { try { const [domainResponse,typeResponse]=await Promise.all([fetchHealthDomains(),fetchOrgReportAssetTypes()]);domains.value=domainResponse.data.items||[];assetTypes.value=typeResponse.data.items||[]; } catch { ElMessage.error("健康领域或附件槽位加载失败"); } await load(); });
</script>

<style scoped>
.package-workspace{display:grid;gap:18px}.package-hero{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:26px;border:1px solid #dbe9e6;border-radius:18px;background:linear-gradient(135deg,#f0faf7,#fff)}.package-kicker{margin:0;color:var(--workspace-accent);font-size:12px;font-weight:800;letter-spacing:.08em}.package-hero h2{margin:5px 0 8px;color:#173f42;font-size:30px}.package-hero p:last-child{max-width:720px;margin:0;color:#647a7d;line-height:1.7}.package-summary{display:flex;gap:12px}.package-summary article{display:flex;align-items:baseline;gap:8px;min-width:150px;padding:14px 18px;border:1px solid #e1e9e8;border-radius:13px;background:#fff}.package-summary strong{color:#1a4b4d;font-size:25px}.package-summary span{color:#6d8082;font-size:12px}.package-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;min-height:140px}.package-card{display:grid;gap:16px;padding:22px;border:1px solid #dfe9e7;border-radius:16px;background:#fff;box-shadow:0 8px 24px rgba(34,75,70,.04)}.package-card.is-inactive{opacity:.78}.package-card header{display:flex;justify-content:space-between;gap:16px}.package-card h3{margin:7px 0 4px;color:#173f42;font-size:20px}.package-card header p{margin:0;color:#6d8082}.package-badges{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.package-badges>span:first-child,.package-domains span,.package-preview>span{padding:4px 8px;border-radius:999px;color:#17695e;background:#e9f6f2;font-size:11px;font-weight:750}.package-price{color:#12685e;font-size:27px;white-space:nowrap}.package-price small{font-size:13px}.package-domains{display:flex;gap:7px;flex-wrap:wrap}.package-domains .is-muted{color:#718183;background:#f2f4f4}.package-card dl{display:grid;gap:10px;margin:0}.package-card dl>div{display:grid;grid-template-columns:76px minmax(0,1fr);gap:10px}.package-card dt{color:#829092;font-size:12px}.package-card dd{margin:0;color:#40595c;font-size:13px;line-height:1.6}.package-card footer{display:flex;align-items:center;gap:4px;padding-top:4px;border-top:1px solid #edf1f0}.package-steps{margin-bottom:22px}.package-form section{min-height:310px}.package-form-grid{display:grid;grid-template-columns:minmax(0,1fr) 190px;gap:14px}.package-form :deep(.el-select),.package-form :deep(.el-input-number){width:100%}.form-guidance{margin:-6px 0 16px;color:#708385;font-size:13px}.package-preview{display:grid;gap:7px;padding:18px;border:1px solid #cfe4df;border-radius:14px;background:#f4fbf9}.package-preview h3,.package-preview p{margin:0}.package-preview strong{color:#12685e;font-size:22px}.package-preview small{color:#65797b}.package-preview>span{width:max-content}@media(max-width:900px){.package-grid{grid-template-columns:1fr}}@media(max-width:650px){.package-hero{align-items:flex-start;flex-direction:column;padding:20px}.package-summary{display:grid;grid-template-columns:repeat(3,1fr)}.package-summary article{display:grid;min-width:0;padding:12px}.package-form-grid{grid-template-columns:1fr}.package-card header{align-items:flex-start}.package-card dl>div{grid-template-columns:1fr;gap:3px}}
.package-badges .package-version{padding:4px 8px;border-radius:999px;color:#5c7375;background:#f1f4f4;font-size:11px;font-weight:700}
:global(html[data-theme="dark"]) .package-hero,
:global(html[data-theme="dark"]) .package-summary article,
:global(html[data-theme="dark"]) .package-card,
:global(html[data-theme="dark"]) .package-preview{border-color:var(--color-border);color:var(--color-text);background:var(--color-surface)}
:global(html[data-theme="dark"]) .package-hero h2,
:global(html[data-theme="dark"]) .package-card h3{color:var(--color-text)}
</style>
