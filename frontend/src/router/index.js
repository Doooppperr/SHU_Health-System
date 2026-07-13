import { createRouter, createWebHistory } from "vue-router";

import { useAuthStore } from "../stores/auth";
import { dashboardRouteForRole } from "../utils/roles";

const WorkspaceLayout = () => import("../layouts/WorkspaceLayout.vue");
const PublicHomeView = () => import("../views/PublicHomeView.vue");
const LoginView = () => import("../views/LoginView.vue");
const RegisterView = () => import("../views/RegisterView.vue");
const ForbiddenView = () => import("../views/ForbiddenView.vue");
const NotFoundView = () => import("../views/NotFoundView.vue");

const UserDashboardView = () => import("../views/UserDashboardView.vue");
const InstitutionListView = () => import("../views/InstitutionListView.vue");
const InstitutionDetailView = () => import("../views/InstitutionDetailView.vue");
const RecordListView = () => import("../views/RecordListView.vue");
const RecordOcrUploadView = () => import("../views/RecordOcrUploadView.vue");
const RecordDetailView = () => import("../views/RecordDetailView.vue");
const FriendManageView = () => import("../views/FriendManageView.vue");
const TrendView = () => import("../views/TrendView.vue");
const MyCommentsView = () => import("../views/MyCommentsView.vue");
const HomeView = () => import("../views/HomeView.vue");

const OrgDashboardView = () => import("../views/org/OrgDashboardView.vue");
const OrgProfileView = () => import("../views/org/OrgProfileView.vue");
const OrgGalleryView = () => import("../views/org/OrgGalleryView.vue");
const OrgPackagesView = () => import("../views/org/OrgPackagesView.vue");
const OrgHealthRecordsView = () => import("../views/org/OrgHealthRecordsView.vue");
const OrgTrendsView = () => import("../views/org/OrgTrendsView.vue");

const AdminDashboardView = () => import("../views/admin/AdminDashboardView.vue");
const AdminInstitutionsView = () => import("../views/admin/AdminInstitutionsView.vue");
const AdminInvitesView = () => import("../views/admin/AdminInvitesView.vue");
const AdminUsersView = () => import("../views/admin/AdminUsersView.vue");
const AdminRecordsView = () => import("../views/admin/AdminRecordsView.vue");
const CommentModerationView = () => import("../views/CommentModerationView.vue");

const userMeta = { requiresAuth: true, roles: ["user"], workspace: "user" };
const orgMeta = { requiresAuth: true, roles: ["institution_admin"], workspace: "org" };
const adminMeta = { requiresAuth: true, roles: ["admin"], workspace: "admin" };

