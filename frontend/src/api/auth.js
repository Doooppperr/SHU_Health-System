import http from "./http";

export function register(payload) {
  return http.post("/auth/register", payload);
}

export function login(payload) {
  return http.post("/auth/login", payload);
}

export function fetchCaptcha() {
  return http.get("/auth/captcha");
}

export function refresh(token) {
  return http.post(
    "/auth/refresh",
    {},
    {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  );
}

export function getMe() {
  return http.get("/users/me");
}

export function requestPasswordResetCode(payload) {
  return http.post("/auth/password-reset/code", payload);
}

export function confirmPasswordReset(payload) {
  return http.post("/auth/password-reset/confirm", payload);
}

export function requestPasswordChangeCode() {
  return http.post("/auth/password-change/code");
}

export function confirmPasswordChange(payload) {
  return http.post("/auth/password-change/confirm", payload);
}
