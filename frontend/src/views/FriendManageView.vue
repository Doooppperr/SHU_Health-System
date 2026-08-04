<template>
  <div class="workspace-page user-platform-page">
    <section class="user-page-lead">
      <div>
        <span class="user-kicker">家人协作</span>
        <h2>亲友授权与账号切换</h2>
        <p>对方接受关联后，双方即可互相切换账号查看健康信息，也可以互相代为预约。</p>
      </div>
    </section>

    <el-alert
      title="账号切换期间，页面只展示被授权人的健康信息；随时可从工作台顶部返回自己的账号。"
      type="info"
      show-icon
      :closable="false"
    />
    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />

    <el-card shadow="never" class="friend-add-card">
      <template #header><strong>添加亲友</strong></template>
      <div class="friend-add-grid">
        <el-input v-model.trim="addForm.health_id" placeholder="输入亲友健康身份码" maxlength="32" />
        <el-input v-model.trim="addForm.relation_name" placeholder="我的备注，如：父亲、配偶" />
        <el-button type="primary" :loading="addLoading" @click="submitAddFriend">发送申请</el-button>
      </div>
    </el-card>

    <el-skeleton v-if="loading" :rows="6" animated />
    <template v-else>
      <section class="friend-section">
        <header class="friend-section__heading">
          <div><span class="user-kicker">全部亲友</span><h3>待处理申请与已关联亲友</h3></div>
          <el-tag effect="plain">{{ outgoing.length }} 位</el-tag>
        </header>
        <div v-if="outgoing.length" class="friend-card-grid">
          <article v-for="row in outgoing" :key="row.id" class="friend-card">
            <header>
              <span class="friend-avatar">{{ initial(row) }}</span>
              <div>
                <h4>{{ displayName(row) }}</h4>
                <p>{{ relationLabel(row) }}</p>
              </div>
              <el-tag :type="relationshipMeta(row).type" effect="light">{{ relationshipMeta(row).label }}</el-tag>
            </header>
            <div v-if="isActive(row)" class="friend-card__permission">
              <div class="friend-card__permission-heading">
                <strong>双向关联权限已生效</strong>
                <el-switch
                  :model-value="true"
                  inline-prompt
                  active-text="开"
                  inactive-text="关"
                  :loading="authorizationChangingId === row.id"
                  :aria-label="`关闭与 ${displayName(row)} 的关联授权`"
                  @change="(value) => toggleRelationAuthorization(row, value)"
                />
              </div>
              <small>双方可互相切换账号查看健康信息，并可互相代为预约。</small>
            </div>
            <div v-else-if="isPending(row)" class="friend-card__permission is-pending">
              <div class="friend-card__permission-heading">
                <strong>{{ row.can_accept ? "对方申请与你建立亲友关联" : "已向对方发送亲友关联申请" }}</strong>
                <el-switch
                  :model-value="false"
                  inline-prompt
                  active-text="开"
                  inactive-text="关"
                  :disabled="!row.can_accept"
                  :loading="authorizationChangingId === row.id"
                  :aria-label="row.can_accept ? `接受与 ${displayName(row)} 的关联授权` : '等待对方接受关联授权'"
                  @change="(value) => toggleRelationAuthorization(row, value)"
                />
              </div>
              <small>{{ row.can_accept ? "接受后，双向账号切换和代预约权限将同时生效。" : "等待对方接受；接受前不会开放任何健康数据或预约权限。" }}</small>
            </div>
            <div v-else class="friend-card__permission is-revoked">
              <strong>亲友关联已解除</strong>
              <small>所有账号切换和代预约权限均已失效。如需恢复，请使用对方的健康身份码重新发起申请。</small>
            </div>
            <div v-if="isActive(row)" class="friend-card__actions">
              <el-button
                type="primary"
                :loading="switchingId === row.id"
                :disabled="isVisitedAccount(row) && !isPreviousAccount(row)"
                @click="switchAccount(row)"
              >
                {{ accountSwitchLabel(row) }}
              </el-button>
              <el-button plain @click="helpBook(row)">帮 TA 预约</el-button>
              <el-dropdown trigger="click">
                <el-button text>更多</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item @click="renameRelation(row)">修改我的备注</el-dropdown-item>
                    <el-dropdown-item divided @click="removeRelation(row)">解除亲友关联</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <div v-else-if="isPending(row)" class="friend-card__actions">
              <el-button
                :type="row.can_accept ? 'danger' : 'default'"
                plain
                @click="removeRelation(row)"
              >
                {{ row.can_accept ? "拒绝申请" : "撤回申请" }}
              </el-button>
            </div>
          </article>
        </div>
        <el-empty v-else description="还没有添加亲友" :image-size="84" />
      </section>
    </template>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import {
  addFriend,
  deleteFriend,
  fetchFriends,
  renameFriend,
  updateFriendAuthorization,
} from "../api/friends";
import { useAuthStore } from "../stores/auth";