const routes = [
  { path: "/", name: "public-home", component: PublicHomeView, meta: { title: "康康健健 HealthDoc" } },
  { path: "/login", name: "login", component: LoginView, meta: { guestOnly: true, title: "登录" } },
  { path: "/register", name: "register", component: RegisterView, meta: { guestOnly: true, title: "注册" } },
  {
    path: "/workspace",
    component: WorkspaceLayout,
    meta: userMeta,
    children: [
      { path: "/dashboard", name: "dashboard", component: UserDashboardView, meta: { title: "健康总览", eyebrow: "个人健康中心" } },
      { path: "/records", name: "records", component: RecordListView, meta: { title: "健康档案", eyebrow: "个人健康中心" } },
      { path: "/records/upload", name: "record-upload", component: RecordOcrUploadView, meta: { title: "OCR 报告录入", eyebrow: "健康档案" } },
      { path: "/records/:id", name: "record-detail", component: RecordDetailView, meta: { title: "档案详情", eyebrow: "健康档案" } },
      { path: "/trends", name: "trends", component: TrendView, meta: { title: "指标趋势", eyebrow: "个人健康中心" } },
      { path: "/friends", name: "friends", component: FriendManageView, meta: { title: "亲友授权", eyebrow: "个人健康中心" } },
      { path: "/institutions", name: "institutions", component: InstitutionListView, meta: { title: "体检机构", eyebrow: "机构服务" } },
      { path: "/institutions/:id", name: "institution-detail", component: InstitutionDetailView, meta: { title: "机构详情", eyebrow: "机构服务" } },
      { path: "/comments/mine", name: "my-comments", component: MyCommentsView, meta: { title: "我的评论", eyebrow: "机构服务" } },
      { path: "/profile", name: "profile", component: HomeView, meta: { title: "个人中心", eyebrow: "账号信息" } },
    ],
  },
  {
    path: "/org",
    component: WorkspaceLayout,
    meta: orgMeta,
    children: [
      { path: "", redirect: { name: "org-dashboard" } },
      { path: "dashboard", name: "org-dashboard", component: OrgDashboardView, meta: { title: "机构运营总览", eyebrow: "机构运营后台" } },
      { path: "profile", name: "org-profile", component: OrgProfileView, meta: { title: "机构资料维护", eyebrow: "机构运营后台" } },
      { path: "gallery", name: "org-gallery", component: OrgGalleryView, meta: { title: "机构相册管理", eyebrow: "机构运营后台" } },
      { path: "packages", name: "org-packages", component: OrgPackagesView, meta: { title: "体检套餐管理", eyebrow: "机构运营后台" } },
      { path: "records", name: "org-records", component: OrgHealthRecordsView, meta: { title: "机构健康档案", eyebrow: "脱敏只读数据" } },
      { path: "trends", name: "org-trends", component: OrgTrendsView, meta: { title: "机构指标趋势", eyebrow: "脱敏只读数据" } },
    ],
  },
  {
    path: "/admin",
    component: WorkspaceLayout,
    meta: adminMeta,
    children: [
      { path: "", redirect: { name: "admin-dashboard" } },
      { path: "dashboard", name: "admin-dashboard", component: AdminDashboardView, meta: { title: "系统运营总览", eyebrow: "系统管理后台" } },
      { path: "institutions", name: "admin-institutions", component: AdminInstitutionsView, meta: { title: "机构与套餐管理", eyebrow: "系统管理后台" } },
      { path: "invites", name: "admin-invites", component: AdminInvitesView, meta: { title: "邀请码管理", eyebrow: "系统管理后台" } },
      { path: "users", name: "admin-users", component: AdminUsersView, meta: { title: "用户与角色管理", eyebrow: "系统管理后台" } },
      { path: "records", name: "admin-records", component: AdminRecordsView, meta: { title: "全局档案监管", eyebrow: "系统管理后台" } },
      { path: "comments", name: "admin-comments", component: CommentModerationView, meta: { title: "评论审核", eyebrow: "系统管理后台" } },
    ],
  },
  { path: "/403", name: "forbidden", component: ForbiddenView, meta: { title: "无权访问" } },
  { path: "/:pathMatch(.*)*", name: "not-found", component: NotFoundView, meta: { title: "页面不存在" } },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition;
    if (to.hash) return { el: to.hash, behavior: "smooth" };
    return { top: 0 };
  },
});

router.beforeEach(async (to) => {
  const authStore = useAuthStore();
  authStore.hydrate();

  if (to.meta.requiresAuth) {
    if (!authStore.accessToken) {
      const refreshed = await authStore.tryRefresh();
      if (!refreshed) return { name: "login", query: { redirect: to.fullPath } };
    }

    const previousRole = authStore.user?.role;
    try {
      const currentUser = await authStore.fetchMe();
      if (previousRole && previousRole !== currentUser.role) {
        return dashboardRouteForRole(currentUser.role);
      }
      if (to.meta.roles?.length && !to.meta.roles.includes(currentUser.role)) {
        return { name: "forbidden" };
      }
    } catch {
      authStore.logout();
      return { name: "login", query: { redirect: to.fullPath } };
    }
  }

  if (to.meta.guestOnly && authStore.accessToken) {
    try {
      const user = await authStore.fetchMe();
      return dashboardRouteForRole(user.role);
    } catch {
      authStore.logout();
    }
  }

  return true;
});

router.afterEach((to) => {
  document.title = `${to.meta.title || "健康档案"} · 康康健健 HealthDoc`;
});

export default router;
