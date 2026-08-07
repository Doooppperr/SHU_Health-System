<template>
  <div class="workspace-page user-platform-page institution-directory">
    <section class="user-page-lead">
      <div><span class="user-kicker">体检服务网络</span><h2>先选机构，再选方便到达的分院</h2><p>同一机构的分院共享已归档体检资料，但预约时间、名额和套餐仍由各分院独立安排。</p></div>
      <el-button v-if="!publicMode" type="primary" plain @click="router.push({name:'appointments'})">查看我的预约</el-button>
      <el-button v-else type="primary" plain @click="router.push({name:'login',query:{redirect:'/appointments'}})">登录后预约</el-button>
    </section>
    <section class="institution-search-panel" aria-label="搜索体检机构">
      <SmartInstitutionSearch
        v-model="query"
        :search="searchMeta"
        :loading="loading || interpreting"
        aria-label="搜索体检机构"
        @search="queueSearch"
        @select="selectSuggestion"
      />
      <small v-if="!loading && query.trim()">找到 {{ displayOrganizations.length }} 家机构主体、{{ matchedBranchCount }} 家分院</small>
      <small v-else>支持机构、分院、套餐、人群、健康方向与交通信息智能推荐</small>
    </section>
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon/>
    <section v-loading="loading" class="organization-list">
      <article
        v-for="organization in displayOrganizations"
        :key="organization.id"
        class="organization-card"
        :class="{'is-recommended':selectedSuggestion?.organization_id===organization.id}"
      >
        <header><div><span>机构主体</span><h3>{{organization.name}}</h3><p>{{organization.description}}</p></div><el-tag type="success">{{organization.branches.length}} 家分院</el-tag></header>
        <div v-if="organization.match_reasons?.length" class="match-reason-row"><b>推荐原因</b><span v-for="reason in organization.match_reasons.slice(0,3)" :key="reason">{{reason}}</span></div>
        <div class="organization-features"><span v-for="feature in organization.service_features" :key="feature">{{feature}}</span></div>
        <div class="branch-grid">
          <button
            v-for="branch in organization.branches"
            :key="branch.id"
            type="button"
            class="branch-card"
            :class="{'is-recommended':selectedSuggestion?.institution_id===branch.id}"
            @click="goDetail(branch.id)"
          >
            <InstitutionCoverImage :institution="branch"/>
            <span><strong>{{branch.branch_name}}</strong><small>{{branch.district}} · {{branch.address}}</small><small>{{branch.package_count}} 个可预约套餐</small></span>
            <span v-if="branch.matched_packages?.length" class="matched-package-list"><em v-for="pkg in branch.matched_packages.slice(0,2)" :key="pkg.id">{{pkg.name}} · {{pkg.reason}}</em></span>
            <b>查看分院 ›</b>
          </button>
        </div>
      </article>
      <el-empty v-if="!loading&&!displayOrganizations.length" :description="query.trim() ? '没有找到匹配的体检机构' : '暂无可预约机构'"/>
    </section>
  </div>
</template>

<script setup>
import {computed,onBeforeUnmount,onMounted,ref} from "vue";
import {useRouter} from "vue-router";
import InstitutionCoverImage from "../components/InstitutionCoverImage.vue";
import SmartInstitutionSearch from "../components/SmartInstitutionSearch.vue";
import {fetchOrganizations} from "../api/institutions";
import {fetchPublicOrganizations} from "../api/public";

const props=defineProps({publicMode:{type:Boolean,default:false}});
const publicMode=computed(()=>props.publicMode);
const router=useRouter();
const loading=ref(false),interpreting=ref(false),organizations=ref([]),errorMessage=ref(""),query=ref("");
const searchMeta=ref({mode:"content",query:"",intent_summary:"",needs_ai:false,suggestions:[]});
const selectedSuggestion=ref(null);
const displayOrganizations=computed(()=>{
  const target=selectedSuggestion.value;
  if(!target)return organizations.value;
  return organizations.value.filter(item=>item.id===target.organization_id).map(item=>({
    ...item,
    branches:target.institution_id?item.branches.filter(branch=>branch.id===target.institution_id):item.branches,
  })).filter(item=>item.branches.length);
});
const matchedBranchCount=computed(()=>displayOrganizations.value.reduce((total,item)=>total+(item.branches?.length||0),0));
let searchTimer;
let requestRevision=0;

