import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HealthDataView from "./HealthDataView.vue";
import HealthDataDetailView from "./HealthDataDetailView.vue";
import HealthTimelineView from "./HealthTimelineView.vue";
import TrendView from "./TrendView.vue";
import { useAuthStore } from "../stores/auth";

const mocks = vi.hoisted(() => ({
  route: { query: {}, params: {} },
  router: { push: vi.fn(), replace: vi.fn().mockResolvedValue() },
  fetchFriends: vi.fn(),
  fetchHealthData: vi.fn(),
  fetchHealthDataDetail: vi.fn(),
  fetchHealthAssetContent: vi.fn(),
  fetchHealthDomains: vi.fn(),
  fetchHealthTrends: vi.fn(),
  fetchTimeline: vi.fn(),
  fetchInstitutions: vi.fn(),
  streamAiTrendAnalysis: vi.fn(),
}));

vi.mock("vue-router", () => ({ useRoute: () => mocks.route, useRouter: () => mocks.router }));
vi.mock("../api/friends", () => ({ fetchFriends: mocks.fetchFriends }));
vi.mock("../api/health", () => ({
  fetchHealthData: mocks.fetchHealthData,
  fetchHealthDataDetail: mocks.fetchHealthDataDetail,
  fetchHealthAssetContent: mocks.fetchHealthAssetContent,
  fetchHealthDomains: mocks.fetchHealthDomains,
  fetchHealthTrends: mocks.fetchHealthTrends,
  fetchTimeline: mocks.fetchTimeline,
}));
vi.mock("../api/institutions", () => ({ fetchInstitutions: mocks.fetchInstitutions }));
vi.mock("../api/ai", () => ({ streamAiTrendAnalysis: mocks.streamAiTrendAnalysis }));

const friendPayload = {
  outgoing: [{ auth_status: true, relation_name: "父亲", friend_user: { id: 12, display_name: "亲友姓名" } }],
  incoming: [],
};
const domainPayload = { items: [{ id: 1, code: "basic", name: "基础体征与体格" }] };
const wrappers = [];

