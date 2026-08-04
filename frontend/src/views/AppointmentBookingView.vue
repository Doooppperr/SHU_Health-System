<template>
  <div class="workspace-page user-platform-page">
    <section class="user-page-lead">
      <div>
        <span class="user-kicker">体检预约</span>
        <h2>为自己和家人安排一次体检</h2>
        <p>先选日期和机构，再确认套餐与受检者。同行人会作为一个预约一起提交，不会出现有人成功、有人失败。</p>
      </div>
    </section>

    <el-alert
      v-if="!profileReady"
      title="完成实名认证后才能预约体检"
      description="请先填写真实姓名、性别和出生日期；提交后身份信息将锁定。"
      type="warning"
      show-icon
      :closable="false"
    >
      <template #default>
        <el-button type="primary" link @click="openProfileGate">立即认证</el-button>
      </template>
    </el-alert>

    <el-card shadow="never" class="user-panel booking-flow-card">
      <el-steps :active="step - 1" finish-status="success" align-center class="booking-steps">
        <el-step title="选择时间与机构" />
        <el-step title="选择体检套餐" />
        <el-step title="确认受检者" />
      </el-steps>

      <section v-if="step === 1" class="booking-step-panel">
        <div class="booking-step-heading"><span>第一步</span><h3>什么时候去，想去哪家机构？</h3><p>预约需至少提前一天，可选择明日至未来 30 天，选定日期后显示当天剩余名额。</p></div>
        <el-form label-position="top">
          <el-form-item label="体检日期" required>
            <el-date-picker v-model="form.appointment_date" type="date" value-format="YYYY-MM-DD" :disabled-date="disabledDate" style="width: 100%" @change="dateChanged" />
          </el-form-item>
          <el-form-item label="搜索体检机构">
            <el-input
              v-model="institutionQuery"
              clearable
              placeholder="输入机构、分院、地区、地址或交通信息"
              @input="searchInstitutions"
            />
          </el-form-item>
          <div v-loading="availabilityLoading" class="booking-institution-grid">
            <button
              v-for="option in availability"
              :key="option.institution.id"
              type="button"
              class="booking-choice-card"
              :class="{ 'is-selected': form.institution_id === option.institution.id, 'is-disabled': option.remaining === 0 }"
              @click="selectInstitution(option)"
            >
              <span class="booking-choice-card__check">{{ form.institution_id === option.institution.id ? "✓" : "院" }}</span>
              <strong>{{ option.institution.name }}</strong>
              <small>{{ option.institution.branch_name }}</small>
              <p>{{ option.remaining == null ? "当天名额充足" : option.remaining ? `当天剩余 ${option.remaining} 个名额` : "当天已约满" }}</p>
            </button>
            <el-empty v-if="!availabilityLoading && !availability.length" description="当天暂时没有可预约机构" />
          </div>
        </el-form>
      </section>

      <section v-else-if="step === 2" class="booking-step-panel">
        <div class="booking-step-heading"><span>第二步</span><h3>选择适合这次需要的套餐</h3><p>{{ selectedInstitution?.institution?.name }} · {{ selectedInstitution?.institution?.branch_name }}</p></div>
        <div class="booking-package-grid">
          <button
            v-for="pkg in selectedInstitution?.packages || []"
            :key="pkg.id"
            type="button"
            class="booking-package-choice"
            :class="{ 'is-selected': form.package_id === pkg.id }"
            @click="form.package_id = pkg.id"
          >
            <div><el-tag effect="plain">{{ packageTypeLabel(pkg.package_type) }}</el-tag><strong>¥ {{ Number(pkg.price || 0).toFixed(0) }}</strong></div>
            <h4>{{ pkg.name }}</h4>
            <p>{{ pkg.audience || genderLabel(pkg.gender_scope) }}</p>
            <div class="journey-domain-list"><span v-for="domain in pkg.domains || []" :key="domain.id">{{ domain.name }}</span></div>
            <small>{{ pkg.focus_area }}</small>
          </button>
        </div>
      </section>

      <section v-else class="booking-step-panel">
        <div class="booking-step-heading"><span>第三步</span><h3>确认谁参加这次体检</h3><p>最多 5 人。关联亲友可直接选择，未关联用户可使用本人提供的健康身份码临时添加。</p></div>
        <el-form label-position="top">
          <el-form-item label="受检者" required>
            <el-select v-model="form.participant_keys" multiple :multiple-limit="5" style="width: 100%" @change="participantChanged">
              <el-option v-for="person in participantOptions" :key="person.key" :label="person.label" :value="person.key" />
            </el-select>
          </el-form-item>
          <div class="health-code-participant">
            <div>
              <strong>使用健康身份码添加受检者</strong>
              <small>适用于对方主动提供身份码的场景。验证后仅展示姓名、性别、出生年份和脱敏身份码，不会建立亲友关系或开放健康数据。</small>
            </div>
            <div>
              <el-input v-model.trim="healthIdInput" maxlength="32" placeholder="输入健康身份码" />
              <el-button :loading="resolvingParticipant" @click="resolveHealthIdParticipant">验证并添加</el-button>
            </div>
          </div>
          <el-alert
            title="隐私提示：可复用最近记录时，平台只提示“记录可用”而不向预约人展示数值；你也可以改为填写本次手工快照，且该值不会写入日常测量。"
            type="info"
            show-icon
            :closable="false"
            style="margin-bottom: 14px"
          />
          <div class="participant-intake-grid">
            <el-card v-for="person in selectedParticipants" :key="person.key" shadow="never">
              <template #header><strong>{{ person.label }}</strong></template>
              <div v-if="person.kind === 'health_code_token'" class="health-code-identity-summary">
                <span><small>姓名</small><strong>{{ person.real_name }}</strong></span>
                <span><small>性别</small><strong>{{ identityGenderLabel(person.gender) }}</strong></span>
                <span><small>出生年份</small><strong>{{ person.birth_year || "未记录" }}</strong></span>
                <span><small>健康身份码</small><strong>{{ person.masked_health_id || "已脱敏" }}</strong></span>
              </div>
              <el-row :gutter="12">
                <el-col :xs="24" :sm="12">
                  <div v-if="person.has_recent_height && !participantIntakes[person.key].manual_height" class="private-intake-ready">
                    <span>身高</span>
                    <el-tag type="success">使用最近记录（本次不展示数值）</el-tag>
                    <el-button link type="primary" @click="setManualIntake(person.key, 'height', true)">改为本次手工填写</el-button>
                  </div>
                  <el-form-item v-else label="身高（cm）" required>
                    <div class="manual-intake-field">
                      <el-input-number v-model="participantIntakes[person.key].height_cm" :min="80" :max="250" :precision="1" controls-position="right" style="width: 100%" />
                      <el-button v-if="person.has_recent_height" link type="primary" @click="setManualIntake(person.key, 'height', false)">改用最近记录</el-button>
                    </div>
                  </el-form-item>
                </el-col>
                <el-col :xs="24" :sm="12">
                  <div v-if="person.has_recent_weight && !participantIntakes[person.key].manual_weight" class="private-intake-ready">
                    <span>体重</span>
                    <el-tag type="success">使用最近记录（本次不展示数值）</el-tag>
                    <el-button link type="primary" @click="setManualIntake(person.key, 'weight', true)">改为本次手工填写</el-button>
                  </div>
                  <el-form-item v-else label="体重（kg）" required>
                    <div class="manual-intake-field">
                      <el-input-number v-model="participantIntakes[person.key].weight_kg" :min="20" :max="300" :precision="1" controls-position="right" style="width: 100%" />
                      <el-button v-if="person.has_recent_weight" link type="primary" @click="setManualIntake(person.key, 'weight', false)">改用最近记录</el-button>
                    </div>
                  </el-form-item>
                </el-col>
              </el-row>
              <small v-if="person.kind === 'health_code_token'">平台只会在服务端采用可用的最近记录，不会向预约人展示历史数值；手工值仅用于本次预约。</small>
              <small v-else-if="person.kind === 'linked_account'">手工填写的身高体重只保存为本次预约快照，不会写入对方的日常测量。</small>
            </el-card>
          </div>
        </el-form>

        <div v-if="selectedPackage" class="booking-review-card">
          <div><span>预约日期</span><strong>{{ formatDate(form.appointment_date) }}</strong></div>
          <div><span>体检机构</span><strong>{{ selectedInstitution?.institution?.name }} · {{ selectedInstitution?.institution?.branch_name }}</strong></div>
          <div><span>体检套餐</span><strong>{{ selectedPackage.name }} · ¥ {{ Number(selectedPackage.price || 0).toFixed(0) }} / 人</strong></div>
          <div><span>受检人数</span><strong>{{ form.participant_keys.length }} 人</strong></div>
        </div>

        <el-alert v-if="selectedPackage" type="info" :closable="false" show-icon>
          <template #title>预约前请确认</template>
          <p>{{ selectedPackage.booking_notice || "具体检查安排和注意事项以机构现场说明为准。" }}</p>
          <p>请携带：身份证原件、HealthDoc 预约凭证、病历本、既往体检报告或影像资料、正在使用的药物清单。</p>
          <p>{{ selectedInstitution?.institution?.address }} · 咨询电话 {{ selectedInstitution?.institution?.consult_phone || "请通过平台联系机构" }}</p>
        </el-alert>
        <el-checkbox v-model="form.notice_confirmed" class="booking-notice-check">我已阅读并确认上述预约与检查须知</el-checkbox>
        <el-alert v-if="selectedInstitution" :type="enough ? 'success' : 'warning'" :closable="false" :title="quotaText" show-icon />
      </section>

      <footer class="booking-flow-actions">
        <el-button v-if="step > 1" @click="step -= 1">上一步</el-button>
        <span></span>
        <el-button v-if="step < 3" type="primary" :disabled="!canContinue || !profileReady" @click="step += 1">继续</el-button>
        <template v-else>
          <el-button v-if="!enough" @click="joinWaitlist">到空位时提醒我</el-button>
          <el-button type="primary" :disabled="!canBook" :loading="submitting" @click="book">确认预约</el-button>
        </template>
      </footer>
    </el-card>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />

    <el-card shadow="never" class="user-panel subject-appointment-panel">
      <template #header>
        <div class="user-section-heading">
          <div><span>受检者视角</span><h3>我的受检预约</h3></div>
          <small>共 {{ appointmentPagination.total }} 条</small>
        </div>
      </template>
      <el-alert
        title="这里只显示你本人作为受检者的正式预约，包括他人为你代约的记录。取消与投诉均由你本人处理。"
        type="info"
        show-icon
        :closable="false"
        style="margin-bottom: 16px"
      />
      <div class="booking-record-list">
        <article v-for="appointment in myAppointments" :key="appointment.id" class="booking-record-card">
          <div class="booking-record-card__date">
            <strong>{{ dayOfMonth(appointment.appointment_date) }}</strong>
            <span>{{ monthLabel(appointment.appointment_date) }}</span>
          </div>
          <div class="booking-record-card__body">
            <div><el-tag :type="appointmentMeta(appointment.status).type" effect="light">{{ appointmentMeta(appointment.status).label }}</el-tag></div>
            <h4>{{ appointment.package_name || appointment.package?.name || "体检套餐" }}</h4>
            <p>{{ appointment.institution?.name }} · {{ appointment.institution?.branch_name }}</p>
            <AppointmentProgress :appointment="appointment" />
            <small v-if="appointment.booked_by_user_id && appointment.booked_by_user_id !== auth.user?.id">
              本次预约由已授权亲友代为提交
            </small>
          </div>
          <div class="booking-record-card__actions">
            <el-button
              size="small"
              plain
              :type="complaintForAppointment(appointment.id) ? 'primary' : 'danger'"
              @click="handleComplaintAction(appointment)"
            >
              {{ complaintForAppointment(appointment.id) ? "查看投诉" : "投诉机构" }}
            </el-button>
            <el-button
              v-if="appointment.status === 'unfulfilled'"
              size="small"
              link
              type="danger"
              @click="cancelOwnAppointment(appointment)"
            >
              取消本人预约
            </el-button>
          </div>
        </article>
        <el-empty v-if="!myAppointments.length" description="还没有本人受检预约" :image-size="80" />
      </div>
      <footer v-if="appointmentPagination.total > 0" class="booking-pagination">
        <span class="booking-pagination__summary">
          第 {{ appointmentPagination.page }} / {{ Math.max(appointmentPagination.pages, 1) }} 页 · 每页 {{ appointmentPagination.page_size }} 条
        </span>
        <el-pagination
          v-model:current-page="appointmentPagination.page"
          :page-size="appointmentPagination.page_size"
          :total="appointmentPagination.total"
          :pager-count="5"
          layout="prev, pager, next"
          @current-change="loadMyAppointments"
        />
      </footer>
    </el-card>

    <section class="booking-management-grid">
      <el-card shadow="never" class="user-panel">
        <template #header><div class="user-section-heading"><div><span>发起人视角</span><h3>我发起的代预约回执</h3></div><small>共 {{ groupPagination.total }} 组</small></div></template>
        <el-alert
          title="回执仅展示脱敏受检者与预约状态。投诉必须由受检者登录本人账号后提交。"
          type="info"
          show-icon
          :closable="false"
          style="margin-bottom: 14px"
        />
        <div class="booking-history-filters">
          <el-select v-model="historyPreset" style="width: 150px" @change="applyHistoryPreset">
            <el-option label="全部记录" value="all" />
            <el-option label="近一周" value="week" />
            <el-option label="近一月" value="month" />
            <el-option label="近半年" value="half_year" />
            <el-option label="自定义范围" value="custom" />
          </el-select>
          <el-date-picker
            v-if="historyPreset === 'custom'"
            v-model="historyRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            @change="historyRangeChanged"
          />
        </div>
        <div class="booking-record-list">
          <article v-for="group in groups" :key="group.id" class="booking-record-card">
            <div class="booking-record-card__date"><strong>{{ dayOfMonth(group.appointment_date) }}</strong><span>{{ monthLabel(group.appointment_date) }}</span></div>
            <div class="booking-record-card__body">
              <div><el-tag v-for="status in group.status_codes || []" :key="status" :type="appointmentMeta(status).type" effect="light">{{ appointmentMeta(status).label }}</el-tag></div>
              <h4>{{ group.package?.name || group.package_name || group.package_name_snapshot }}</h4>
              <p>{{ group.institution?.name }} · {{ group.institution?.branch_name }}</p>
              <div class="appointment-participant-list">
                <article v-for="appointment in groupAppointments(group)" :key="appointment.id" class="appointment-participant-card">
                  <header>
                    <div>
                      <strong>{{ appointment.user?.display_name || appointment.user?.name || appointment.subject_name_snapshot || "受检者" }}</strong>
                      <small>{{ participantTypeLabel(appointment.participant_type) }} · {{ appointmentMeta(appointment.status).label }}</small>
                    </div>
                    <el-button
                      v-if="appointment.can_cancel"
                      size="small"
                      link
                      type="danger"
                      @click="cancelGroupMember(appointment)"
                    >
                      取消该成员
                    </el-button>
                  </header>
                  <AppointmentProgress :appointment="{ ...appointment, appointment_date: group.appointment_date }" />
                </article>
              </div>
            </div>
            <el-button v-if="group.can_cancel" link type="danger" @click="cancelGroup(group)">取消整组</el-button>
          </article>
          <el-empty v-if="!groups.length" description="还没有预约记录" :image-size="80" />
        </div>
        <footer v-if="groupPagination.total > 0" class="booking-pagination">
          <span class="booking-pagination__summary">
            第 {{ groupPagination.page }} / {{ Math.max(groupPagination.pages, 1) }} 页 · 每页 {{ groupPagination.page_size }} 组
          </span>
          <el-pagination
            v-model:current-page="groupPagination.page"
            :page-size="groupPagination.page_size"
            :total="groupPagination.total"
            :pager-count="5"
            layout="prev, pager, next"
            @current-change="loadGroups"
          />
        </footer>
      </el-card>

      <el-card shadow="never" class="user-panel">
        <template #header><div class="user-section-heading"><div><span>空位动态</span><h3>我的提醒</h3></div><small>{{ activeWaitlistCount }} 条生效中</small></div></template>
        <div class="waitlist-card-list">
          <article v-for="item in waitlists" :key="item.id" class="waitlist-card">
            <div><el-tag :type="item.status === 'active' ? 'warning' : 'info'" effect="light">{{ item.status_label || WAITLIST_STATUS[item.status] || "状态更新中" }}</el-tag><h4>{{ item.package?.name }}</h4><p>{{ item.institution?.name }} · {{ formatDate(item.appointment_date) }}</p></div>
            <el-button v-if="item.status === 'active'" link type="danger" @click="cancelWaitlist(item)">取消提醒</el-button>
          </article>
          <el-empty v-if="!waitlists.length" description="没有空位提醒" :image-size="80" />
        </div>
        <el-pagination
          v-if="waitlistPagination.total > waitlistPagination.page_size"
          v-model:current-page="waitlistPagination.page"
          :page-size="waitlistPagination.page_size"
          :total="waitlistPagination.total"
          layout="prev, pager, next"
          @current-change="loadWaitlists"
        />
      </el-card>
    </section>

    <el-card shadow="never" class="user-panel complaint-history-panel">
      <template #header>
        <div class="user-section-heading">
          <div><span>服务反馈</span><h3>我的投诉</h3></div>
          <small>{{ complaintPagination.total }} 条</small>
        </div>
      </template>
      <div class="complaint-card-list">
        <article v-for="item in complaints" :id="`complaint-${item.id}`" :key="item.id" class="complaint-card">
          <header>
            <div>
              <strong>{{ item.institution?.name || item.institution_name || "体检机构" }}</strong>
              <small>{{ item.created_at || item.submitted_at }}</small>
            </div>
            <el-tag :type="complaintMeta(item.status).type">{{ complaintMeta(item.status).label }}</el-tag>
          </header>
          <h4>{{ item.category_label || complaintCategoryLabel(item.category) }}</h4>
          <p>{{ item.content || item.description }}</p>
          <div v-if="item.institution_reply" class="complaint-reply"><strong>机构回复</strong><p>{{ item.institution_reply }}</p></div>
          <div v-if="item.admin_reply" class="complaint-reply"><strong>平台回复</strong><p>{{ item.admin_reply }}</p></div>
          <el-timeline v-if="conversationMessages(item).length" class="complaint-message-timeline">
            <el-timeline-item
              v-for="message in conversationMessages(item)"
              :key="message.id || `${message.sender_role}-${message.created_at}`"
              :timestamp="formatDateTime(message.created_at)"
            >
              <strong>{{ complaintSenderLabel(message.sender_role) }}</strong>
              <p>{{ message.content }}</p>
            </el-timeline-item>
          </el-timeline>
          <footer v-if="['institution_pending', 'user_confirmation', 'awaiting_user_confirmation', 'institution_replied'].includes(item.status)">
            <el-button v-if="['user_confirmation', 'awaiting_user_confirmation', 'institution_replied'].includes(item.status)" type="success" plain @click="confirmComplaint(item)">确认已解决</el-button>
            <el-button type="danger" plain @click="escalateComplaintItem(item)">申请平台处理</el-button>
          </footer>
        </article>
        <el-empty v-if="!complaints.length" description="暂无投诉记录" :image-size="80" />
      </div>
      <footer v-if="complaintPagination.total > complaintPagination.page_size" class="booking-pagination">
        <span class="booking-pagination__summary">
          第 {{ complaintPagination.page }} / {{ Math.max(complaintPagination.pages, 1) }} 页 · 每页 {{ complaintPagination.page_size }} 条
        </span>
        <el-pagination
          v-model:current-page="complaintPagination.page"
          :page-size="complaintPagination.page_size"
          :total="complaintPagination.total"
          :pager-count="5"
          layout="prev, pager, next"
        />
      </footer>
    </el-card>

    <el-dialog v-model="complaintDialogVisible" title="提交机构投诉" width="min(540px, 92vw)">
      <el-alert
        :title="`${complaintForm.institution_name || '该机构'} · ${complaintForm.subject_name || '本次预约'}`"
        type="info"
        :closable="false"
        style="margin-bottom: 16px"
      />
      <el-form label-position="top">
        <el-form-item label="投诉类型" required>
          <el-select v-model="complaintForm.category" style="width: 100%">
            <el-option label="服务态度" value="service" />
            <el-option label="预约与到检安排" value="appointment" />
            <el-option label="报告质量或时效" value="report" />
            <el-option label="医疗服务质量" value="medical_quality" />
            <el-option label="隐私问题" value="privacy" />
            <el-option label="其他问题" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="问题说明" required>
          <el-input v-model.trim="complaintForm.content" type="textarea" :rows="5" maxlength="1000" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="complaintDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="complaintSubmitting" @click="submitComplaint">提交投诉</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="receiptVisible" title="预约成功" width="min(560px, 92vw)">
      <el-result icon="success" title="预约已提交">
        <template #sub-title>
          <p>{{ receiptSummary }}</p>
          <p>预约编号：{{ bookingReceipt?.display_id || bookingReceipt?.id || "已生成" }}</p>
        </template>
      </el-result>
      <template #footer><el-button type="primary" @click="receiptVisible = false">查看预约进度</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useRoute } from "vue-router";
