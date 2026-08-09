import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import IcpFilingLink from "./IcpFilingLink.vue";

describe("IcpFilingLink", () => {
  it("links the published filing number to the MIIT portal", () => {
    const wrapper = mount(IcpFilingLink);
    const link = wrapper.get("a");
    expect(link.text()).toBe("沪ICP备2026034136号-1");
    expect(link.attributes("href")).toBe("https://beian.miit.gov.cn/");
    expect(link.attributes("target")).toBe("_blank");
    expect(link.attributes("rel")).toContain("noopener");
  });
});
