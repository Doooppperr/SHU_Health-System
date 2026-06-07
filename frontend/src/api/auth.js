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
