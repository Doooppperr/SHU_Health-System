import { mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { afterEach, describe, expect, it } from "vitest";

import SmartInstitutionSearch from "./SmartInstitutionSearch.vue";

const wrappers = [];
const search = {
  mode: "hybrid",
  intent_summary: "女性年度体检",
  suggestions: [
    {
      kind: "organization",
      organization_id: 4,
      institution_id: null,
      package_id: null,
      title: "安沐女性与家庭健康中心",
      subtitle: "2 家相关分院",
      reason: "匹配套餐：女性年度基础关怀",
    },
    {
      kind: "package",
      organization_id: 4,
      institution_id: 12,
      package_id: 21,
      title: "女性年度基础关怀",
      subtitle: "安沐女性与家庭健康中心 · 黄浦院区",
      reason: "适用人群",
    },
  ],
};

afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

function mountSearch(props = {}) {
  const wrapper = mount(SmartInstitutionSearch, {
    attachTo: document.body,
    props: { modelValue: "女性", search, ...props },
    global: { plugins: [ElementPlus] },
  });
  wrappers.push(wrapper);
  return wrapper;
}

describe("SmartInstitutionSearch", () => {
  it("renders accessible explainable suggestions and emits a selection", async () => {
    const wrapper = mountSearch();
    const input = wrapper.get("input");
    await wrapper.get(".smart-institution-search").trigger("focusin");

    expect(wrapper.get(".smart-institution-search").attributes("role")).toBe("combobox");
    expect(wrapper.get(".smart-institution-search").attributes("aria-expanded")).toBe("true");
    expect(wrapper.findAll('[role="option"]')).toHaveLength(2);
    expect(wrapper.text()).toContain("AI + 内容匹配");
    expect(wrapper.text()).toContain("匹配套餐：女性年度基础关怀");

    await wrapper.findAll('[role="option"]')[1].trigger("click");
    expect(wrapper.emitted("select")[0][0]).toEqual(search.suggestions[1]);
  });

  it("supports keyboard navigation and emits the current query", async () => {
    const wrapper = mountSearch();
    const input = wrapper.get("input");
    await wrapper.get(".smart-institution-search").trigger("focusin");
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("select")[0][0]).toEqual(search.suggestions[1]);

    await input.setValue("心血管");
    expect(wrapper.emitted("update:modelValue").at(-1)).toEqual(["心血管"]);
    expect(wrapper.emitted("search").at(-1)).toEqual(["心血管"]);
  });

  it("keeps content fallback useful when the model is unavailable", async () => {
    const wrapper = mountSearch({
      search: { mode: "content_fallback", intent_summary: "复杂需求", suggestions: [] },
    });
    await wrapper.get(".smart-institution-search").trigger("focusin");
    expect(wrapper.text()).toContain("已使用内容匹配");
    expect(wrapper.text()).toContain("暂无匹配推荐");
  });
});