import {
  cancelAppointment,
  cancelBookingGroup,
  cancelWaitlistSubscription,
  createBookingGroup,
  createWaitlistSubscription,
  fetchAppointmentAvailability,
  fetchBookingIntakeDefaults,
  fetchBookingGroups,
  fetchMyAppointments,
  fetchWaitlistSubscriptions,
  resolveBookingParticipantToken,
} from "../api/appointments";
import { fetchFriends } from "../api/friends";
import {
  confirmComplaintResolved,
  createComplaint,
  escalateComplaint,
  fetchMyComplaints,
} from "../api/complaints";
import { useAuthStore } from "../stores/auth";
import AppointmentProgress from "../components/AppointmentProgress.vue";
import {
  bookingDateBounds,
  businessDateString,
  complaintMeta,
  isBasicProfileComplete,
  isBookingDateDisabled,
  normalizeAppointmentParticipants,
} from "../utils/v12";
import {
  WAITLIST_STATUS,
  appointmentMeta,
  formatDate,
  formatDateTime,
  genderLabel,
  packageTypeLabel,
} from "../utils/userPlatform";

const route = useRoute();
const auth = useAuthStore();
const step = ref(1);
const availability = ref([]);
const groups = ref([]);
const myAppointments = ref([]);
const waitlists = ref([]);
const allComplaints = ref([]);
const relations = ref([]);
const tokenParticipants = ref([]);
let tokenParticipantSequence = 0;
const submitting = ref(false);
const resolvingParticipant = ref(false);
const complaintSubmitting = ref(false);
const availabilityLoading = ref(false);
const errorMessage = ref("");
const institutionQuery = ref("");
const healthIdInput = ref("");
const participantIntakes = reactive({});
const selfRecent = reactive({ height: false, weight: false });
const complaintDialogVisible = ref(false);
const receiptVisible = ref(false);
const bookingReceipt = ref(null);
const complaintForm = reactive({
  appointment_id: null,
  category: "service",
  content: "",
  institution_name: "",
  subject_name: "",
});
const historyPreset = ref("all");
const historyRange = ref([]);
const groupPagination = reactive({ page: 1, page_size: 10, total: 0, pages: 0 });
const appointmentPagination = reactive({ page: 1, page_size: 10, total: 0, pages: 0 });
const waitlistPagination = reactive({ page: 1, page_size: 15, total: 0, pages: 0 });
const complaintPagination = reactive({ page: 1, page_size: 10, total: 0, pages: 0 });
const waitlistActiveCount = ref(0);
let searchTimer;
const dateBounds = bookingDateBounds();
const requestedAppointmentDate = String(
  route.query.appointment_date || route.query.date || "",
).trim();

