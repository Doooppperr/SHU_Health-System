import { defineComponent, h } from "vue";
import { flushPromises, shallowMount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  routerPush: vi.fn(),
  fetchIndicatorDicts: vi.fn(),
  fetchRecordDetail: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "31" } }),
  useRouter: () => ({ push: mocks.routerPush }),
}));

vi.mock("../api/indicators", () => ({
  fetchIndicatorDicts: mocks.fetchIndicatorDicts,
}));

vi.mock("../api/records", () => ({
  fetchRecordDetail: mocks.fetchRecordDetail,
  addRecordIndicator: vi.fn(),
  deleteRecordIndicator: vi.fn(),
  updateRecordIndicator: vi.fn(),
}));

import RecordDetailView from "./RecordDetailView.vue";
import { useAuthStore } from "../stores/auth";

const MainNavActionsStub = defineComponent({
  setup(_props, { slots }) {
    return () => h("div", [slots.prefix?.(), slots.default?.()]);
  },
});

const SimpleStub = defineComponent({
  setup(_props, { slots }) {
    return () => h("div", [slots.header?.(), slots.default?.()]);
  },
});

const ButtonStub = defineComponent({
  inheritAttrs: false,
  emits: ["click"],
  setup(_props, { attrs, emit, slots }) {
    return () => h(
      "button",
      { ...attrs, onClick: () => emit("click") },
      slots.default?.()
    );
  },
});

const EmptyStub = defineComponent({ setup: () => () => null });

beforeEach(() => {
  mocks.routerPush.mockReset();
  mocks.fetchIndicatorDicts.mockResolvedValue({ data: { items: [] } });
  mocks.fetchRecordDetail.mockResolvedValue({
    data: {
      item: {
        id: 31,
        display_id: "health31",
        owner: { username: "tester" },
        exam_date: "2026-07-13",
        status: "confirmed",
        indicators: [],
      },
    },
  });
});

describe("RecordDetailView OCR entry", () => {
  it("opens OCR in attach mode for the current record", async () => {
    const pinia = createPinia();
    useAuthStore(pinia).user = { id: 10, username: "tester", role: "user" };
    const wrapper = shallowMount(RecordDetailView, {
      global: {
        plugins: [pinia],
        stubs: {
          MainNavActions: MainNavActionsStub,
          ElButton: ButtonStub,
          ElCard: SimpleStub,
          ElAlert: EmptyStub,
          ElSkeleton: EmptyStub,
          ElDescriptions: SimpleStub,
          ElDescriptionsItem: SimpleStub,
          ElSelect: SimpleStub,
          ElOption: EmptyStub,
          ElInput: EmptyStub,
          ElTable: EmptyStub,
          ElTableColumn: EmptyStub,
          ElTag: SimpleStub,
        },
      },
    });
    await flushPromises();

    expect(wrapper.get('[data-testid="record-ocr-button"]').text()).toContain("OCR 上传报告");
    await wrapper.get('[data-testid="record-ocr-button"]').trigger("click");
    expect(mocks.routerPush).toHaveBeenCalledWith({
      name: "record-upload",
      query: { record_id: "31" },
    });

    wrapper.unmount();
  });
});
