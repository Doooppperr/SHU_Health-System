import http from "./http";

export function fetchOrgInstitution() {
  return http.get("/org/institution");
}

export function updateOrgInstitution(payload) {
  return http.put("/org/institution", payload);
}

export function fetchOrgPackages(params = {}) {
  return http.get("/org/packages", { params });
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
export const fetchOrgContext = () => http.get("/org/context");
export const fetchOrgReportAssetContent = (id, assetId) => http.get(`/org/reports/${id}/assets/${assetId}/content`, { responseType: "blob" });
export const updateOrgReport = (id, payload) => http.put(`/org/reports/${id}`, payload);
export const addOrgReportIndicator = (id, payload) => http.post(`/org/reports/${id}/indicators`, payload);
export const updateOrgReportIndicator = (id, indicatorId, payload) => http.put(`/org/reports/${id}/indicators/${indicatorId}`, payload);
export const deleteOrgReportIndicator = (id, indicatorId) => http.delete(`/org/reports/${id}/indicators/${indicatorId}`);
export const addOrgTextResult = (id, payload) => http.post(`/org/health-data/${id}/text-results`, payload);
export const deleteOrgTextResult = (id, resultId) => http.delete(`/org/health-data/${id}/text-results/${resultId}`);
export function uploadOrgHealthAsset(id, file, fields) { const form=new FormData(); form.append("file",file); Object.entries(fields).forEach(([k,v])=>form.append(k,v??"")); return http.post(`/org/health-data/${id}/assets`,form,{headers:{"Content-Type":"multipart/form-data"}}); }
export const fetchOrgReportAssetTypes = (reportId) => http.get("/org/report-asset-types", { params: { report_id: reportId } });
export const updateOrgHealthAsset = (id, assetId, payload) => http.patch(`/org/health-data/${id}/assets/${assetId}`, payload);
export const deleteOrgHealthAsset = (id, assetId) => http.delete(`/org/health-data/${id}/assets/${assetId}`);
export const lockOrgReport = (id) => http.post(`/org/reports/${id}/lock`);
export const submitOrgReport = (id) => http.post(`/org/reports/${id}/submit`);
export const submitOrgReportForReview = (id, uploadDoctorName) => http.post(`/org/reports/${id}/submit-review`, { upload_doctor_name: uploadDoctorName });
export const reviewOrgReport = (id, reviewDoctorName) => http.post(`/org/reports/${id}/review`, { review_doctor_name: reviewDoctorName });
export const fetchOrgAudienceInsights = (params = {}) => http.get("/org/audience-insights", { params });
export const fetchOrgAccountDeactivationCheck = () => http.get("/org/account/deactivation-check");
export const deactivateOrgAccount = (currentPassword) => http.post(
  "/org/account/deactivate",
  { confirm: true, current_password: currentPassword },
);
export function uploadOrgReportOcr(file, fields) {
  const form = new FormData(); form.append("file", file);
  Object.entries(fields).forEach(([key, value]) => { if (value !== null && value !== "") form.append(key, value); });
  return http.post("/org/reports/ocr", form, { headers: { "Content-Type": "multipart/form-data" } });
}

export const reactivateOrgPackage = (packageId) => http.post(`/org/packages/${packageId}/reactivate`);
export const fetchOrgPackageChangeRequests = (params = {}) => http.get("/org/package-change-requests", { params });
export const withdrawOrgPackageChangeRequest = (id) => http.post(`/org/package-change-requests/${id}/withdraw`);
export const fetchOrgAppointments = (params = {}) => http.get("/org/appointments", { params });
export const attendOrgAppointment = (id) => http.post(`/org/appointments/${id}/attend`);
export const invalidateOrgAppointment = (id) => http.post(`/org/appointments/${id}/invalidate`);
export const closeOrgAppointment = (id, payload) => http.post(`/org/appointments/${id}/close`, payload);
export const fetchOrgAppointmentCapacity = () => http.get("/org/appointment-capacity");
export const updateOrgAppointmentCapacity = (dailyAppointmentLimit) => http.put("/org/appointment-capacity", { daily_appointment_limit: dailyAppointmentLimit });
