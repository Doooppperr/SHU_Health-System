import http from "./http";


export function authorizeOAuth(payload) {
  return http.post(`${window.location.origin}/oauth/authorize`, payload);
}

export function fetchOAuthClients() {
  return http.get("/admin/oauth-clients");
}

export function decideOAuthClient(clientId, decision) {
  return http.post(`/admin/oauth-clients/${clientId}/decision`, { decision });
}