const router = useRouter();
const authStore = useAuthStore();
const loading = ref(false);
const addLoading = ref(false);
const switchingId = ref(null);
const authorizationChangingId = ref(null);
const errorMessage = ref("");
const outgoing = ref([]);
const addForm = reactive({ health_id: "", relation_name: "亲友" });

function counterpart(row) {
  return row.counterparty || row.friend_user || row.user || {};
}
function displayName(row) {
  const person = counterpart(row);
  return person.display_name || person.real_name || person.username || "亲友";
}
function initial(row) {
  return displayName(row).slice(0, 1);
}
function relationLabel(row) {
  return row.my_remark || row.relation_name || "亲友";
}
function isActive(row) {
  return relationStatus(row) === "active";
}
function isPending(row) {
  return relationStatus(row) === "pending";
}
function relationStatus(row) {
  return row.relationship_status || row.status || (row.auth_status ? "active" : "pending");
}
function relationshipMeta(row) {
  if (isActive(row)) return { label: "已关联", type: "success" };
  if (!isPending(row)) return { label: "已解除", type: "info" };
  if (row.can_accept) return { label: "待你接受", type: "warning" };
  return { label: "等待对方接受", type: "info" };
}

function isVisitedAccount(row) {
  const accountId = Number(counterpart(row).id);
  return (authStore.delegation?.session?.chain || [])
    .map((value) => Number(value))
    .includes(accountId);
}

function isPreviousAccount(row) {
  const chain = (authStore.delegation?.session?.chain || []).map((value) => Number(value));
  return chain.length > 1 && Number(counterpart(row).id) === chain.at(-2);
}

function accountSwitchLabel(row) {
  if (isPreviousAccount(row)) return `返回 ${displayName(row)}`;
  if (isVisitedAccount(row)) return "已在切换链路中";
  return "进入亲友账号";
}

async function loadFriends() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const { data } = await fetchFriends();
    const combined = data.items || [...(data.outgoing || []), ...(data.incoming || [])];
    outgoing.value = [...new Map(combined.map((item) => [item.id, item])).values()];
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "亲友关系加载失败";
  } finally {
    loading.value = false;
  }
}

async function submitAddFriend() {
  if (!addForm.health_id) return ElMessage.warning("请输入亲友健康身份码");
  addLoading.value = true;
  try {
    await addFriend({
      health_id: addForm.health_id.trim(),
      relation_name: addForm.relation_name?.trim() || "亲友",
    });
    Object.assign(addForm, { health_id: "", relation_name: "亲友" });
    ElMessage.success("申请已发送，等待对方授权");
    await loadFriends();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "亲友添加失败");
  } finally {
    addLoading.value = false;
  }
}

async function switchAccount(row) {
  switchingId.value = row.id;
  try {
    if (isPreviousAccount(row)) {
      await authStore.returnToPreviousAccount();
      ElMessage.success(`已返回 ${displayName(row)}`);
    } else {
      await authStore.switchToFriend(row);
      ElMessage.success(`已进入 ${displayName(row)} 的授权账号`);
    }
    await router.push({ name: "timeline" });
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || error?.message || "暂时无法切换账号");
  } finally {
    switchingId.value = null;
  }
}

