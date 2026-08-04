import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OrgFinanceView from "./org/OrgFinanceView.vue";
import AdminFinanceView from "./admin/AdminFinanceView.vue";
import * as orgApi from "../api/org";
import * as adminApi from "../api/admin";

vi.mock("../api/org", async (original) => ({
  ...(await original()),
  fetchOrgFinanceSummary: vi.fn(),
  fetchOrgFinanceOrders: vi.fn(),
  refundOrgFinanceOrder: vi.fn(),
}));
vi.mock("../api/admin", async (original) => ({
  ...(await original()),
  fetchAdminFinanceSummary: vi.fn(),
  fetchAdminFinanceOrders: vi.fn(),
}));

const stubs = {
  "el-alert": { template: "<div><slot /></div>" },
  "el-card": { template: "<section><slot name='header' /><slot /></section>" },
  "el-select": { template: "<select><slot /></select>" },
  "el-option": true,
  "el-table": { template: "<div><slot /></div>" },
  "el-table-column": true,
  "el-pagination": true,
};

describe("finance workspaces", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows institution balance, settlement and refund totals", async () => {
    orgApi.fetchOrgFinanceSummary.mockResolvedValue({ data: { summary: {
      available_balance: 975, cumulative_credited: 975,
      pending_settlement: 487.5, cumulative_refunded: 97.5,
      refund_required_count: 1,
    } } });
    orgApi.fetchOrgFinanceOrders.mockResolvedValue({ data: { items: [], pagination: { page: 1, page_size: 15, total: 0 } } });
    const wrapper = mount(OrgFinanceView, { global: { stubs } });
    await flushPromises();
    expect(wrapper.text()).toContain("¥ 975.00");
    expect(wrapper.text()).toContain("¥ 487.50");
    expect(wrapper.text()).toContain("1 笔等待处理");
  });

  it("shows platform custody and fee metrics", async () => {
    adminApi.fetchAdminFinanceSummary.mockResolvedValue({ data: { summary: {
      platform_custody: 1000, platform_fee: 25,
      pending_settlement: 975, refund_required_count: 2,
      suspended_institution_count: 1,
    } } });
    adminApi.fetchAdminFinanceOrders.mockResolvedValue({ data: { items: [], pagination: { page: 1, page_size: 15, total: 0 } } });
    const wrapper = mount(AdminFinanceView, { global: { stubs } });
    await flushPromises();
    expect(wrapper.text()).toContain("¥ 1000.00");
    expect(wrapper.text()).toContain("¥ 25.00");
    expect(wrapper.text()).toContain("1 家分院已暂停");
  });
});
