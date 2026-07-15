import http from "./http";

export const fetchMeasurements = (params = {}) => http.get("/self-measurements", { params });
export const createMeasurement = (payload) => http.post("/self-measurements", payload);
export const updateMeasurement = (id, payload) => http.put(`/self-measurements/${id}`, payload);
export const deleteMeasurement = (id) => http.delete(`/self-measurements/${id}`);
export const fetchTimeline = (params = {}) => http.get("/health/timeline", { params });
export const fetchTrend = (indicatorId, params = {}) => http.get(`/health/trends/${indicatorId}`, { params });
