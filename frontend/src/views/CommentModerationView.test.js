import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  approveCommentReply: vi.fn(),
  fetchCommentAppeals: vi.fn(),
  fetchCommentModerationList: vi.fn(),
  rejectCommentReply: vi.fn(),
  resolveCommentAppeal: vi.fn(),
  sanctionCommentUser: vi.fn(),
  updateCommentVisibility: vi.fn(),
}));

vi.mock("../api/comments", () => api);
vi.mock("../components/MainNavActions.vue", () => ({
  default: { template: "<div />" },
}));

import CommentModerationView from "./CommentModerationView.vue";

const wrappers = [];

function commentRow(overrides = {}) {
  return {
    id: 1,
    institution: { name: "虚构健康机构", branch_name: "虚构分院" },
    user: { id: 8, username: "test1" },
    rating: 3,
    content: "虚构待审核评论",
    is_visible: false,
    hidden_reason: null,
    created_at: "2026-08-03T08:00:00+08:00",
    reply: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchCommentModerationList.mockImplementation((params) => {
    const item = params.queue === "replies"
      ? commentRow({
        id: 2,
        content: "虚构已有机构回复的评论",
        reply: {
          id: 9,
          content: "虚构待审核回复",
          status: "pending",
          status_label: "待审核",
        },
      })
      : commentRow();
    const total = { comments: 18, replies: 7, all: 31 }[params.queue] ?? 31;
    return Promise.resolve({
      data: {
        items: [item],
        pagination: { page: params.page, page_size: 15, total, pages: 3 },
        counts: { comments_pending: 18, replies_pending: 7, all: 31 },
      },
    });
  });
  api.fetchCommentAppeals.mockImplementation((params) => Promise.resolve({
    data: {
      items: [{
        id: 15,
        user_id: 8,
        content: "虚构申诉说明",
        status: params.status || "approved",
        sanction: {
          reason: "虚构禁言原因",
          duration_label: "7天",
          user: { id: 8, username: "test1" },
        },
      }],
      pagination: { page: params.page, page_size: 15, total: 5, pages: 3 },
      counts: { pending: 5, approved: 2, rejected: 1, all: 8 },
    },
  }));
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  document.body.innerHTML = "";
});

describe("评论治理服务端分页", () => {
  it("uses server queues and counts, paginates appeals, and renders the sanction user fallback", async () => {
    const wrapper = mount(CommentModerationView, {
      attachTo: document.body,
      global: {
        plugins: [ElementPlus],
        stubs: { teleport: true },
      },
    });
    wrappers.push(wrapper);
    await flushPromises();

    expect(api.fetchCommentModerationList).toHaveBeenCalledWith({
      page: 1,
      page_size: 15,
      queue: "comments",
    });
    expect(wrapper.text()).toContain("用户评价待审核（18）");
    expect(wrapper.text()).toContain("机构回复待审核（7）");
    expect(wrapper.text()).toContain("封禁申诉（5）");
    expect(wrapper.text()).toContain("全部审核记录（31）");

    wrapper.vm.mode = "replies";
    await wrapper.vm.modeChanged("replies");
    await flushPromises();
    expect(api.fetchCommentModerationList).toHaveBeenLastCalledWith({
      page: 1,
      page_size: 15,
      queue: "replies",
    });
    expect(wrapper.text()).toContain("虚构待审核回复");

    wrapper.vm.mode = "appeals";
    await wrapper.vm.modeChanged("appeals");
    await flushPromises();
    expect(api.fetchCommentAppeals).toHaveBeenLastCalledWith({
      page: 1,
      page_size: 15,
      status: "pending",
    });
    expect(wrapper.text()).toContain("test1");
    expect(wrapper.vm.appealPagination.total).toBe(5);

    wrapper.vm.appealPagination.page = 2;
    await wrapper.vm.loadAppeals();
    expect(api.fetchCommentAppeals).toHaveBeenLastCalledWith({
      page: 2,
      page_size: 15,
      status: "pending",
    });

    wrapper.vm.appealStatus = "all";
    await wrapper.vm.appealStatusChanged();
    expect(api.fetchCommentAppeals).toHaveBeenLastCalledWith({
      page: 1,
      page_size: 15,
    });
  });
});