function localDate() {
  return businessDateString();
}

const form = reactive({
  appointment_date:
    /^\d{4}-\d{2}-\d{2}$/.test(requestedAppointmentDate)
      && !isBookingDateDisabled(new Date(`${requestedAppointmentDate}T00:00:00`))
      ? requestedAppointmentDate
      : dateBounds.minString,
  institution_id: route.query.institution_id ? Number(route.query.institution_id) : null,
  package_id: route.query.package_id ? Number(route.query.package_id) : null,
  participant_keys: [],
  notice_confirmed: false,
});
const selectedInstitution = computed(() => availability.value.find((item) => item.institution.id === form.institution_id));
const selectedPackage = computed(() => selectedInstitution.value?.packages.find((item) => item.id === form.package_id));
const profileReady = computed(() => isBasicProfileComplete(auth.user || {}));
function relationCanBook(item) {
  return Boolean(
    item.booking_granted_to_me
    ?? item.booking_auth_status
    ?? item.booking_authorized
    ?? item.permissions?.booking
  );
}
const participantOptions = computed(() => [
  {
    key: `self:${auth.user?.id || "me"}`,
    kind: "self",
    user_id: auth.user?.id,
    has_recent_height: selfRecent.height,
    has_recent_weight: selfRecent.weight,
    label: `我本人（${auth.user?.real_name || auth.user?.username || "当前用户"}）`,
  },
  ...relations.value
    .filter(relationCanBook)
    .map((item) => {
      const person = item.counterparty || item.friend_user || {};
      return {
        key: `relation:${item.id}`,
        kind: "linked_account",
        relation_id: item.id,
        user_id: person.id,
        label: `${person.display_name || person.real_name || person.username || "亲友"}（已授权代预约）`,
      };
    }),
  ...tokenParticipants.value,
]);
const selectedParticipants = computed(() => participantOptions.value.filter((item) => form.participant_keys.includes(item.key)));
const intakesComplete = computed(() => selectedParticipants.value.every((person) => {
  const intake = participantIntakes[person.key] || {};
  const heightReady = (person.has_recent_height && !intake.manual_height)
    || (Number(intake.height_cm) >= 80 && Number(intake.height_cm) <= 250);
  const weightReady = (person.has_recent_weight && !intake.manual_weight)
    || (Number(intake.weight_kg) >= 20 && Number(intake.weight_kg) <= 300);
  return heightReady && weightReady;
}));
const remaining = computed(() => selectedInstitution.value?.remaining);
const enough = computed(() => remaining.value == null || remaining.value >= form.participant_keys.length);
const quotaText = computed(() => {
  if (remaining.value == null) return "当天名额充足，可以提交预约";
  if (enough.value) return `当天还剩 ${remaining.value} 个名额，可以容纳当前 ${form.participant_keys.length} 人`;
  return `当天只剩 ${remaining.value} 个名额，暂时无法容纳当前 ${form.participant_keys.length} 人`;
});
const canContinue = computed(() => step.value === 1
  ? Boolean(form.appointment_date && form.institution_id)
  : Boolean(form.package_id));
