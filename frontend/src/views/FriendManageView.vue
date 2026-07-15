<template>
  <div class="home-shell">
    <el-card class="home-card">
      <template #header>
        <div class="top-bar">
          <span>亲友管理</span>
          <MainNavActions />
        </div>
      </template>

      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        :closable="false"
        style="margin-bottom: 16px"
      />

      <el-card shadow="never" style="margin-bottom: 16px">
        <template #header>
          <span>添加亲友</span>
        </template>

        <div class="friend-add-grid">
          <el-input v-model="addForm.friend_username" placeholder="输入亲友用户名" />
          <el-input v-model="addForm.relation_name" placeholder="关系名称（如：父亲、配偶）" />
          <el-button type="primary" :loading="addLoading" @click="submitAddFriend">添加</el-button>
        </div>
      </el-card>

      <el-skeleton :rows="6" animated v-if="loading" />

      <template v-else>
        <el-card shadow="never" style="margin-bottom: 16px">
          <template #header>
            <span>我申请查看的亲友</span>
          </template>

          <el-table :data="outgoing" border empty-text="暂无亲友关系">
            <el-table-column prop="friend_user.username" label="亲友用户名" min-width="140" />
            <el-table-column prop="relation_name" label="关系名称" min-width="140" />
            <el-table-column label="授权状态" width="120">
              <template #default="scope">
                <el-tag :type="scope.row.auth_status ? 'success' : 'info'">
                  {{ scope.row.auth_status ? "已授权" : "待授权" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="180">
              <template #default="scope">
                <el-button type="primary" link @click="renameRelation(scope.row)">改名</el-button>
                <el-button type="danger" link @click="removeRelation(scope.row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never">
          <template #header>
            <span>申请查看我的亲友（我可开关只读授权）</span>
          </template>

          <el-table :data="incoming" border empty-text="暂无被添加关系">
            <el-table-column prop="user.username" label="请求方用户名" min-width="150" />
            <el-table-column prop="relation_name" label="对方关系名" min-width="140" />
            <el-table-column label="授权开关" width="140">
              <template #default="scope">
                <el-switch
                  :model-value="scope.row.auth_status"
                  :active-value="true"
                  :inactive-value="false"
                  @change="(value) => changeAuthorization(scope.row, value)"
                />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="scope">
                <el-button type="danger" link @click="removeRelation(scope.row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRouter } from "vue-router";

import MainNavActions from "../components/MainNavActions.vue";
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
const errorMessage = ref("");
const outgoing = ref([]);
const incoming = ref([]);

const addForm = reactive({
  friend_username: "",
  relation_name: "亲友",
});

const loadFriends = async () => {
  loading.value = true;
  errorMessage.value = "";

  try {
    const { data } = await fetchFriends();
    outgoing.value = data.outgoing || [];
    incoming.value = data.incoming || [];
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "亲友关系加载失败";
    outgoing.value = [];
    incoming.value = [];
  } finally {
    loading.value = false;
  }
};

const submitAddFriend = async () => {
  if (!addForm.friend_username) {
    ElMessage.error("请输入亲友用户名");
    return;
  }

  addLoading.value = true;

  try {
    await addFriend({
      friend_username: addForm.friend_username.trim(),
      relation_name: addForm.relation_name?.trim() || "亲友",
    });
    addForm.friend_username = "";
    addForm.relation_name = "亲友";
    ElMessage.success("亲友添加成功，等待对方授权");
    await loadFriends();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "亲友添加失败");
  } finally {
    addLoading.value = false;
  }
};

const renameRelation = async (row) => {
  try {
    const { value } = await ElMessageBox.prompt("请输入新的关系名称", "修改关系名称", {
      inputValue: row.relation_name,
      confirmButtonText: "保存",
      cancelButtonText: "取消",
      inputPattern: /.+/,
      inputErrorMessage: "关系名称不能为空",
    });

    await renameFriend(row.id, { relation_name: value });
    ElMessage.success("关系名称已更新");
    await loadFriends();
  } catch (error) {
    if (error === "cancel") {
      return;
    }
    ElMessage.error(error?.response?.data?.message || "关系名称修改失败");
  }
};

const changeAuthorization = async (row, authStatus) => {
  try {
    await updateFriendAuthorization(row.id, { auth_status: authStatus });
    ElMessage.success(authStatus ? "已开启授权" : "已关闭授权");
    await loadFriends();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "授权状态更新失败");
    await loadFriends();
  }
};

const removeRelation = async (row) => {
  try {
    await ElMessageBox.confirm("删除后双方关系将失效，确认继续？", "提示", {
      type: "warning",
      confirmButtonText: "确认删除",
      cancelButtonText: "取消",
    });

    await deleteFriend(row.id);
    ElMessage.success("亲友关系已删除");
    await loadFriends();
  } catch (error) {
    if (error === "cancel") {
      return;
    }
    ElMessage.error(error?.response?.data?.message || "删除失败");
  }
};

const goInstitutions = () => {
  router.push({ name: "institutions" });
};

const goTrends = () => {
  router.push({ name: "trends" });
};

const goCommentModeration = () => {
  router.push({ name: "comment-moderation" });
};

const goProfile = () => {
  router.push({ name: "profile" });
};

const logout = () => {
  authStore.logout();
  router.push({ name: "login" });
};

onMounted(async () => {
  await loadFriends();
});
</script>
