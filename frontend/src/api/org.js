import http from "./http";

export function fetchOrgInstitution() {
  return http.get("/org/institution");
}

export function updateOrgInstitution(payload) {
  return http.put("/org/institution", payload);
}

export function fetchOrgPackages() {
  return http.get("/org/packages");
}

export function createOrgPackage(payload) {
  return http.post("/org/packages", payload);
}

export function updateOrgPackage(packageId, payload) {
  return http.put(`/org/packages/${packageId}`, payload);
}

export function deactivateOrgPackage(packageId) {
  return http.delete(`/org/packages/${packageId}`);
}

export function fetchOrgImages() {
  return http.get("/org/images");
}

export function uploadOrgImage(file) {
  const formData = new FormData();
  formData.append("file", file);
  return http.post("/org/images", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function reorderOrgImages(imageIds) {
  return http.put("/org/images/order", { image_ids: imageIds });
}

export function deleteOrgImage(imageId) {
  return http.delete(`/org/images/${imageId}`);
}

export const fetchOrgReports = (params = {}) => http.get("/org/reports", { params });
export const createOrgReport = (payload) => http.post("/org/reports", payload);
export const fetchOrgReport = (id) => http.get(`/org/reports/${id}`);
export const updateOrgReport = (id, payload) => http.put(`/org/reports/${id}`, payload);
export const addOrgReportIndicator = (id, payload) => http.post(`/org/reports/${id}/indicators`, payload);
export const updateOrgReportIndicator = (id, indicatorId, payload) => http.put(`/org/reports/${id}/indicators/${indicatorId}`, payload);
export const deleteOrgReportIndicator = (id, indicatorId) => http.delete(`/org/reports/${id}/indicators/${indicatorId}`);
export const lockOrgReport = (id) => http.post(`/org/reports/${id}/lock`);
export const submitOrgReport = (id) => http.post(`/org/reports/${id}/submit`);
export function uploadOrgReportOcr(file, fields) {
  const form = new FormData(); form.append("file", file);
  Object.entries(fields).forEach(([key, value]) => { if (value !== null && value !== "") form.append(key, value); });
  return http.post("/org/reports/ocr", form, { headers: { "Content-Type": "multipart/form-data" } });
}