const canBook = computed(() => Boolean(
  form.appointment_date
  && form.institution_id
  && form.package_id
  && profileReady.value
  && form.participant_keys.length
  && intakesComplete.value
  && form.notice_confirmed
  && enough.value
));
const activeWaitlistCount = computed(() => waitlistActiveCount.value);
const complaints = computed(() => {
  const start = (complaintPagination.page - 1) * complaintPagination.page_size;
  return allComplaints.value.slice(start, start + complaintPagination.page_size);
});
const receiptSummary = computed(() => {
  const people = selectedParticipants.value;
  const proxyCount = people.filter((person) => person.kind !== "self").length;
  if (!proxyCount) return "你的个人预约已成功，后续进度会显示在预约记录中。";
  const selfIncluded = people.some((person) => person.kind === "self");
  return `${selfIncluded ? "本人预约与" : ""}${proxyCount} 位代预约受检者的预约已成功，所有受检者均已生成独立进度。`;
});

function disabledDate(value) {
  return isBookingDateDisabled(value);
}

function dayOfMonth(value) {
  return new Date(`${value}T00:00:00`).getDate();
}

function monthLabel(value) {
  return new Date(`${value}T00:00:00`).toLocaleDateString("zh-CN", { month: "short" });
}

function selectInstitution(option) {
  form.institution_id = option.institution.id;
  if (!option.packages?.some((item) => item.id === form.package_id)) form.package_id = null;
}

