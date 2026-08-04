import http from "./http";

export const fetchMyComplaints = (params = {}) => http.get("/complaints", { params });
export const createComplaint = (payload) => http.post("/complaints", payload);
export const confirmComplaintResolved = (id) => http.post(`/complaints/${id}/confirm-resolved`);
export const escalateComplaint = (id, reason = "") => http.post(`/complaints/${id}/escalate`, { reason });

export const fetchOrgComplaints = (params = {}) => http.get("/org/complaints", { params });
export const replyOrgComplaint = (id, content) => http.post(`/org/complaints/${id}/reply`, { content });

export const fetchAdminComplaints = (params = {}) => http.get("/admin/complaints", { params });
export const startAdminComplaint = (id) => http.post(`/admin/complaints/${id}/start`);
export const replyAdminComplaint = (id, content) => http.post(`/admin/complaints/${id}/reply`, { content });
export const resolveAdminComplaint = (id) => http.post(`/admin/complaints/${id}/resolve`);
