import { expect, test } from "@playwright/test";

const demoPassword = process.env.E2E_TEST_PASSWORD || "Shuhealthdoc！";

async function login(page, username) {
  const captchaResponse = page.waitForResponse((response) => (
    response.url().includes("/api/auth/captcha") && response.status() === 200
  ));
  await page.goto("/login");
  const captcha = await (await captchaResponse).json();
  expect(captcha.captcha_answer, "E2E backend must expose testing captcha answers").toBeTruthy();
  await page.getByPlaceholder("请输入用户名").fill(username);
  await page.getByPlaceholder("请输入密码").fill(demoPassword);
  await page.getByPlaceholder("输入验证码").fill(captcha.captcha_answer);
  await page.getByRole("button", { name: "登录并进入工作台" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

test("访客可浏览机构套餐，预约登录跳转保留上下文", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /让每一次体检/ })).toBeVisible();
  await expect(page.getByText("上海市宝山区上大路99号")).toBeVisible();
  await expect(page.getByText("021-114514")).toBeVisible();
  await expect(page.getByText("shucs666@shu.edu.cn")).toBeVisible();

  const catalog = await (await page.request.get("/api/public/organizations")).json();
  const institutionId = catalog.items[0].branches[0].id;
  await page.goto(`/explore/institutions/${institutionId}?appointment_date=2030-01-01`);
  await expect(page.getByRole("heading", { name: "服务介绍" })).toBeVisible();
  const bookingButton = page.getByRole("button", { name: "登录后预约" }).first();
  await expect(bookingButton).toBeVisible();
  await bookingButton.click();
  await expect(page).toHaveURL(/\/login\?redirect=/);

  const redirect = new URL(page.url()).searchParams.get("redirect");
  expect(redirect).toContain("/appointments?");
  expect(redirect).toContain(`institution_id=${institutionId}`);
  expect(redirect).toMatch(/package_id=\d+/);
  expect(redirect).toContain("appointment_date=2030-01-01");
});

test("关联账号支持三层直接切换并在左下角显示真实姓名", async ({ page }) => {
  await login(page, "test1");
  await expect(page.getByText("请先完成实名认证")).toHaveCount(0);

  for (const expectedName of ["陈雨桐", "周婧", "顾远"]) {
    await page.getByRole("button", { name: "切换关联账号" }).click();
    await page.getByText(`切换至 ${expectedName}`, { exact: true }).click();
    await expect(page.locator(".workspace-user strong")).toHaveText(expectedName);
    await expect(page.locator(".workspace-avatar")).toHaveText(expectedName.slice(0, 1));
  }
});

test("未实名认证账号登录后立即提示一次性实名认证", async ({ page }) => {
  await login(page, "test6");
  await expect(page.getByRole("dialog", { name: "完成实名认证" })).toBeVisible();
  await expect(page.getByText("姓名、性别和出生日期提交后将锁定")).toBeVisible();
  await page.getByRole("button", { name: "稍后完善" }).click();
  await expect(page.getByText("请先完成实名认证")).toBeVisible();
  await expect(page.getByText("日常测量、体检预约等健康服务暂不可用")).toBeVisible();
});

test("用户预约页展示个人投诉闭环与可操作状态", async ({ page }) => {
  await login(page, "test2");
  await page.goto("/appointments");

  await expect(page.getByRole("heading", { name: "投诉与退款" })).toBeVisible();
  await page.getByRole("button", { name: /预约记录/ }).click();
  const payButton = page.getByRole("button", { name: "立即付款" }).first();
  await expect(payButton).toBeVisible();
  await payButton.click();
  const paymentDialog = page.getByRole("dialog", { name: "订单付款" });
  await expect(paymentDialog).toBeVisible();
  await paymentDialog.getByRole("button", { name: "立即付款" }).click();
  await expect(paymentDialog.getByText("付款成功", { exact: true })).toBeVisible();
  await expect(page.locator(".complaint-card").first()).toBeVisible();
  await expect(page.locator(".complaint-card").first().getByText(/待机构处理|待用户确认|平台处理中|已解决/)).toBeVisible();
});

test("机构端可进入画像、报告复核和投诉处理工作台", async ({ page }) => {
  await login(page, "institution1_staff1");
  await page.goto("/org/dashboard");
  await expect(page.getByRole("heading", { name: "用户人群画像与套餐分析" })).toBeVisible();
  await expect(page.getByText(/去重受检者 \d+ 人/)).toBeVisible();

  await page.goto("/org/reports");
  await expect(page.getByRole("heading", { name: "接待与健康数据归档" })).toBeVisible();
  await page.getByRole("button", { name: /本院归档/ }).click();
  await page.getByRole("button", { name: "查看完整内容" }).first().click();
  await expect(page.getByRole("heading", { name: "上传、复核与正式归档" })).toBeVisible();

  await page.goto("/org/complaints");
  await expect(page.getByRole("heading", { name: "投诉处理" })).toBeVisible();

  await page.goto("/org/finance");
  await expect(page.getByRole("heading", { name: "收款与退款", level: 2 })).toBeVisible();
});

test("管理员可进入评论处罚申诉与平台投诉工作台", async ({ page }) => {
  await login(page, "demo_admin");
  await page.goto("/admin/comments");
  await expect(page.getByText(/封禁申诉（\d+）/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "评论审核" })).toBeVisible();

  await page.goto("/admin/complaints");
  await expect(page.getByRole("heading", { name: "投诉记录" })).toBeVisible();

  await page.goto("/admin/finance");
  await expect(page.getByRole("heading", { name: "托管、服务费与退款治理" })).toBeVisible();
});

test("移动端机构财务页面保持可操作且不产生页面级横向溢出", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, "institution1_staff1");
  await page.goto("/org/finance");

  await expect(page.getByRole("heading", { name: "收款与退款", level: 2 })).toBeVisible();
  await expect(page.getByText("可用余额", { exact: true })).toBeVisible();
  const viewportMetrics = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(viewportMetrics.scrollWidth).toBeLessThanOrEqual(viewportMetrics.clientWidth);
});