async function dateChanged() {
  form.institution_id = null;
  form.package_id = null;
  await loadAvailability();
}

async function loadAvailability() {
  availabilityLoading.value = true;
  try {
    availability.value = (await fetchAppointmentAvailability(form.appointment_date, institutionQuery.value.trim())).data.items || [];
    if (form.institution_id && !selectedInstitution.value) {
      form.institution_id = null;
      form.package_id = null;
    }
  } finally {
    availabilityLoading.value = false;
  }
}

function searchInstitutions() {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(loadAvailability, 300);
}

function participantChanged(keys) {
  for (const key of keys) {
    if (!participantIntakes[key]) {
      const person = participantOptions.value.find((item) => item.key === key) || {};
      participantIntakes[key] = {
        height_cm: null,
        weight_kg: null,
        manual_height: !person.has_recent_height,
        manual_weight: !person.has_recent_weight,
      };
    }
  }
}

function setManualIntake(key, dimension, enabled) {
  const intake = participantIntakes[key];
  if (!intake) return;
  intake[`manual_${dimension}`] = enabled;
}

function openProfileGate() {
  window.dispatchEvent(new CustomEvent("healthdoc-open-profile-gate"));
}

async function resolveHealthIdParticipant() {
  if (!healthIdInput.value) {
    ElMessage.warning("请输入健康身份码");
    return;
  }
  resolvingParticipant.value = true;
  try {
    const { data } = await resolveBookingParticipantToken(healthIdInput.value.trim());
    const item = data.item || data.participant || data;
    const canonicalKey = item.participant_type === "self"
      ? `self:${auth.user?.id || "me"}`
      : item.participant_type === "linked_account" && item.relation_id
        ? `relation:${item.relation_id}`
        : null;
    if (canonicalKey) {
      if (!participantOptions.value.some((person) => person.key === canonicalKey)) {
        throw new Error("对应的关联账号已失效，请刷新页面后重试");
      }
      if (!form.participant_keys.includes(canonicalKey)) {
        if (form.participant_keys.length >= 5) {
          ElMessage.warning("每次最多预约 5 位受检者");
          return;
        }
        form.participant_keys.push(canonicalKey);
        participantChanged(form.participant_keys);
      }
      healthIdInput.value = "";
      ElMessage.info(
        item.participant_type === "self"
          ? "该身份码属于当前账号，已按本人参与去重"
          : "该受检者已是关联亲友，已按关联账号参与去重",
      );
      return;
    }
    const participantToken = data.participant_token || item.participant_token || item.token;
    if (!participantToken) throw new Error("未返回一次性预约凭证");
    const identityKey = [item.real_name, item.birth_year, item.masked_health_id].join("|");
    const existing = tokenParticipants.value.find((person) => person.identity_key === identityKey);
    if (existing) {
      if (!form.participant_keys.includes(existing.key)) {
        form.participant_keys.push(existing.key);
        participantChanged(form.participant_keys);
      }
      ElMessage.info("该受检者已在本次预约中");
      return;
    }
    if (form.participant_keys.length >= 5) {
      ElMessage.warning("每次最多预约 5 位受检者");
      return;
    }
    // Keep the short-lived server token out of DOM values and component keys.
    // It remains only in the in-memory participant object until final submission.
    const key = `health-code-participant:${++tokenParticipantSequence}`;
    tokenParticipants.value.push({
      key,
      kind: "health_code_token",
      participant_token: participantToken,
      identity_key: identityKey,
      real_name: item.real_name || item.display_name || item.masked_name || "身份码受检者",
      gender: item.gender,
      birth_year: item.birth_year,
      masked_health_id: item.masked_health_id,
      has_recent_height: Boolean(item.has_recent_height),
      has_recent_weight: Boolean(item.has_recent_weight),
      label: `${item.real_name || item.display_name || item.masked_name || "身份码受检者"}（身份码验证）`,
    });
    form.participant_keys.push(key);
    participantChanged(form.participant_keys);
    healthIdInput.value = "";
    ElMessage.success("受检者已添加，平台未向你展示其健康数据");
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || error?.message || "健康身份码验证失败");
  } finally {
    resolvingParticipant.value = false;
  }
}