async function load(mode="content",revision=++requestRevision){
  if(mode==="content")loading.value=true;else interpreting.value=true;
  errorMessage.value="";
  try{
    const params={q:query.value.trim(),search_mode:mode};
    const response=await (publicMode.value?fetchPublicOrganizations(params):fetchOrganizations(params));
    if(revision!==requestRevision)return;
    organizations.value=response.data.items||[];
    searchMeta.value=response.data.search||searchMeta.value;
    if(mode==="content"&&searchMeta.value.needs_ai&&query.value.trim())await load("hybrid",revision);
  }catch(error){
    if(revision===requestRevision)errorMessage.value=error?.response?.data?.message||"机构列表加载失败";
  }finally{
    if(revision===requestRevision){if(mode==="content")loading.value=false;else interpreting.value=false;}
  }
}
function queueSearch(){
  selectedSuggestion.value=null;
  requestRevision+=1;
  window.clearTimeout(searchTimer);
  searchTimer=window.setTimeout(()=>load("content"),300);
}
function selectSuggestion(item){selectedSuggestion.value=item;}
function goDetail(id){router.push({name:publicMode.value?"public-institution-detail":"institution-detail",params:{id}});}
onMounted(()=>load("content"));
onBeforeUnmount(()=>window.clearTimeout(searchTimer));
</script>

<style scoped>
.institution-search-panel{display:flex;align-items:center;gap:14px;padding:16px 18px;margin-bottom:18px;border:1px solid var(--color-border);border-radius:16px;background:var(--color-surface)}.institution-search-panel .smart-institution-search{max-width:720px}.institution-search-panel small{color:var(--color-muted);white-space:nowrap}.organization-list{display:grid;gap:20px}.organization-card{padding:24px;border:1px solid var(--color-border);border-radius:18px;background:var(--color-surface)}.organization-card.is-recommended{border-color:color-mix(in srgb,var(--workspace-accent) 60%,var(--color-border));box-shadow:0 0 0 2px color-mix(in srgb,var(--workspace-accent) 12%,transparent)}.organization-card>header{display:flex;justify-content:space-between;gap:20px}.organization-card h3{margin:4px 0 8px;font-size:24px}.organization-card p{margin:0;color:var(--color-muted);line-height:1.7}.organization-card header span{color:var(--workspace-accent);font-size:12px;font-weight:800}.match-reason-row,.organization-features{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:16px 0}.match-reason-row b{color:var(--workspace-accent);font-size:12px}.match-reason-row span,.organization-features span{padding:6px 10px;border-radius:999px;background:var(--color-soft);color:var(--workspace-accent);font-size:12px}.branch-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.branch-card{display:grid;gap:10px;padding:0 0 14px;overflow:hidden;border:1px solid var(--color-border);border-radius:14px;background:transparent;color:inherit;text-align:left;cursor:pointer}.branch-card.is-recommended{border-color:var(--workspace-accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--workspace-accent) 12%,transparent)}.branch-card>span,.branch-card>b{padding:0 14px}.branch-card strong,.branch-card small{display:block}.branch-card small{margin-top:5px;color:var(--color-muted)}.branch-card b{color:var(--workspace-accent)}.matched-package-list{display:grid;gap:5px}.matched-package-list em{padding:6px 8px;border-radius:8px;background:var(--color-soft);color:var(--workspace-accent);font-size:11px;font-style:normal;line-height:1.4}@media(max-width:1000px){.branch-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:640px){.institution-search-panel{align-items:stretch;flex-direction:column}.institution-search-panel small{white-space:normal}.branch-grid{grid-template-columns:1fr}.organization-card{padding:18px}}
</style>