function helpBook(row) {
  router.push({ name: "appointments", query: { relation_id: row.id } });
}

async function renameRelation(row) {
  try {
    const { value } = await ElMessageBox.prompt("请输入新的亲友备注", "修改备注", {
      inputValue: relationLabel(row),
      confirmButtonText: "保存",
      cancelButtonText: "取消",
      inputPattern: /.+/,
      inputErrorMessage: "备注不能为空",
    });
    await renameFriend(row.id, { relation_name: value });
    ElMessage.success("备注已更新");
    await loadFriends();
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || "备注修改失败");
    }
  }
}

async function toggleRelationAuthorization(row, value) {
  if (!value && !isActive(row)) return;
  authorizationChangingId.value = row.id;
  try {
    if (!value) {
      await ElMessageBox.confirm(
        "关闭后双方的账号切换和代预约权限会立即失效，再次关联必须重新申请并接受。",
        "关闭双向关联授权",
        {
          type: "warning",
          confirmButtonText: "确认关闭",
          cancelButtonText: "保持开启",
        },
      );
    }
    await updateFriendAuthorization(row.id, { auth_status: value });
    ElMessage.success(value
      ? "已建立亲友关联，双向账号切换和代预约权限已生效"
      : "亲友关联已解除，双方授权立即失效");
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || (value ? "接受亲友申请失败" : "关闭关联失败"));
    }
  } finally {
    authorizationChangingId.value = null;
    await loadFriends();
  }
}

async function removeRelation(row) {
  try {
    const active = isActive(row);
    const action = active ? "解除关联" : (row.can_accept ? "拒绝申请" : "撤回申请");
    await ElMessageBox.confirm(
      active
        ? "解除后双方将立即失去账号切换和代预约权限；如需恢复，必须重新发起申请。"
        : `确认${action}？本次亲友申请将被删除。`,
      action,
      {
      type: "warning",
      confirmButtonText: action,
      cancelButtonText: "取消",
      },
    );
    await deleteFriend(row.id);
    ElMessage.success(active ? "亲友关联已解除" : `已${action}`);
    await loadFriends();
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || "删除失败");
    }
  }
}

onMounted(loadFriends);
</script>

<style scoped>
.friend-add-card,
.friend-section {
  margin-top: 18px;
}

.friend-add-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(180px, 0.7fr) auto;
  gap: 12px;
}

.friend-section {
  padding: 22px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 20px;
  background: var(--el-bg-color);
}

.friend-section__heading,
.friend-card > header,
.friend-card__actions {
  display: flex;
  align-items: center;
}

.friend-section__heading {
  justify-content: space-between;
  margin-bottom: 16px;
}

.friend-section__heading h3 {
  margin: 5px 0 0;
}

.friend-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
  gap: 16px;
}

.friend-card {
  display: grid;
  gap: 16px;
  padding: 18px;
  border: 1px solid var(--el-border-color);
  border-radius: 18px;
  background: var(--el-fill-color-blank);
}

.friend-card > header {
  gap: 12px;
}

.friend-card > header > div {
  min-width: 0;
  flex: 1;
}

.friend-card h4,
.friend-card p {
  margin: 0;
}

.friend-card p {
  color: var(--el-text-color-secondary);
}

.friend-card__permission {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 13px 14px;
  border: 1px solid #b9ded5;
  border-radius: 13px;
  background: #eef9f6;
  color: #205f53;
}

.friend-card__permission-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.friend-card__permission.is-pending {
  border-color: var(--el-border-color);
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
}

.friend-card__permission.is-revoked {
  border-color: #e5c7c7;
  background: #fff5f5;
  color: #8b3a3a;
}

.friend-card__permission small {
  color: var(--el-text-color-secondary);
  line-height: 1.55;
}

.friend-avatar {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border-radius: 14px;
  background: #e9f5f1;
  color: #207766;
  font-size: 20px;
  font-weight: 800;
}

.friend-card__actions {
  flex-wrap: wrap;
  gap: 8px;
}

@media (max-width: 720px) {
  .friend-add-grid {
    grid-template-columns: 1fr;
  }
}
</style>
