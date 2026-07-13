import { defineComponent, h } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RecordListView from "./RecordListView.vue";
import { useAuthStore } from "../stores/auth";

const mocks = vi.hoisted(() => ({
  routerPush: vi.fn(),
  aiStore: {
    isSending: false,
    prepareRecordAnalysis: vi.fn(),
  },
  fetchRecords: vi.fn(),
  fetchFriends: vi.fn(),
  fetchInstitutions: vi.fn(),
  fetchInstitutionPackages: vi.fn(),
  fetchUsers: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}));

vi.mock("../stores/aiChat", () => ({
  useAiChatStore: () => mocks.aiStore,
}));

vi.mock("../api/records", () => ({
  fetchRecords: mocks.fetchRecords,
  createRecord: vi.fn(),
  deleteRecord: vi.fn(),
  updateRecord: vi.fn(),
}));

vi.mock("../api/friends", () => ({
  fetchFriends: mocks.fetchFriends,
}));

vi.mock("../api/institutions", () => ({
  fetchInstitutions: mocks.fetchInstitutions,
  fetchInstitutionPackages: mocks.fetchInstitutionPackages,
}));

vi.mock("../api/users", () => ({
  fetchUsers: mocks.fetchUsers,
}));

const ElTableStub = defineComponent({
  name: "ElTable",
  props: {
    data: { type: Array, default: () => [] },
    rowKey: { type: [String, Function], default: undefined },
  },
  emits: ["selection-change"],
  methods: {
    clearSelection() {},
    toggleRowSelection() {},
  },
  setup(_props, { slots }) {
    return () => h("div", { "data-testid": "record-table" }, slots.default?.());
  },
});

const ElTableColumnStub = defineComponent({
  name: "ElTableColumn",
  props: {
    type: { type: String, default: "" },
    selectable: { type: Function, default: undefined },
  },
  setup() {
    return () => null;
  },
});

const ElButtonStub = defineComponent({
  name: "ElButton",
  inheritAttrs: false,
  props: {
    disabled: Boolean,
    loading: Boolean,
  },
  emits: ["click"],
  setup(props, { attrs, emit, slots }) {
    return () => h(
      "button",
      {
        ...attrs,
        disabled: props.disabled || props.loading,
        onClick: () => emit("click"),
      },
      slots.default?.()
    );
  },
});

const MainNavActionsStub = defineComponent({
  name: "MainNavActions",
  setup(_props, { slots }) {
    return () => h("div", [slots.prefix?.(), slots.default?.()]);
  },
});

const simpleStub = defineComponent({
  setup(_props, { slots }) {
    return () => h("div", [slots.header?.(), slots.default?.(), slots.footer?.()]);
  },
});

const records = [
  { id: 1, owner_id: 10, status: "confirmed", indicator_count: 2, owner: { username: "本人" } },
  { id: 2, owner_id: 10, status: "confirmed", indicator_count: 1, owner: { username: "本人" } },
  { id: 3, owner_id: 20, status: "confirmed", indicator_count: 3, owner: { username: "亲友" } },
  { id: 4, owner_id: 10, status: "draft", indicator_count: 4, owner: { username: "本人" } },
  { id: 5, owner_id: 10, status: "confirmed", indicator_count: 0, owner: { username: "本人" } },
];

async function mountView({ role = "user" } = {}) {
  const pinia = createPinia();
  useAuthStore(pinia).user = { id: 10, username: "tester", role };
  const wrapper = mount(RecordListView, {
    global: {
      plugins: [pinia],
      stubs: {
        MainNavActions: MainNavActionsStub,
        ElTable: ElTableStub,
        ElTableColumn: ElTableColumnStub,
        ElButton: ElButtonStub,
        ElCard: simpleStub,
        ElAlert: simpleStub,
        ElDialog: simpleStub,
        ElForm: simpleStub,
        ElFormItem: simpleStub,
        ElDatePicker: simpleStub,
        ElSelect: simpleStub,
        ElOption: simpleStub,
      },
      directives: {
        loading: () => {},
      },
    },
  });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  mocks.routerPush.mockReset();
  mocks.aiStore.isSending = false;
  mocks.aiStore.prepareRecordAnalysis.mockReset();
  mocks.fetchRecords.mockResolvedValue({ data: { items: records } });
  mocks.fetchFriends.mockResolvedValue({ data: { manageable: [] } });
  mocks.fetchInstitutions.mockResolvedValue({ data: { items: [] } });
  mocks.fetchInstitutionPackages.mockResolvedValue({ data: { items: [] } });
  mocks.fetchUsers.mockResolvedValue({ data: { items: [] } });
});

