import { flushPromises, shallowMount } from "@vue/test-utils";
import { defineComponent } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { ElMessage } from "element-plus";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  routerPush: vi.fn(),
  fetchFriends: vi.fn(),
  fetchIndicatorDicts: vi.fn(),
  fetchInstitutions: vi.fn(),
  fetchInstitutionPackages: vi.fn(),
  fetchUsers: vi.fn(),
  uploadRecordByOcr: vi.fn(),
  confirmRecord: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}));
vi.mock("../api/friends", () => ({ fetchFriends: mocks.fetchFriends }));
vi.mock("../api/indicators", () => ({
  fetchIndicatorDicts: mocks.fetchIndicatorDicts,
}));
vi.mock("../api/institutions", () => ({
  fetchInstitutions: mocks.fetchInstitutions,
  fetchInstitutionPackages: mocks.fetchInstitutionPackages,
}));
vi.mock("../api/users", () => ({ fetchUsers: mocks.fetchUsers }));
vi.mock("../api/records", () => ({
  uploadRecordByOcr: mocks.uploadRecordByOcr,
  confirmRecord: mocks.confirmRecord,
}));

import RecordOcrUploadView from "./RecordOcrUploadView.vue";
import { useAuthStore } from "../stores/auth";

const dicts = [
  { id: 2, code: "FBG", name: "空腹血糖", value_type: "numeric", unit: "mmol/L" },
  { id: 9, code: "UA", name: "尿酸", value_type: "numeric", unit: "μmol/L" },
];
const wrappers = [];
const EmptyStub = defineComponent({ name: "EmptyStub", setup: () => () => null });

async function mountView() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const authStore = useAuthStore(pinia);
  authStore.accessToken = "token";
  authStore.user = { id: 10, username: "tester", role: "user" };
  const wrapper = shallowMount(RecordOcrUploadView, {
    global: {
      plugins: [pinia],
      stubs: {
        MainNavActions: true,
        ElTable: EmptyStub,
        ElTableColumn: EmptyStub,
      },
    },
  });
  wrappers.push(wrapper);
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.fetchFriends.mockResolvedValue({ data: { manageable: [] } });
  mocks.fetchIndicatorDicts.mockResolvedValue({ data: { items: dicts } });
  mocks.fetchInstitutions.mockResolvedValue({ data: { items: [] } });
  mocks.fetchInstitutionPackages.mockResolvedValue({ data: { items: [] } });
  mocks.fetchUsers.mockResolvedValue({ data: { items: [] } });
  mocks.uploadRecordByOcr.mockResolvedValue({
    data: {
      item: { id: 31, status: "parsed", indicators: [] },
      ocr: {
        provider: "huawei",
        candidate_mappings: [
          {
            field_index: 0,
            label: "空腹血糖",
            value: "6.8 mmol/L (reference 3.9 - 6.1)",
            indicator_dict_id: "2",
            indicator_code: "FBG",
            indicator_name: "空腹血糖",
            score: "0.98",
          },
          {
            field_index: 1,
            label: "尿酸",
            value: "389 umol/L",
            indicator_dict_id: 9,
            indicator_code: "UA",
            indicator_name: "尿酸",
            score: 1,
          },
        ],
      },
    },
  });
  mocks.confirmRecord.mockResolvedValue({
    data: {
      item: { id: 31, status: "confirmed", indicators: [] },
      ocr: { confirmed_count: 2 },
    },
  });
  vi.spyOn(ElMessage, "success").mockImplementation(() => {});
  vi.spyOn(ElMessage, "warning").mockImplementation(() => {});
  vi.spyOn(ElMessage, "error").mockImplementation(() => {});
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.restoreAllMocks();
});

describe("RecordOcrUploadView confirmation", () => {
  it("refreshes options and sends only normalized current mappings", async () => {
    const wrapper = await mountView();
    wrapper.vm.form.exam_date = "2026-07-13";
    wrapper.vm.onFileChange(
      { raw: new File(["report"], "report.png", { type: "image/png" }) },
      []
    );

    await wrapper.vm.submitUpload();
    await flushPromises();
    expect(mocks.fetchIndicatorDicts).toHaveBeenCalledTimes(2);
    expect(wrapper.vm.mappingDraftRows.map((row) => row.indicator_dict_id)).toEqual([2, 9]);

    await wrapper.vm.confirmParsedRecord();
    expect(mocks.confirmRecord).toHaveBeenCalledWith(31, {
      confirmed_mappings: [
        {
          indicator_dict_id: 2,
          value: "6.8 mmol/L (reference 3.9 - 6.1)",
          score: 0.98,
        },
        { indicator_dict_id: 9, value: "389 umol/L", score: 1 },
      ],
    });
  });

  it("does not call confirm when an active row contains a stale option", async () => {
    const wrapper = await mountView();
    wrapper.vm.form.exam_date = "2026-07-13";
    wrapper.vm.onFileChange(
      { raw: new File(["report"], "report.png", { type: "image/png" }) },
      []
    );
    await wrapper.vm.submitUpload();
    await flushPromises();
    wrapper.vm.mappingDraftRows[0].indicator_dict_id = 999;

    await wrapper.vm.confirmParsedRecord();

    expect(mocks.confirmRecord).not.toHaveBeenCalled();
    expect(ElMessage.error).toHaveBeenCalledWith(
      expect.stringContaining("请选择当前指标字典中的有效指标")
    );
  });

  it("keeps the already loaded dictionary when the post-upload refresh fails", async () => {
    const wrapper = await mountView();
    mocks.fetchIndicatorDicts.mockRejectedValueOnce(new Error("offline"));
    wrapper.vm.form.exam_date = "2026-07-13";
    wrapper.vm.onFileChange(
      { raw: new File(["report"], "report.png", { type: "image/png" }) },
      []
    );

    await wrapper.vm.submitUpload();
    await flushPromises();
    expect(ElMessage.warning).toHaveBeenCalledWith(
      expect.stringContaining("已保留当前选项")
    );
    expect(wrapper.vm.indicatorDicts).toEqual(dicts);

    await wrapper.vm.confirmParsedRecord();
    expect(mocks.confirmRecord).toHaveBeenCalledOnce();
  });
});