function identityGenderLabel(value) {
  return { male: "男性", female: "女性", other: "其他", undisclosed: "未公开" }[value] || "未记录";
}

function dateOffset(days) {
  const value = new Date();
  value.setHours(0, 0, 0, 0);
  value.setDate(value.getDate() - days);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

async function applyHistoryPreset() {
  historyRange.value = [];
  groupPagination.page = 1;
  await loadGroups();
}

async function historyRangeChanged() {
  groupPagination.page = 1;
  await loadGroups();
}

function historyParams() {
  const params = { page: groupPagination.page, page_size: 10 };
  const offsets = { week: 7, month: 30, half_year: 183 };
  if (offsets[historyPreset.value]) {
    params.start_date = dateOffset(offsets[historyPreset.value]);
    params.end_date = localDate();
  } else if (historyPreset.value === "custom" && historyRange.value?.length === 2) {
    [params.start_date, params.end_date] = historyRange.value;
  }
  return params;
}

async function loadGroups() {
  const response = await fetchBookingGroups(historyParams());
  groups.value = response.data.items || [];
  Object.assign(groupPagination, response.data.pagination || { page: 1, page_size: 10, total: groups.value.length, pages: 1 });
  if (!groups.value.length && groupPagination.total > 0 && groupPagination.page > groupPagination.pages) {
    groupPagination.page = Math.max(groupPagination.pages, 1);
    await loadGroups();
  }
}

async function loadWaitlists() {
  const response = await fetchWaitlistSubscriptions({ page: waitlistPagination.page, page_size: 15 });
  waitlists.value = response.data.items || [];
  waitlistActiveCount.value = Number(response.data.active_count || 0);
  Object.assign(waitlistPagination, response.data.pagination || { page: 1, page_size: 15, total: waitlists.value.length, pages: 1 });
}

async function loadMyAppointments() {
  const response = await fetchMyAppointments({
    page: appointmentPagination.page,
    page_size: appointmentPagination.page_size,
  });
  myAppointments.value = response.data.items || [];
  Object.assign(
    appointmentPagination,
    response.data.pagination || {
      page: 1,
      page_size: appointmentPagination.page_size,
      total: myAppointments.value.length,
      pages: myAppointments.value.length ? 1 : 0,
    },
  );
  if (
    !myAppointments.value.length
    && appointmentPagination.total > 0
    && appointmentPagination.page > appointmentPagination.pages
  ) {
    appointmentPagination.page = Math.max(appointmentPagination.pages, 1);
    await loadMyAppointments();
  }
}

async function loadComplaints() {
  // The API deliberately caps a page at 100 rows.  Fetch every server page so
  // an older complaint still marks its appointment and notification deep
  // links never disappear behind the former first-50 limit.  Rendering remains
  // locally paginated to keep the page compact.
  const first = await fetchMyComplaints({ page: 1, page_size: 100 });
  const firstItems = first.data.items || [];
  const serverPages = Math.max(Number(first.data.pagination?.pages || 1), 1);
  const remainingResponses = serverPages > 1
    ? await Promise.all(
      Array.from({ length: serverPages - 1 }, (_, index) => (
        fetchMyComplaints({ page: index + 2, page_size: 100 })
      )),
    )
    : [];
  allComplaints.value = [
    ...firstItems,
    ...remainingResponses.flatMap((response) => response.data.items || []),
  ];
  complaintPagination.total = allComplaints.value.length;
  complaintPagination.pages = Math.ceil(
    complaintPagination.total / complaintPagination.page_size,
  );
  if (complaintPagination.page > Math.max(complaintPagination.pages, 1)) {
    complaintPagination.page = Math.max(complaintPagination.pages, 1);
  }
}

async function reload() {
  await Promise.all([loadMyAppointments(), loadGroups(), loadWaitlists(), loadComplaints()]);
}

function payload() {
  const participants = selectedParticipants.value.map((person) => {
    const intake = participantIntakes[person.key] || {};
    return {
      type: person.kind,
      kind: person.kind,
      user_id: person.user_id,
      relation_id: person.relation_id,
      participant_token: person.participant_token,
      height_cm: intake.manual_height || !person.has_recent_height ? intake.height_cm : null,
      weight_kg: intake.manual_weight || !person.has_recent_weight ? intake.weight_kg : null,
    };
  });
  return {
    appointment_date: form.appointment_date,
    institution_id: form.institution_id,
    package_id: form.package_id,
    notice_confirmed: form.notice_confirmed,
    participants,
  };
}

function resetParticipantsAfterBooking() {
  tokenParticipants.value = [];
  healthIdInput.value = "";
  Object.keys(participantIntakes).forEach((key) => {
    delete participantIntakes[key];
  });
  const selfKey = `self:${auth.user?.id || "me"}`;
  form.participant_keys = [selfKey];
  participantChanged(form.participant_keys);
}

async function book() {
  submitting.value = true;
  try {
    const { data } = await createBookingGroup(payload());
    bookingReceipt.value = data.item || data.group || data;
    receiptVisible.value = true;
    ElMessage.success("预约成功，所有受检者都已加入本次安排");
    step.value = 1;
    form.notice_confirmed = false;
    resetParticipantsAfterBooking();
    await Promise.all([loadAvailability(), reload()]);
  } catch (error) {
    const data = error?.response?.data || {};
    if (data.code === "APPOINTMENT_DATE_CONFLICT") {
      const names = (data.conflicts || []).map((item) => item.display_name).filter(Boolean);
      const detail = names.length ? `受检者：${names.join("、")}。` : "";
      await ElMessageBox.alert(
        `${detail}${data.message || "当天已有预约，请先查看原预约后再选择其他日期"}`,
        "当天已有预约",
        { type: "warning", confirmButtonText: "我知道了" },
      );
    } else {
      ElMessage.error(data.message || "预约没有提交成功，请稍后重试");
    }
    await loadAvailability();
  } finally {
    submitting.value = false;
  }
}

function groupAppointments(group) {
  return normalizeAppointmentParticipants(group);
}

function participantTypeLabel(type) {
  return {
    self: "本人",
    linked_account: "关联亲友",
    friend: "关联亲友",
    health_code_token: "身份码受检者",
    health_code: "身份码受检者",
  }[type] || "受检者";
}

function complaintCategoryLabel(category) {
  return {
    service: "服务态度",
    appointment: "预约与到检安排",
    report: "报告质量或时效",
    medical_quality: "医疗服务质量",
    privacy: "隐私问题",
    other: "其他问题",
  }[category] || "服务投诉";
}

function openComplaint(appointment, group) {
  Object.assign(complaintForm, {
    appointment_id: appointment.id,
    category: "service",
    content: "",
    institution_name: group.institution?.name || "",
    subject_name:
      appointment.user?.display_name
      || appointment.user?.name
      || appointment.subject_name_snapshot
      || auth.user?.real_name
      || auth.user?.display_name
      || "",
  });
  complaintDialogVisible.value = true;
}

function complaintForAppointment(appointmentId) {
  return allComplaints.value.find((item) => Number(item.appointment_id || item.appointment?.id) === Number(appointmentId));
}

function focusComplaint(item) {
  const index = allComplaints.value.findIndex((candidate) => candidate.id === item.id);
  if (index >= 0) {
    complaintPagination.page = Math.floor(index / complaintPagination.page_size) + 1;
  }
  nextTick(() => {
    document.getElementById(`complaint-${item.id}`)?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  });
}

function focusComplaintById(value) {
  const complaintId = Number(value);
  if (!complaintId) return;
  const requested = allComplaints.value.find(
    (item) => Number(item.id) === complaintId,
  );
  if (requested) focusComplaint(requested);
}

function handleComplaintAction(appointment) {
  const existing = complaintForAppointment(appointment.id);
  if (existing) {
    focusComplaint(existing);
    return;
  }
  openComplaint(appointment, appointment);
}

function complaintSenderLabel(role) {
  return {
    user: "用户补充",
    institution_admin: "机构回复",
    admin: "平台回复",
  }[role] || "处理记录";
}

function conversationMessages(item) {
  return (item.messages || []).filter((message, index) => !(
    index === 0
    && message.sender_role === "user"
    && message.content === (item.content || item.description)
  ));
}

async function submitComplaint() {
  if (!complaintForm.appointment_id || !complaintForm.category || !complaintForm.content) {
    ElMessage.warning("请选择投诉类型并完整说明问题");
    return;
  }
  complaintSubmitting.value = true;
  try {
    const { data } = await createComplaint({
      appointment_id: complaintForm.appointment_id,
      category: complaintForm.category,
      content: complaintForm.content,
    });
    complaintDialogVisible.value = false;
    ElMessage.success("投诉已提交，机构将通过平台回复");
    await loadComplaints();
    focusComplaintById(data?.item?.id);
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "投诉提交失败");
  } finally {
    complaintSubmitting.value = false;
  }
}

