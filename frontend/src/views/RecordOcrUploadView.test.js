import { flushPromises, shallowMount } from "@vue/test-utils";
import { defineComponent, reactive } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { ElMessage, ElMessageBox } from "element-plus";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  routeQuery: {},
  routerPush: vi.fn(),
  fetchFriends: vi.fn(),
  fetchIndicatorDicts: vi.fn(),
  fetchInstitutions: vi.fn(),
  fetchInstitutionPackages: vi.fn(),
  fetchUsers: vi.fn(),
  fetchRecordDetail: vi.fn(),
  fetchPendingRecordOcr: vi.fn(),
  cancelPendingRecordOcr: vi.fn(),
  uploadRecordByOcr: vi.fn(),
  confirmRecord: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: mocks.routeQuery }),
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
  fetchRecordDetail: mocks.fetchRecordDetail,
  fetchPendingRecordOcr: mocks.fetchPendingRecordOcr,
  cancelPendingRecordOcr: mocks.cancelPendingRecordOcr,
}));

import RecordOcrUploadView from "./RecordOcrUploadView.vue";
import { useAuthStore } from "../stores/auth";

mocks.routeQuery = reactive(mocks.routeQuery);

const dicts = [
  { id: 2, code: "FBG", name: "空腹血糖", value_type: "numeric", unit: "mmol/L" },
  { id: 9, code: "UA", name: "尿酸", value_type: "numeric", unit: "μmol/L" },
];
const wrappers = [];
const EmptyStub = defineComponent({ name: "EmptyStub", setup: () => () => null });

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function ocrResult({ pending = false, recordId = 31 } = {}) {
  return {
    item: {
      id: recordId,
      status: pending ? "confirmed" : "parsed",
      indicators: [],
    },
    ocr: {
      provider: "huawei",
      pending_confirmation: pending,
      attachment_id: pending ? `attachment-${recordId}` : null,
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
  };
}

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
  Object.values(mocks)
    .filter((mock) => typeof mock?.mockReset === "function")
    .forEach((mock) => mock.mockReset());
  Object.keys(mocks.routeQuery).forEach((key) => delete mocks.routeQuery[key]);
  mocks.fetchFriends.mockResolvedValue({ data: { manageable: [] } });
  mocks.fetchIndicatorDicts.mockResolvedValue({ data: { items: dicts } });
  mocks.fetchInstitutions.mockResolvedValue({ data: { items: [] } });
  mocks.fetchInstitutionPackages.mockResolvedValue({ data: { items: [] } });
  mocks.fetchUsers.mockResolvedValue({ data: { items: [] } });
  mocks.fetchRecordDetail.mockResolvedValue({
    data: {
      item: {
        id: 31,
        display_id: "health31",
        owner_id: 10,
        owner: { username: "tester" },
        exam_date: "2026-07-13",
        institution_id: null,
        package_id: null,
        indicator_count: 1,
        indicators: [{ id: 1, value: "32" }],
        status: "confirmed",
        ocr_pending_confirmation: false,
      },
    },
  });
  mocks.uploadRecordByOcr.mockResolvedValue({
    data: ocrResult(),
  });
  mocks.fetchPendingRecordOcr.mockResolvedValue({ data: ocrResult({ pending: true }) });
  mocks.cancelPendingRecordOcr.mockResolvedValue({
    data: {
      item: { id: 31, status: "confirmed", ocr_pending_confirmation: false },
      message: "pending OCR attachment cancelled",
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
  vi.spyOn(ElMessageBox, "confirm").mockResolvedValue("confirm");
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.restoreAllMocks();
});

describe("RecordOcrUploadView confirmation", () => {
  it("attaches OCR to the selected existing record without sending new-record metadata", async () => {
    mocks.routeQuery.record_id = "31";
    mocks.uploadRecordByOcr.mockResolvedValueOnce({
      data: ocrResult({ pending: true }),
    });
    const wrapper = await mountView();

    expect(mocks.fetchRecordDetail).toHaveBeenCalledWith(31);
    expect(wrapper.vm.isAttachMode).toBe(true);
    expect(wrapper.vm.targetRecord.id).toBe(31);
    expect(wrapper.text()).toContain("health31");

    wrapper.vm.onFileChange(
      { raw: new File(["report"], "report.png", { type: "image/png" }) },
      []
    );
    await wrapper.vm.submitUpload();
    await flushPromises();

    const payload = mocks.uploadRecordByOcr.mock.calls[0][0];
    expect(payload.get("record_id")).toBe("31");
    expect(payload.get("owner_id")).toBeNull();
    expect(payload.get("exam_date")).toBeNull();
    expect(ElMessage.success).toHaveBeenCalledWith(
      "报告已解析，确认入档前原档案保持不变"
    );
    expect(wrapper.vm.uploadNeedsConfirmation).toBe(true);

    await wrapper.vm.confirmParsedRecord();
    expect(mocks.confirmRecord).toHaveBeenCalledWith(
      31,
      expect.objectContaining({ attachment_id: "attachment-31" })
    );
  });

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
    expect(wrapper.text()).toContain("health31");
    expect(wrapper.text()).not.toMatch(/档案ID\s*31(?:\D|$)/);

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
    expect(wrapper.vm.uploadNeedsConfirmation).toBe(false);
  });

  it("reloads and resets the pending upload when the target query changes", async () => {
    mocks.routeQuery.record_id = "31";
    mocks.fetchRecordDetail.mockImplementation((recordId) => Promise.resolve({
      data: {
        item: {
          id: recordId,
          display_id: `health${recordId}`,
          owner_id: 10,
          owner: { username: "tester" },
          exam_date: "2026-07-13",
          institution_id: null,
          package_id: null,
          indicator_count: 1,
          status: "confirmed",
        },
      },
    }));
    const wrapper = await mountView();
    wrapper.vm.onFileChange(
      { raw: new File(["report"], "report.png", { type: "image/png" }) },
      []
    );
    await wrapper.vm.submitUpload();
    await flushPromises();
    expect(wrapper.vm.uploadResult).not.toBeNull();

    mocks.routeQuery.record_id = "32";
    await flushPromises();

    expect(mocks.fetchRecordDetail).toHaveBeenLastCalledWith(32);
    expect(wrapper.vm.targetRecord.id).toBe(32);
    expect(wrapper.vm.uploadResult).toBeNull();
    expect(wrapper.vm.selectedFile).toBeNull();
    expect(wrapper.vm.mappingDraftRows).toEqual([]);
  });

  it("discards an old upload response when the target changes during the request", async () => {
    mocks.routeQuery.record_id = "31";
    mocks.fetchRecordDetail.mockImplementation((recordId) => Promise.resolve({
      data: {
        item: {
          id: recordId,
          display_id: `health${recordId}`,
          owner_id: 10,
          owner: { username: "tester" },
          exam_date: "2026-07-13",
          institution_id: null,
          package_id: null,
          indicator_count: 1,
          status: "confirmed",
          ocr_pending_confirmation: false,
        },
      },
    }));
    const pendingUpload = deferred();
    mocks.uploadRecordByOcr.mockReturnValueOnce(pendingUpload.promise);
    const wrapper = await mountView();
    wrapper.vm.onFileChange(
      { raw: new File(["report"], "report.png", { type: "image/png" }) },
      []
    );

    const submitPromise = wrapper.vm.submitUpload();
    await flushPromises();
    mocks.routeQuery.record_id = "32";
    await flushPromises();
    pendingUpload.resolve({ data: ocrResult({ pending: true, recordId: 31 }) });
    await submitPromise;
    await flushPromises();

    expect(wrapper.vm.targetRecord.id).toBe(32);
    expect(wrapper.vm.uploadResult).toBeNull();
    expect(wrapper.vm.mappingDraftRows).toEqual([]);
  });

  it("discards an old confirmation response when the target changes", async () => {
    mocks.routeQuery.record_id = "31";
    mocks.fetchRecordDetail.mockImplementation((recordId) => Promise.resolve({
      data: {
        item: {
          id: recordId,
          display_id: `health${recordId}`,
          owner_id: 10,
          owner: { username: "tester" },
          exam_date: "2026-07-13",
          institution_id: null,
          package_id: null,
          indicator_count: 1,
          status: "confirmed",
          ocr_pending_confirmation: false,
        },
      },
    }));
    mocks.uploadRecordByOcr.mockResolvedValueOnce({
      data: ocrResult({ pending: true, recordId: 31 }),
    });
    const wrapper = await mountView();
    wrapper.vm.onFileChange(
      { raw: new File(["report"], "report.png", { type: "image/png" }) },
      []
    );
    await wrapper.vm.submitUpload();
    await flushPromises();

    const pendingConfirm = deferred();
    mocks.confirmRecord.mockReturnValueOnce(pendingConfirm.promise);
    const confirmPromise = wrapper.vm.confirmParsedRecord();
    await flushPromises();
    mocks.routeQuery.record_id = "32";
    await flushPromises();
    pendingConfirm.resolve({
      data: {
        item: { id: 31, status: "confirmed", ocr_pending_confirmation: false },
        ocr: { confirmed_count: 2 },
      },
    });
    await confirmPromise;
    await flushPromises();

    expect(wrapper.vm.targetRecord.id).toBe(32);
    expect(wrapper.vm.uploadResult).toBeNull();
  });

  it("restores and can cancel a pending OCR attachment", async () => {
    mocks.routeQuery.record_id = "31";
    mocks.fetchRecordDetail.mockResolvedValueOnce({
      data: {
        item: {
          id: 31,
          display_id: "health31",
          owner_id: 10,
          owner: { username: "tester" },
          exam_date: "2026-07-13",
          institution_id: null,
          package_id: null,
          indicator_count: 1,
          status: "confirmed",
          ocr_pending_confirmation: true,
        },
      },
    });
    const wrapper = await mountView();

    expect(mocks.fetchPendingRecordOcr).toHaveBeenCalledWith(31);
    expect(wrapper.vm.uploadResult.ocr.attachment_id).toBe("attachment-31");

    await wrapper.vm.cancelPendingOcr();
    await flushPromises();

    expect(mocks.cancelPendingRecordOcr).toHaveBeenCalledWith(31, "attachment-31");
    expect(wrapper.vm.uploadResult).toBeNull();
    expect(wrapper.vm.targetRecord.ocr_pending_confirmation).toBe(false);
    expect(ElMessage.success).toHaveBeenCalledWith(
      "已放弃暂存报告，原档案保持不变"
    );
  });

  it("does not cancel the old attachment when the target changes during confirmation", async () => {
    mocks.routeQuery.record_id = "31";
    mocks.fetchRecordDetail.mockImplementation((recordId) => Promise.resolve({
      data: {
        item: {
          id: recordId,
          display_id: `health${recordId}`,
          owner_id: 10,
          owner: { username: "tester" },
          exam_date: "2026-07-13",
          institution_id: null,
          package_id: null,
          indicator_count: 1,
          status: "confirmed",
          ocr_pending_confirmation: recordId === 31,
        },
      },
    }));
    const pendingConfirmation = deferred();
    ElMessageBox.confirm.mockReturnValueOnce(pendingConfirmation.promise);
    const wrapper = await mountView();

    const cancelPromise = wrapper.vm.cancelPendingOcr();
    await flushPromises();
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1);

    mocks.routeQuery.record_id = "32";
    await flushPromises();
    expect(wrapper.vm.targetRecord.id).toBe(32);

    pendingConfirmation.resolve("confirm");
    await cancelPromise;
    await flushPromises();

    expect(mocks.cancelPendingRecordOcr).not.toHaveBeenCalled();
    expect(wrapper.vm.targetRecord.id).toBe(32);
    expect(wrapper.vm.uploadResult).toBeNull();
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
