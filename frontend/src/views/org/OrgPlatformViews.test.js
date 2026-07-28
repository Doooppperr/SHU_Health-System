import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const push=vi.fn(),replace=vi.fn();
vi.mock("vue-router",()=>({useRouter:()=>({push,replace}),useRoute:()=>({query:{},fullPath:"/org/reports"})}));
const orgApi=vi.hoisted(()=>({
  fetchOrgPackages:vi.fn(),createOrgPackage:vi.fn(),updateOrgPackage:vi.fn(),deactivateOrgPackage:vi.fn(),reactivateOrgPackage:vi.fn(),
  fetchOrgReports:vi.fn(),createOrgReport:vi.fn(),fetchOrgReport:vi.fn(),addOrgReportIndicator:vi.fn(),deleteOrgReportIndicator:vi.fn(),addOrgTextResult:vi.fn(),deleteOrgTextResult:vi.fn(),uploadOrgHealthAsset:vi.fn(),deleteOrgHealthAsset:vi.fn(),lockOrgReport:vi.fn(),submitOrgReport:vi.fn(),uploadOrgReportOcr:vi.fn(),
  fetchOrgReportAssetTypes:vi.fn(),fetchOrgReportAssetContent:vi.fn(),updateOrgHealthAsset:vi.fn(),
  fetchOrgAppointments:vi.fn(),attendOrgAppointment:vi.fn(),closeOrgAppointment:vi.fn(),fetchOrgAppointmentCapacity:vi.fn(),updateOrgAppointmentCapacity:vi.fn(),
}));
const healthApi=vi.hoisted(()=>({fetchHealthDomains:vi.fn()}));
const indicatorApi=vi.hoisted(()=>({fetchIndicatorDicts:vi.fn()}));
const dashboardApi=vi.hoisted(()=>({fetchOrgDashboard:vi.fn()}));
vi.mock("../../api/org",()=>orgApi);vi.mock("../../api/health",()=>healthApi);vi.mock("../../api/indicators",()=>indicatorApi);vi.mock("../../api/dashboards",()=>dashboardApi);

import OrgDashboardView from "./OrgDashboardView.vue";
import OrgPackagesView from "./OrgPackagesView.vue";
import OrgReportsView from "./OrgReportsView.vue";

const wrappers=[];
const mountView=(component)=>{const wrapper=mount(component,{attachTo:document.body,global:{plugins:[ElementPlus],stubs:{teleport:true}}});wrappers.push(wrapper);return wrapper;};

beforeEach(()=>{
  vi.clearAllMocks();
  healthApi.fetchHealthDomains.mockResolvedValue({data:{items:[{id:1,name:"心脑血管"},{id:2,name:"代谢"}]}});
  indicatorApi.fetchIndicatorDicts.mockResolvedValue({data:{items:[]}});
  orgApi.fetchOrgAppointmentCapacity.mockResolvedValue({data:{unlimited:false,daily_appointment_limit:20}});
  orgApi.fetchOrgReports.mockResolvedValue({data:{items:[],total:0,filtered_total:0}});
  orgApi.fetchOrgAppointments.mockResolvedValue({data:{items:[]}});
  orgApi.fetchOrgReportAssetTypes.mockResolvedValue({data:{items:[]}});
});
afterEach(()=>{wrappers.splice(0).forEach((wrapper)=>wrapper.unmount());document.body.innerHTML="";});