async function confirmComplaint(item) {
  try {
    await confirmComplaintResolved(item.id);
    ElMessage.success("已确认投诉解决");
    await loadComplaints();
    focusComplaintById(item.id);
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "状态更新失败");
  }
}

async function escalateComplaintItem(item) {
  try {
    const { value } = await ElMessageBox.prompt(
      item.status === "institution_pending"
        ? "你可以从投诉提交后直接申请平台介入，请说明需要平台处理的原因。"
        : "请说明对机构处理结果不满意的原因，平台管理员会继续处理。",
      "申请平台处理",
      {
        inputType: "textarea",
        confirmButtonText: "提交平台",
        cancelButtonText: "取消",
        inputPattern: /.+/,
        inputErrorMessage: "请填写申请原因",
      },
    );
    await escalateComplaint(item.id, value);
    ElMessage.success("已转交平台管理员处理");
    await loadComplaints();
    focusComplaintById(item.id);
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || "申请平台处理失败");
    }
  }
}

async function joinWaitlist() {
  try {
    await createWaitlistSubscription(payload());
    ElMessage.success("空位提醒已开启；收到提醒后仍需回来确认预约");
    await reload();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "空位提醒开启失败");
  }
}

async function cancelGroup(group) {
  try {
    await ElMessageBox.confirm(
      "这会取消组内所有尚未到检的预约，确认继续吗？",
      "取消整组预约",
      { type: "warning", confirmButtonText: "确认取消", cancelButtonText: "保留预约" }
    );
    await cancelBookingGroup(group.id);
    ElMessage.success("整组预约已取消");
    await Promise.all([loadAvailability(), reload()]);
  } catch (error) {
    if (error !== "cancel" && error !== "close") ElMessage.error(error?.response?.data?.message || "取消失败");
  }
}

