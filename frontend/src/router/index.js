import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";
import { dashboardRouteForRole } from "../utils/roles";

const lazy = (path) => () => import(/* @vite-ignore */ path);
const WorkspaceLayout = () => import("../layouts/WorkspaceLayout.vue");
const userMeta = { requiresAuth: true, roles: ["user"], workspace: "user" };
const orgMeta = { requiresAuth: true, roles: ["institution_admin"], workspace: "org" };
const adminMeta = { requiresAuth: true, roles: ["admin"], workspace: "admin" };

const routes = [
  { path: "/", name: "public-home", component: () => import("../views/PublicHomeView.vue"), meta: { title: "康康健健 HealthDoc" } },
  { path: "/login", name: "login", component: () => import("../views/LoginView.vue"), meta: { guestOnly: true, title: "登录" } },
  { path: "/forgot-password", name: "forgot-password", component: () => import("../views/ForgotPasswordView.vue"), meta: { guestOnly: true, title: "找回密码" } },
  { path: "/register", name: "register", component: () => import("../views/RegisterView.vue"), meta: { guestOnly: true, title: "注册" } },
  { path: "/workspace", component: WorkspaceLayout, meta: userMeta, children: [
    { path: "/dashboard", name: "dashboard", component: () => import("../views/UserDashboardView.vue"), meta: { title: "健康总览" } },
    { path: "/timeline", name: "timeline", component: () => import("../views/HealthTimelineView.vue"), meta: { title: "健康时间线" } },
    { path: "/measurements", redirect: { name: "dashboard", query: { quick: "measurement" } } },
    { path: "/trends", name: "trends", component: () => import("../views/TrendView.vue"), meta: { title: "健康趋势" } },
    { path: "/health-data", name: "health-data", component: () => import("../views/HealthDataView.vue"), meta: { title: "体检数据" } },
    { path: "/health-data/:id", name: "health-data-detail", component: () => import("../views/HealthDataDetailView.vue"), meta: { title: "体检数据详情" } },
    { path: "/reports", redirect: { name: "health-data" } },
    { path: "/reports/:id", redirect: to => ({ name: "health-data-detail", params: { id: `hd-i-${Number(to.params.id).toString(16)}` } }) },
    { path: "/friends", name: "friends", component: () => import("../views/FriendManageView.vue"), meta: { title: "亲友授权" } },
    { path: "/institutions", name: "institutions", component: () => import("../views/InstitutionListView.vue"), meta: { title: "体检机构" } },
    { path: "/institutions/:id", name: "institution-detail", component: () => import("../views/InstitutionDetailView.vue"), meta: { title: "机构详情" } },
    { path: "/appointments", name: "appointments", component: () => import("../views/AppointmentBookingView.vue"), meta: { title: "体检预约" } },
    { path: "/comments/mine", name: "my-comments", component: () => import("../views/MyCommentsView.vue"), meta: { title: "我的评论" } },
    { path: "/profile", name: "profile", component: () => import("../views/ProfileView.vue"), meta: { title: "个人资料" } },
  ] },
  { path: "/org", component: WorkspaceLayout, meta: orgMeta, children: [
    { path: "", redirect: { name: "org-dashboard" } },
    { path: "dashboard", name: "org-dashboard", component: () => import("../views/org/OrgDashboardView.vue"), meta: { title: "机构运营总览" } },
    { path: "reports", name: "org-reports", component: () => import("../views/org/OrgReportsView.vue"), meta: { title: "体检管理" } },
    { path: "profile", name: "org-profile", component: () => import("../views/org/OrgProfileView.vue"), meta: { title: "机构资料" } },
    { path: "gallery", name: "org-gallery", redirect: { name: "org-profile", query: { section: "gallery" } } },
    { path: "comments", name: "org-comments", component: () => import("../views/org/OrgCommentsView.vue"), meta: { title: "用户评价" } },
    { path: "packages", name: "org-packages", component: () => import("../views/org/OrgPackagesView.vue"), meta: { title: "体检套餐" } },
    { path: "package-reviews", name: "org-package-reviews", component: () => import("../views/org/OrgPackageReviewsView.vue"), meta: { title: "信息审核" } },
  ] },
  { path: "/admin", component: WorkspaceLayout, meta: adminMeta, children: [
    { path: "", redirect: { name: "admin-dashboard" } },
    { path: "dashboard", name: "admin-dashboard", component: () => import("../views/admin/AdminDashboardView.vue"), meta: { title: "系统运营总览" } },
    { path: "institutions", name: "admin-institutions", component: () => import("../views/admin/AdminInstitutionsView.vue"), meta: { title: "机构管理" } },
    { path: "invites", name: "admin-invites", component: () => import("../views/admin/AdminInvitesView.vue"), meta: { title: "邀请码管理" } },
    { path: "users", name: "admin-users", component: () => import("../views/admin/AdminUsersView.vue"), meta: { title: "账号管理" } },
    { path: "comments", name: "admin-comments", component: () => import("../views/CommentModerationView.vue"), meta: { title: "评论审核" } },
    { path: "package-reviews", name: "admin-package-reviews", component: () => import("../views/admin/AdminPackageReviewsView.vue"), meta: { title: "审核记录" } },
  ] },
  { path: "/403", name: "forbidden", component: () => import("../views/ForbiddenView.vue"), meta: { title: "无权访问" } },
  { path: "/:pathMatch(.*)*", name: "not-found", component: () => import("../views/NotFoundView.vue"), meta: { title: "页面不存在" } },
];

const router = createRouter({ history: createWebHistory(), routes, scrollBehavior: () => ({ top: 0 }) });
router.beforeEach(async (to) => {
  const auth = useAuthStore(); auth.hydrate();
  if (to.meta.requiresAuth) {
    if (!auth.accessToken && !(await auth.tryRefresh())) return { name: "login", query: { redirect: to.fullPath } };
    try {
      const user = await auth.fetchMe();
      if (to.meta.roles?.length && !to.meta.roles.includes(user.role)) return { name: "forbidden" };
    } catch { auth.logout(); return { name: "login" }; }
  }
  if (to.meta.guestOnly && auth.accessToken) {
    try { return dashboardRouteForRole((await auth.fetchMe()).role); } catch { auth.logout(); }
  }
  return true;
});
router.afterEach((to) => { document.title = `${to.meta.title || "健康管理"} · 康康健健 HealthDoc`; });
export default router;
