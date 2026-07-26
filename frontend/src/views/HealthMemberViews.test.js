import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HealthDataView from "./HealthDataView.vue";
import HealthTimelineView from "./HealthTimelineView.vue";
import TrendView from "./TrendView.vue";
import { useAuthStore } from "../stores/auth";

const mocks = vi.hoisted(() => ({
  route: { query: {} },
  router: { push: vi.fn(), replace: vi.fn().mockResolvedValue() },
  fetchFriends: vi.fn(),
  fetchHealthData: vi.fn(),
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
  fetchHealthDomains: mocks.fetchHealthDomains,
  fetchHealthTrends: mocks.fetchHealthTrends,
  fetchTimeline: mocks.fetchTimeline,
}));
vi.mock("../api/institutions", () => ({ fetchInstitutions: mocks.fetchInstitutions }));
vi.mock("../api/ai", () => ({ streamAiTrendAnalysis: mocks.streamAiTrendAnalysis }));

const friendPayload = {
  outgoing: [{ auth_status: true, relation_name: "父亲", friend_user: { id: 12, username: "亲友账号" } }],
  incoming: [],
};
const domainPayload = { items: [{ id: 1, code: "basic", name: "基础体征与体格" }] };
const wrappers = [];

function mountView(component, query = {}) {
  mocks.route.query = query;
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

describe("健康成员筛选请求", () => {
  it("体检数据选择亲友时发送亲友编号", async () => {
    mountView(HealthDataView, { owner_id: "12" });
    await flushPromises();
    expect(mocks.fetchHealthData).toHaveBeenCalledWith(expect.objectContaining({ owner_id: 12 }));
  });

  it("健康时间线选择亲友时发送亲友编号", async () => {
    mountView(HealthTimelineView, { owner_id: "12" });
    await flushPromises();
    expect(mocks.fetchTimeline).toHaveBeenCalledWith(expect.objectContaining({ owner_id: 12 }));
  });

  it("健康趋势和 AI 使用相同的亲友编号", async () => {
    mountView(TrendView, { owner_id: "12", domain_id: "1" });
    await flushPromises();
    expect(mocks.fetchHealthTrends).toHaveBeenCalledWith(1, expect.objectContaining({ owner_id: 12, source_type: "all" }));
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
});