async function cancelOwnAppointment(appointment) {
  try {
    await ElMessageBox.confirm(
      "确认取消你本人的这条受检预约？取消后如需体检必须重新预约。",
      "取消本人预约",
      { type: "warning", confirmButtonText: "确认取消", cancelButtonText: "保留预约" },
    );
    await cancelAppointment(appointment.id);
    ElMessage.success("本人预约已取消");
    await Promise.all([loadAvailability(), reload()]);
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || "取消失败");
    }
  }
}

async function cancelGroupMember(appointment) {
  try {
    await ElMessageBox.confirm(
      "只取消该受检者尚未到检的预约，组内其他成员不受影响。确认继续？",
      "取消该成员",
      { type: "warning", confirmButtonText: "确认取消", cancelButtonText: "保留预约" },
    );
    await cancelAppointment(appointment.id);
    ElMessage.success("该成员预约已取消");
    await Promise.all([loadAvailability(), reload()]);
  } catch (error) {
    if (error !== "cancel" && error !== "close") {
      ElMessage.error(error?.response?.data?.message || "取消失败");
    }
  }
}

async function cancelWaitlist(item) {
  try {
    await cancelWaitlistSubscription(item.id);
    ElMessage.success("空位提醒已取消");
    await reload();
  } catch (error) {
    ElMessage.error(error?.response?.data?.message || "取消提醒失败");
  }
}

onMounted(async () => {
  try {
    const selfKey = `self:${auth.user?.id || "me"}`;
    form.participant_keys = [selfKey];
    participantChanged(form.participant_keys);
    const [friendResponse, intakeResponse] = await Promise.all([
      fetchFriends(),
      fetchBookingIntakeDefaults().catch(() => null),
    ]);
    const relationItems = friendResponse.data.items
      || [...(friendResponse.data.outgoing || []), ...(friendResponse.data.incoming || [])];
    relations.value = [...new Map(relationItems.map((item) => [item.id, item])).values()];
    const requestedRelation = Number(route.query.relation_id);
    if (requestedRelation && relations.value.some(
      (item) => item.id === requestedRelation && relationCanBook(item)
    )) {
      form.participant_keys.push(`relation:${requestedRelation}`);
      participantChanged(form.participant_keys);
    }
    if (intakeResponse?.data?.item) {
      const defaults = intakeResponse.data.item;
      selfRecent.height = defaults.height_cm != null;
      selfRecent.weight = defaults.weight_kg != null;
      Object.assign(participantIntakes[selfKey], defaults, {
        manual_height: !selfRecent.height,
        manual_weight: !selfRecent.weight,
      });
    }
    await Promise.all([loadAvailability(), reload()]);
    focusComplaintById(route.query.complaint_id);
    if (form.institution_id && selectedInstitution.value) step.value = form.package_id ? 3 : 2;
  } catch (error) {
    errorMessage.value = error?.response?.data?.message || "预约信息暂时没有加载成功，请稍后重试";
  }
});

watch(() => route.query.complaint_id, async (value, previous) => {
  if (!value || value === previous) return;
  await loadComplaints();
  focusComplaintById(value);
});

onBeforeUnmount(() => window.clearTimeout(searchTimer));
</script>

<style scoped>
.subject-appointment-panel {
  margin-bottom: 18px;
}

.booking-record-card__actions {
  display: grid;
  justify-items: end;
  gap: 6px;
}

.health-code-participant {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, .8fr);
  gap: 18px;
  margin: 4px 0 14px;
  padding: 16px;
  border: 1px dashed var(--el-border-color);
  border-radius: 14px;
}

.health-code-participant > div:first-child {
  display: grid;
  gap: 5px;
}

.health-code-participant small {
  color: var(--el-text-color-secondary);
}

.health-code-identity-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 14px;
}

.health-code-identity-summary > span {
  display: grid;
  gap: 4px;
  padding: 10px;
  border-radius: 10px;
  background: var(--el-fill-color-light);
}

.health-code-identity-summary small {
  color: var(--el-text-color-secondary);
}

.health-code-participant > div:last-child {
  display: flex;
  align-items: center;
  gap: 8px;
}

.private-intake-ready {
  display: grid;
  gap: 8px;
  min-height: 72px;
}

.manual-intake-field {
  display: grid;
  gap: 4px;
  width: 100%;
}

.private-intake-ready > span {
  color: var(--el-text-color-regular);
  font-size: 14px;
}

.appointment-participant-list {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.appointment-participant-card {
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 14px;
  background: var(--el-fill-color-extra-light);
}

.appointment-participant-card > header,
.complaint-card > header,
.complaint-card footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.appointment-participant-card > header > div,
.complaint-card > header > div {
  display: grid;
  gap: 3px;
}

.appointment-participant-card small,
.complaint-card small {
  color: var(--el-text-color-secondary);
}

.complaint-history-panel {
  margin-top: 18px;
}

.complaint-card-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 14px;
}

.complaint-card {
  display: grid;
  gap: 10px;
  padding: 16px;
  border: 1px solid var(--el-border-color);
  border-radius: 16px;
}

.complaint-card h4,
.complaint-card p {
  margin: 0;
}

.complaint-reply {
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--el-fill-color-light);
}

.complaint-reply p {
  margin-top: 5px;
}

.complaint-message-timeline {
  margin: 2px 0 0;
  padding: 12px 12px 0;
  border-radius: 12px;
  background: var(--el-fill-color-extra-light);
}

.complaint-message-timeline p {
  margin-top: 4px;
}

@media (max-width: 720px) {
  .health-code-participant {
    grid-template-columns: 1fr;
  }

  .health-code-identity-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .health-code-participant > div:last-child {
    align-items: stretch;
    flex-direction: column;
  }

  .booking-record-card__actions {
    grid-column: 2;
    justify-items: start;
  }
}
</style>
