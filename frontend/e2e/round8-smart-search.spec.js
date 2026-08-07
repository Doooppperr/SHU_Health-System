import { expect, test } from "@playwright/test";


test("公开目录可按女性套餐智能推荐并原地筛选", async ({ page }) => {
  await page.setViewportSize({ width: 2048, height: 1000 });
  await page.goto("/explore/institutions");
  const input = page.getByRole("textbox", { name: "搜索体检机构" });
  await input.fill("我是女生");
  await expect(page.getByRole("option", { name: /安沐女性与家庭健康中心/ }).first()).toBeVisible();
  await expect(page.getByText(/已理解|AI \+ 内容匹配|内容智能匹配|已使用内容匹配/)).toHaveCount(0);
  await page.getByRole("option", { name: /安沐女性与家庭健康中心/ }).first().click();
  await expect(page.getByRole("heading", { name: "安沐女性与家庭健康中心" })).toBeVisible();
  await expect(page.getByText(/女性年度基础关怀/).first()).toBeVisible();
  await expect(page.locator(".organization-card")).toHaveCount(1);
});


test("访客导航在桌面真正居中并在小屏无横向溢出", async ({ page }) => {
  await page.setViewportSize({ width: 2048, height: 1000 });
  await page.goto("/explore/institutions");
  await expect(page.getByRole("navigation", { name: "公开页面导航" })).toBeVisible();
  const alignment = await page.evaluate(() => {
    const nav = document.querySelector(".public-site-header .portal-nav").getBoundingClientRect();
    return Math.abs((nav.left + nav.right) / 2 - window.innerWidth / 2);
  });
  expect(alignment).toBeLessThanOrEqual(2);

  for (const width of [2048, 1902, 1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(page.getByRole("navigation", { name: "公开页面导航" })).toBeVisible();
    const layout = await page.evaluate(() => {
      const bounds = (selector) => document.querySelector(selector).getBoundingClientRect();
      const brand = bounds(".public-site-header .portal-brand");
      const nav = bounds(".public-site-header .portal-nav");
      const actions = bounds(".public-site-header .portal-actions");
      const overlaps = (left, right) => left.right > right.left && left.bottom > right.top && left.top < right.bottom;
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        brandNavOverlap: overlaps(brand, nav),
        navActionsOverlap: overlaps(nav, actions),
      };
    });
    expect(layout.overflow).toBeLessThanOrEqual(0);
    expect(layout.brandNavOverlap).toBe(false);
    expect(layout.navActionsOverlap).toBe(false);
  }

  await page.getByRole("button", { name: /主题模式/ }).click();
  await page.getByRole("menuitemradio", { name: "暗色模式" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "开启关怀模式" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-care", "on");

  await page.goto("/explore/institutions");
  const search = page.getByRole("textbox", { name: "搜索体检机构" });
  await expect(search).toBeVisible();
  await search.fill("女性");
  const mobileOption = page.getByRole("option", { name: /安沐女性与家庭健康中心/ }).first();
  await expect(mobileOption).toBeVisible();
  const dropdownBounds = await page.locator(".smart-search-dropdown").boundingBox();
  expect(dropdownBounds.x).toBeGreaterThanOrEqual(0);
  expect(dropdownBounds.x + dropdownBounds.width).toBeLessThanOrEqual(390);
});