function mountView(component, query = {}) {
  mocks.route.query = query;
  mocks.route.params = component === HealthDataDetailView ? { id: "hd-i-42" } : {};
  const pinia = createPinia();
  setActivePinia(pinia);
  useAuthStore(pinia).user = { id: 1, username: "本人账号", role: "user" };
  const wrapper = mount(component, {
    global: {
      plugins: [pinia, ElementPlus],
      stubs: { HealthTrendChart: { template: '<div data-testid="trend-chart" />' } },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.router.replace.mockResolvedValue();
  mocks.fetchFriends.mockResolvedValue({ data: friendPayload });
  mocks.fetchHealthDomains.mockResolvedValue({ data: domainPayload });
  mocks.fetchInstitutions.mockResolvedValue({ data: { items: [] } });
  mocks.fetchHealthData.mockResolvedValue({ data: { owner: { id: 12 }, items: [], pagination: { page: 1, page_size: 15, total: 0 } } });
  mocks.fetchHealthDataDetail.mockResolvedValue({
    data: {
      item: {
        source_type: "institution",
        business_date: "2026-07-24",
        package: { name: "年度综合体检" },
        source: { name: "澄心健康管理中心", branch_name: "徐汇综合院区" },
        review_trace: {
          upload_doctor_name: "测试上传医生（虚构）",
          uploaded_at: "2026-07-24T02:00:00+00:00",
          review_doctor_name: "测试复核医生（虚构）",
          reviewed_at: "2026-07-24T03:00:00+00:00",
          published_at: "2026-07-24T03:00:00+00:00",
        },
        sections: [
          {
            domain: { id: 1, name: "基础体征与体格" },
            indicators: [{ id: 1, value: "26.3", result_status: "high", is_abnormal: true, indicator: { name: "体重指数", unit: "kg/m²" } }],
            text_results: [{ id: 1, title: "体格检查结论", body: "体重指数偏高。" }],
            assets: [],
          },
          {
            domain: { id: 2, name: "心脑血管" },
            indicators: [{ id: 2, value: "120", result_status: "normal", is_abnormal: false, indicator: { name: "收缩压", unit: "mmHg" } }],
            text_results: [{ id: 2, title: "心血管检查结论", body: "血压平稳。" }],
            assets: [{ id: 2, title: "十二导联心电图", modality: "ecg", annotation: "窦性心律。" }],
          },
        ],
      },
    },
  });
  mocks.fetchTimeline.mockResolvedValue({ data: { owner: { id: 12 }, items: [], pagination: { page: 1, page_size: 15, total: 0 } } });
  mocks.fetchHealthTrends.mockResolvedValue({
    data: {
      owner: { id: 12 },
      series_by_indicator: [],
      source_options: [{ value: "all", label: "全部来源" }],
    },
  });
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
});

describe("当前有效账号的健康页面", () => {
  it("本月没有异常时显示近期健康状况良好", async () => {
    const wrapper = mountView(TrendView, { domain_id: "1" });
    await flushPromises();

    expect(wrapper.text()).toContain("近期健康状况良好");
    expect(wrapper.text()).not.toContain("不能替代医生诊断");
  });

  it("体检数据始终读取当前有效账号且不再显示成员选择器", async () => {
    const wrapper = mountView(HealthDataView, { owner_id: "12" });
    await flushPromises();
    expect(mocks.fetchHealthData).toHaveBeenCalledWith(expect.not.objectContaining({ owner_id: expect.anything() }));
    expect(wrapper.text()).not.toContain("查看谁的资料");
  });

  it("体检数据支持近一周、近一个月和近半年快捷范围", async () => {
    const wrapper = mountView(HealthDataView);
    await flushPromises();
    mocks.fetchHealthData.mockClear();

    wrapper.vm.datePreset = "half_year";
    await wrapper.vm.applyDatePreset();
    await flushPromises();

    expect(mocks.fetchHealthData).toHaveBeenCalledWith(expect.objectContaining({
      page: 1,
      start_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
      end_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
    }));
    expect(mocks.router.replace).toHaveBeenLastCalledWith({
      query: expect.objectContaining({ date_preset: "half_year" }),
    });
  });

  it("体检数据自定义范围允许跨年查询", async () => {
    const wrapper = mountView(HealthDataView);
    await flushPromises();
    mocks.fetchHealthData.mockClear();

    wrapper.vm.datePreset = "custom";
    wrapper.vm.dateRange = ["2024-01-01", "2026-07-31"];
    await wrapper.vm.applyCustomDateRange();
    await flushPromises();

    expect(mocks.fetchHealthData).toHaveBeenCalledWith(expect.objectContaining({
      page: 1,
      start_date: "2024-01-01",
      end_date: "2026-07-31",
    }));
  });

  it("健康时间线始终读取当前有效账号且不再显示成员选择器", async () => {
    const wrapper = mountView(HealthTimelineView, { owner_id: "12" });
    await flushPromises();
    expect(mocks.fetchTimeline).toHaveBeenCalledWith(expect.not.objectContaining({ owner_id: expect.anything() }));
    expect(wrapper.text()).not.toContain("查看谁的记录");
  });

  it("健康趋势与 AI 都基于当前有效账号且不再显示成员选择器", async () => {
    const wrapper = mountView(TrendView, { owner_id: "12", domain_id: "1" });
    await flushPromises();
    expect(mocks.fetchHealthTrends).toHaveBeenCalledWith(
      1,
      expect.not.objectContaining({ owner_id: expect.anything() }),
    );
    expect(wrapper.text()).not.toContain("查看谁的趋势");
  });

  it("健康趋势支持快捷日期和跨年自定义范围", async () => {
    const wrapper = mountView(TrendView, { domain_id: "1" });
    await flushPromises();
    mocks.fetchHealthTrends.mockClear();

    wrapper.vm.datePreset = "month";
    await wrapper.vm.applyDatePreset();
    await flushPromises();
    expect(mocks.fetchHealthTrends).toHaveBeenCalledWith(1, expect.objectContaining({
      start_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
      end_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
    }));

    wrapper.vm.datePreset = "custom";
    wrapper.vm.dateRange = ["2022-01-01", "2026-07-31"];
    await wrapper.vm.applyCustomDateRange();
    await flushPromises();
    expect(mocks.fetchHealthTrends).toHaveBeenCalledWith(1, expect.objectContaining({
      start_date: "2022-01-01",
      end_date: "2026-07-31",
    }));
    expect(mocks.router.replace).toHaveBeenLastCalledWith({
      query: expect.objectContaining({ date_preset: "custom" }),
    });
  });

  it("时间线和趋势页不再残留测量入口", async () => {
    const timeline = mountView(HealthTimelineView);
    const trend = mountView(TrendView, { domain_id: "1" });
    await flushPromises();
    expect(timeline.text()).not.toContain("记录今日测量");
    expect(timeline.text()).not.toContain("记录测量");
    expect(trend.text()).not.toContain("记录新测量");
    expect(trend.text()).not.toContain("开始记录");
  });

  it("没有有效参考范围时不显示描述性或暂无范围提示卡", async () => {
    mocks.fetchHealthTrends.mockResolvedValue({
      data: {
        series_by_indicator: [{
          indicator: { id: 1, name: "身高", unit: "cm" },
          points: [{ value: 175, measured_at: "2026-07-24" }],
          summary: { latest: 175, change: 0 },
          reference: {
            label: "描述性测量值",
            context: "该数值不单独判定正常或异常",
          },
        }],
        source_options: [{ value: "all", label: "全部来源" }],
      },
    });
    const wrapper = mountView(TrendView, { domain_id: "1" });
    await flushPromises();
    expect(wrapper.find(".trend-reference-note").exists()).toBe(false);
    expect(wrapper.find(".trend-chart-platform").classes()).not.toContain("has-reference-note");
    expect(wrapper.text()).not.toContain("描述性测量值");
    expect(wrapper.text()).not.toContain("暂无统一参考范围");
  });

  it("有范围时只展示简洁的参考范围标题和数值", async () => {
    mocks.fetchHealthTrends.mockResolvedValue({
      data: {
        series_by_indicator: [{
          indicator: { id: 2, name: "体温", unit: "°C" },
          points: [{ value: 36.4, measured_at: "2026-07-24" }],
          summary: { latest: 36.4, change: -0.2 },
          reference: {
            low: 36.1,
            high: 37.2,
            label: "机构报告参考范围",
            context: "优先采用机构报告提供的参考范围",
            source_url: "https://example.test/reference",
          },
        }],
        source_options: [{ value: "all", label: "全部来源" }],
      },
    });
    const wrapper = mountView(TrendView, { domain_id: "1" });
    await flushPromises();
    const note = wrapper.get(".trend-reference-note");
    expect(wrapper.find(".trend-chart-platform").classes()).toContain("has-reference-note");
    expect(note.text()).toContain("参考范围");
    expect(note.text()).toContain("36.1–37.2 °C");
    expect(note.text()).not.toContain("机构报告参考范围");
    expect(note.text()).not.toContain("优先采用机构报告提供的参考范围");
    expect(note.find("a").exists()).toBe(false);
  });

  it("异常提示只显示本月异常并可进入详情", async () => {
    const month = new Date().toISOString().slice(0, 7);
    mocks.fetchHealthTrends.mockResolvedValue({
      data: {
        series_by_indicator: [{
          indicator: { id: 7, name: "收缩压", unit: "mmHg" },
          points: [
            { date: `${month}-01`, value: 150, result_status: "high", health_data_id: "hd-i-20", source: { type: "institution", name: "澄心健康" } },
            { date: `${month}-02`, value: 146, result_status: "high", health_data_id: "hd-i-28", source: { type: "institution", name: "澄心健康" } },
            { date: `${month}-03`, value: 132, result_status: "normal", is_abnormal: false, health_data_id: "hd-s-1", source: { type: "self" } },
          ],
          summary: { latest: 132, change: -14 },
          reference: { low: 90, high: 139 },
        }],
        source_options: [{ value: "all", label: "全部来源" }],
      },
    });
    const wrapper = mountView(TrendView, { domain_id: "1" });
    await flushPromises();

    expect(wrapper.text()).toContain("本月发现 1 项指标存在异常（共 2 条记录）");
    expect(wrapper.text()).toContain("146 mmHg");
    expect(wrapper.text()).toContain("150 mmHg");
    expect(wrapper.text()).toContain("90–139 mmHg");
    expect(wrapper.text()).not.toContain("不能替代医生诊断");

    await wrapper.findAll(".abnormal-list__tail .el-button")[0].trigger("click");
    expect(mocks.router.push).toHaveBeenCalledWith({
      name: "health-data-detail",
      params: { id: "hd-i-28" },
    });
  });

  it("异常提示接收机构报告的非数值阳性结果", async () => {
    const month = new Date().toISOString().slice(0, 7);
    mocks.fetchHealthTrends.mockResolvedValue({
      data: {
        series_by_indicator: [],
        qualitative_series_by_indicator: [{
          indicator: { id: 19, name: "尿蛋白", unit: "" },
          points: [
            { date: `${month}-01`, value: "阳性", result_status: "positive", is_abnormal: true, health_data_id: "hd-i-19", reference: "阴性", source: { type: "institution", name: "虚构体检机构" } },
            { date: `${month}-02`, value: "阴性", result_status: "negative", is_abnormal: false, health_data_id: "hd-i-29", reference: "阴性", source: { type: "institution", name: "虚构体检机构" } },
          ],
          reference: { label: "机构报告定性参考" },
        }],
        source_options: [{ value: "all", label: "全部来源" }],
      },
    });

    const wrapper = mountView(TrendView, { domain_id: "1" });
    await flushPromises();

    expect(wrapper.text()).toContain("尿蛋白");
    expect(wrapper.text()).toContain("阳性");
    expect(wrapper.text()).not.toContain("历史异常");
    expect(wrapper.findAll('[data-testid="trend-chart"]')).toHaveLength(0);
  });

  it("体检详情支持按一个或多个健康方向同步筛选指标、结论和附件", async () => {
    const wrapper = mountView(HealthDataDetailView);
    await flushPromises();

    expect(wrapper.text()).toContain("已显示 2/2 个方向");
    expect(wrapper.text()).toContain("体格检查结论");
    expect(wrapper.text()).toContain("十二导联心电图");
    expect(wrapper.text()).toContain("测试上传医生（虚构）");
    expect(wrapper.text()).toContain("测试复核医生（虚构）");
    expect(wrapper.text()).toContain("已锁档展示");
    expect(wrapper.text()).toContain("2");

    wrapper.vm.handleDomainSelection([2]);
    await flushPromises();
    expect(wrapper.text()).toContain("已显示 1/2 个方向");
    expect(wrapper.text()).not.toContain("体格检查结论");
    expect(wrapper.text()).toContain("心血管检查结论");
    expect(wrapper.text()).toContain("十二导联心电图");
    expect(wrapper.find(".health-detail-hero__summary").text()).toContain("1");

    wrapper.vm.handleDomainSelection([]);
    await flushPromises();
    expect(wrapper.text()).toContain("心血管检查结论");

    wrapper.vm.selectAllDomains();
    await flushPromises();
    expect(wrapper.text()).toContain("已显示 2/2 个方向");
    expect(wrapper.text()).toContain("体格检查结论");
  });

  it("体检详情忽略旧 owner_id 并始终读取当前有效账号", async () => {
    mountView(HealthDataDetailView, { owner_id: "12" });
    await flushPromises();

    expect(mocks.fetchHealthDataDetail).toHaveBeenCalledWith("hd-i-42");
  });
});