describe("RecordListView intelligent analysis selection", () => {
  it("offers both a new OCR flow and an attach-to-record OCR flow", async () => {
    const wrapper = await mountView();

    expect(wrapper.get('[data-testid="new-record-ocr-button"]').text()).toContain("OCR 上传报告");
    wrapper.vm.goUpload(records[0]);
    expect(mocks.routerPush).toHaveBeenLastCalledWith({
      name: "record-upload",
      query: { record_id: "1" },
    });

    wrapper.vm.goUpload();
    expect(mocks.routerPush).toHaveBeenLastCalledWith({ name: "record-upload" });
  });

  it("only allows confirmed records that contain indicators", async () => {
    const wrapper = await mountView();
    const selectionColumn = wrapper.findAllComponents(ElTableColumnStub)[0];
    const selectable = selectionColumn.props("selectable");

    expect(selectable(records[0])).toBe(true);
    expect(selectable(records[3])).toBe(false);
    expect(selectable(records[4])).toBe(false);
    expect(wrapper.get('[data-testid="record-analysis-button"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-testid="record-analysis-button"]').text()).toContain("智能分析（0）");
  });

  it("locks selection to the first owner and unlocks after clearing", async () => {
    const wrapper = await mountView();
    const table = wrapper.findComponent(ElTableStub);
    const selectable = wrapper.findAllComponents(ElTableColumnStub)[0].props("selectable");

    table.vm.$emit("selection-change", [records[0]]);
    await wrapper.vm.$nextTick();
    expect(selectable(records[1])).toBe(true);
    expect(selectable(records[2])).toBe(false);

    table.vm.$emit("selection-change", []);
    await wrapper.vm.$nextTick();
    expect(selectable(records[2])).toBe(true);
  });

  it("normalizes a select-all event to one owner", async () => {
    const wrapper = await mountView();
    const table = wrapper.findComponent(ElTableStub);

    table.vm.$emit("selection-change", [records[0], records[1], records[2]]);
    await wrapper.vm.$nextTick();
    expect(wrapper.get('[data-testid="record-analysis-button"]').text()).toContain("智能分析（2）");

    await wrapper.get('[data-testid="record-analysis-button"]').trigger("click");
    expect(mocks.aiStore.prepareRecordAnalysis).toHaveBeenCalledWith([records[0], records[1]]);
  });

  it("passes every selected row to the AI store without applying a count limit", async () => {
    const manyRecords = Array.from({ length: 8 }, (_, index) => ({
      id: index + 100,
      owner_id: 10,
      status: "confirmed",
      indicator_count: 1,
    }));
    mocks.fetchRecords.mockResolvedValue({ data: { items: manyRecords } });
    const wrapper = await mountView();

    wrapper.findComponent(ElTableStub).vm.$emit("selection-change", manyRecords);
    await wrapper.vm.$nextTick();
    const button = wrapper.get('[data-testid="record-analysis-button"]');
    expect(button.text()).toContain("智能分析（8）");
    expect(button.attributes("disabled")).toBeUndefined();

    await button.trigger("click");
    expect(mocks.aiStore.prepareRecordAnalysis).toHaveBeenCalledOnce();
    expect(mocks.aiStore.prepareRecordAnalysis).toHaveBeenCalledWith(manyRecords);
  });

  it("disables analysis while the AI store is sending", async () => {
    mocks.aiStore.isSending = true;
    const wrapper = await mountView();
    wrapper.findComponent(ElTableStub).vm.$emit("selection-change", [records[0]]);
    await wrapper.vm.$nextTick();

    expect(wrapper.get('[data-testid="record-analysis-button"]').attributes("disabled")).toBeDefined();
    await wrapper.get('[data-testid="record-analysis-button"]').trigger("click");
    expect(mocks.aiStore.prepareRecordAnalysis).not.toHaveBeenCalled();
  });

  it("does not expose record analysis controls to administrators", async () => {
    const wrapper = await mountView({ role: "admin" });

    expect(wrapper.find('[data-testid="record-analysis-button"]').exists()).toBe(false);
    expect(
      wrapper
        .findAllComponents(ElTableColumnStub)
        .some((column) => column.props("type") === "selection")
    ).toBe(false);
  });
});
