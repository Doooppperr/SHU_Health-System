import http from "./http";

export const fetchProfile = () => http.get("/profile/me");
export const updateProfile = (payload) => http.put("/profile/me", payload);

export async function completeBasicProfile(payload) {
  try {
    return await http.post("/profile/me/complete", payload);
  } catch (error) {
    if (![404, 405].includes(error?.response?.status)) throw error;
    return http.put("/profile/me", payload);
  }
}
