import http from "./http";

export function fetchUsers(params = {}) {
  return http.get("/users", { params });
}

export const fetchUser = (userId) => http.get(`/users/${userId}`);
export const changeUserPassword = (userId, password) => http.post(`/users/${userId}/password`, { password });
export const retryUserPasswordNotification = (userId) => http.post(`/users/${userId}/password-notification/retry`);
export const correctUserBasicProfile = (userId, payload) => http.put(`/admin/users/${userId}/basic-profile`, payload);

export function updateUser(userId, payload) {
  return http.put(`/users/${userId}`, payload);
}

export function deleteUser(userId) {
  return http.delete(`/users/${userId}`, { data: { confirm: true } });
}