describe("institution platform views",()=>{
  it("turns dashboard counts into actionable daily work",async()=>{
    dashboardApi.fetchOrgDashboard.mockResolvedValue({data:{summary:{institution:{name:"澄心健康管理中心"},today:{booked:8,capacity:20,remaining:12,awaiting_arrival:3,awaiting_archive:2,waitlist_subscriptions:1},active_package_count:3,pending_package_review_count:1,recent_package_reviews:[]}}});
    const wrapper=mountView(OrgDashboardView);await flushPromises();
    expect(wrapper.text()).toContain("今日运营工作台");expect(wrapper.text()).toContain("等待到检");expect(wrapper.text()).toContain("完成待归档数据");
    await wrapper.get(".org-task").trigger("click");expect(push).toHaveBeenCalledWith({name:"org-reports",query:{view:"today"}});
  });

  it("renders user-facing service cards and a three-step design wizard",async()=>{
    orgApi.fetchOrgPackages.mockResolvedValue({data:{items:[{id:1,name:"慢病风险综合评估",focus_area:"长期风险管理",gender_scope:"all",price:1299,is_active:true,package_type:"combined",audience:"关注慢病风险的成年人",description:"提供综合健康评估",booking_notice:"检查前请空腹",domains:[{id:1,name:"心脑血管"},{id:2,name:"代谢"}],pending_request:null}]}});
    const wrapper=mountView(OrgPackagesView);await flushPromises();
    const card=wrapper.get(".package-card");expect(card.text()).toContain("组合服务");expect(card.text()).toContain("关注慢病风险的成年人");expect(card.text()).not.toContain("female_all");
    await wrapper.get(".package-hero .el-button").trigger("click");await flushPromises();expect(wrapper.vm.dialogVisible).toBe(true);expect(wrapper.vm.step).toBe(0);
  });

  it("organizes appointments by real service tasks and removes OCR jargon",async()=>{
    orgApi.fetchOrgAppointments.mockResolvedValue({data:{items:[{id:8,appointment_date:new Date().toISOString().slice(0,10),status:"awaiting_report",package_name:"都市年度基础体检",user:{name:"张明",health_id:"HD-001"},report_id:null,report_status:null}]}});
    const wrapper=mountView(OrgReportsView);await flushPromises();
    expect(wrapper.text()).toContain("今日接待");expect(wrapper.text()).toContain("待归档");expect(wrapper.text()).toContain("本院归档");expect(wrapper.text()).toContain("机构共享档案");
    const archiveTab=wrapper.findAll(".report-tabs button").find((item)=>item.text().includes("待归档"));await archiveTab.trigger("click");await flushPromises();
    expect(wrapper.text()).toContain("都市年度基础体检");expect(wrapper.text()).toContain("导入体检报告并识别");expect(wrapper.text()).not.toContain("OCR 上传");
  });

  it("filters both archive tabs and shows a readable exam date",async()=>{
    orgApi.fetchOrgReports.mockImplementation((params)=>Promise.resolve({data:{
      pagination:{page:1,page_size:15,total:params.scope==="branch"?12:8,pages:1},
      items:[{
        id:3,subject_name_snapshot:"林国安",subject_display_name:"林国安",
        exam_date:"2026-07-23T00:00:00",indicator_count:6,
        access_mode:params.scope==="organization"?"cross_branch_read_only":"branch",
        source_branch:{name:"澄心健康管理中心",branch_name:"徐汇综合院区"},
      }],
    }}));
    const wrapper=mountView(OrgReportsView);await flushPromises();
    const historyTab=wrapper.findAll(".report-tabs button").find((item)=>item.text().includes("本院归档"));
    await historyTab.trigger("click");await flushPromises();
    expect(wrapper.text()).toContain("体检日期：2026年7月23日");
    expect(wrapper.text()).toContain("受检者：林国安");
    expect(wrapper.text()).toContain("共 12 份，当前显示 1 份");
    const subject=wrapper.find(".archive-toolbar .el-input__inner");
    await subject.setValue("test3");
    const filterButton=wrapper.findAll(".archive-toolbar .el-button").find((item)=>item.text().includes("筛选档案"));
    await filterButton.trigger("click");await flushPromises();
    expect(orgApi.fetchOrgReports).toHaveBeenCalledWith(expect.objectContaining({scope:"branch",subject:"test3"}));
    const sharedTab=wrapper.findAll(".report-tabs button").find((item)=>item.text().includes("机构共享档案"));
    await sharedTab.trigger("click");await flushPromises();
    expect(orgApi.fetchOrgReports).toHaveBeenCalledWith(expect.objectContaining({scope:"organization",access:"cross_branch",subject:"test3"}));
  });

  it("shows conclusion coverage and prevents locking while a report domain is incomplete",async()=>{
    orgApi.fetchOrgAppointments.mockResolvedValue({data:{items:[{
      id:8,appointment_date:"2026-07-28",status:"awaiting_report",package_name:"年度综合体检",
      user:{name:"林晓晨",health_id:"HID-8K3M2Q7A"},report_id:11,report_status:"draft",
    }]}});
    orgApi.fetchOrgReport.mockResolvedValue({data:{item:{
      id:11,status:"draft",
      package_version:{domains:[{id:1,name:"心脑血管"},{id:2,name:"代谢"}]},
      indicators:[{id:1,display_domain_id:1,value:"4.2"}],
      assets:[{id:2,domain_id:2,title:"检查附件"}],
      text_results:[{id:3,domain_id:1,title:"心血管检查结论",body:"本次检查结果总体平稳。"}],
    }}});
    const wrapper=mountView(OrgReportsView);await flushPromises();
    const archiveTab=wrapper.findAll(".report-tabs button").find((item)=>item.text().includes("待归档"));
    await archiveTab.trigger("click");await flushPromises();
    const detailButton=wrapper.findAll(".appointment-actions .el-button").find((item)=>item.text().includes("继续完善结果"));
    await detailButton.trigger("click");await flushPromises();
    expect(orgApi.fetchOrgReport).toHaveBeenCalledWith(11);
    expect(wrapper.vm.detailVisible).toBe(true);
    expect(wrapper.vm.conclusionDomainIds.has(1)).toBe(true);
    expect(wrapper.vm.missingConclusionDomains.map((domain)=>domain.name)).toEqual(["代谢"]);
    await wrapper.vm.lockReport(11);
    expect(orgApi.lockOrgReport).not.toHaveBeenCalled();
  });
});
