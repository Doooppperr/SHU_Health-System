import http from "./http";

export function fetchInstitutionComments(institutionId, params = {}) {
  return http.get("/comments", {
    params: { ...params, institution_id: institutionId },
  });
}

export function fetchMyComments(params = {}) {
  return http.get("/comments/mine", { params });
}

export function createInstitutionComment(payload) {
  return http.post("/comments", payload);
}

export function fetchCommentModerationList(params = {}) {
  return http.get("/comments/moderation", { params });
}

export function updateCommentVisibility(commentId, payload) {
  return http.put(`/comments/${commentId}/visibility`, payload);
}

export function updateComment(commentId, payload) {
  return http.put(`/comments/${commentId}`, payload);
}

export function deleteComment(commentId) {
  return http.delete(`/comments/${commentId}`);
}

export function fetchOrganizationComments(params = {}) {
  return http.get("/comments/organization", { params });
}

export function submitOrganizationReply(commentId, content) {
  return http.post(`/comments/${commentId}/reply`, { content });
}

export function approveCommentReply(replyId) {
  return http.post(`/comments/replies/${replyId}/approve`);
}

export function rejectCommentReply(replyId, reviewNote) {
  return http.post(`/comments/replies/${replyId}/reject`, { review_note: reviewNote });
}

export function fetchUnreadCommentReplyCount() {
  return http.get("/comments/mine/unread-replies");
}

export function markCommentRepliesRead() {
  return http.post("/comments/mine/replies/read");
}

export const fetchMyCommentSanction = () => http.get("/comments/mine/sanction");
export const submitCommentAppeal = (sanctionId, content) => http.post(
  "/comments/appeals",
  { sanction_id: sanctionId, content },
);
export const fetchCommentAppeals = (params = {}) => http.get("/comments/appeals", { params });
export const sanctionCommentUser = (userId, reason, sourceCommentId = null, durationDays = null) => http.post(
  "/comments/moderation/sanctions",
  {
    user_id: userId,
    source_comment_id: sourceCommentId,
    reason,
    duration_days: durationDays,
  },
);
export const resolveCommentAppeal = (appealId, action, reason = "") => http.post(
  `/comments/appeals/${appealId}/${action === "unban" ? "approve" : "reject"}`,
  { review_note: reason },
);
