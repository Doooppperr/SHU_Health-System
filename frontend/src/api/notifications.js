import http from "./http";

export const fetchNotifications = (params = {}) => http.get("/notifications", { params });
export const fetchNotificationUnreadCount = () => http.get("/notifications/unread-count");
export const markNotificationRead = (id) => http.post(`/notifications/${id}/read`);
export const markAllNotificationsRead = () => http.post("/notifications/read-all");
