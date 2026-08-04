import http from "./http";

export function fetchFriends() {
  return http.get("/friends");
}

export function addFriend(payload) {
  return http.post("/friends", payload);
}

export function renameFriend(relationId, payload) {
  return http.put(`/friends/${relationId}`, payload);
}

export function updateFriendAuthorization(relationId, payload) {
  return http.put(`/friends/${relationId}/authorization`, payload);
}
export function updateBookingAuthorization(relationId, payload) {
  return http.put(`/friends/${relationId}/booking-authorization`, payload);
}

export function deleteFriend(relationId) {
  return http.delete(`/friends/${relationId}`);
}

export function switchFriendSession(relationId) {
  return http.post(`/friends/${relationId}/switch-session`);
}

export function exitFriendSession() {
  return http.post("/auth/delegation/exit");
}

export function returnFriendSession() {
  return http.post("/auth/delegation/back");
}
