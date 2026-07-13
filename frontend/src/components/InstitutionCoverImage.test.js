import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import InstitutionCoverImage from "./InstitutionCoverImage.vue";

const institution = {
  name: "康康健健体检",
  branch_name: "徐汇分院",
  cover_image_url: "/uploads/institutions/cover.webp",
  logo_url: "/uploads/logos/legacy.png",
  images: [{ image_url: "/uploads/institutions/gallery-first.webp" }],
};

describe("InstitutionCoverImage", () => {
  it("renders the ordered gallery cover returned by the institution API", () => {
    const wrapper = mount(InstitutionCoverImage, { props: { institution } });
    const image = wrapper.get("img");

    expect(image.attributes("src")).toBe(institution.cover_image_url);
    expect(image.attributes("alt")).toBe("康康健健体检·徐汇分院封面");
    expect(image.attributes("loading")).toBe("lazy");
  });

  it("keeps compatibility with legacy logo and image payloads", async () => {
    const wrapper = mount(InstitutionCoverImage, {
      props: {
        institution: { ...institution, cover_image_url: null },
      },
    });
    expect(wrapper.get("img").attributes("src")).toBe(institution.logo_url);

    await wrapper.setProps({
      institution: {
        ...institution,
        cover_image_url: null,
        logo_url: null,
      },
    });
    expect(wrapper.get("img").attributes("src")).toBe(institution.images[0].image_url);
  });

  it("shows an accessible placeholder when no cover exists or loading fails", async () => {
    const wrapper = mount(InstitutionCoverImage, { props: { institution } });
    await wrapper.get("img").trigger("error");

    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.get('[role="img"]').attributes("aria-label")).toBe("康康健健体检·徐汇分院暂无封面");
    expect(wrapper.text()).toContain("封面暂时无法加载");

    await wrapper.setProps({ institution: { name: "无图机构" } });
    expect(wrapper.text()).toContain("暂无封面");
  });
});
