import http from "./http";

export const fetchAdminOrganizations = () => http.get("/admin/organizations");
export const createAdminOrganization = (payload) => http.post("/admin/organizations", payload);
export const updateAdminOrganization = (id, payload) => http.put(`/admin/organizations/${id}`, payload);
export const deactivateAdminOrganization = (id) => http.post(`/admin/organizations/${id}/deactivate`);
export const restoreAdminOrganization = (id) => http.post(`/admin/organizations/${id}/restore`);
export const createAdminOrganizationBranch = (id, payload) => http.post(`/admin/organizations/${id}/branches`, payload);

export function fetchAdminInstitutions(params = {}) {
  return http.get("/admin/institutions", { params });
}

export function createAdminInstitution(payload) {
  return http.post("/admin/institutions", payload);
}

export function fetchAdminInstitution(institutionId) {
  return http.get(`/admin/institutions/${institutionId}`);
}

export function updateAdminInstitution(institutionId, payload) {
  return http.put(`/admin/institutions/${institutionId}`, payload);
}

export function deactivateAdminInstitution(institutionId) {
  return http.post(`/admin/institutions/${institutionId}/deactivate`);
}

export function restoreAdminInstitution(institutionId) {
  return http.post(`/admin/institutions/${institutionId}/restore`);
}

export function fetchAdminPackages(institutionId, params = {}) {
  return http.get(`/admin/institutions/${institutionId}/packages`, { params });
}

export function createAdminPackage(institutionId, payload) {
  return http.post(`/admin/institutions/${institutionId}/packages`, payload);
}

export function updateAdminPackage(institutionId, packageId, payload) {
  return http.put(`/admin/institutions/${institutionId}/packages/${packageId}`, payload);
}

export function deactivateAdminPackage(institutionId, packageId) {
  return http.delete(`/admin/institutions/${institutionId}/packages/${packageId}`);
}

export function fetchAdminImages(institutionId) {
  return http.get(`/admin/institutions/${institutionId}/images`);
}

export function uploadAdminImage(institutionId, file) {
  const formData = new FormData();
  formData.append("file", file);
  return http.post(`/admin/institutions/${institutionId}/images`, formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export function reorderAdminImages(institutionId, imageIds) {
  return http.put(`/admin/institutions/${institutionId}/images/order`, { image_ids: imageIds });
}

export function deleteAdminImage(institutionId, imageId) {
  return http.delete(`/admin/institutions/${institutionId}/images/${imageId}`);
}

export function fetchAdminInvites(params = {}) {
  return http.get("/admin/invites", { params });
}

export function issueInstitutionInvite(institutionId) {
  return http.post(`/admin/institutions/${institutionId}/invite`);
}

export const deleteInstitutionAccount = (userId) => http.delete(`/admin/institution-accounts/${userId}`);
export const fetchAdminPackageChangeRequests = (params = {}) => http.get("/admin/package-change-requests", { params });
export const approveAdminPackageChangeRequest = (id, reviewNote = null) => http.post(`/admin/package-change-requests/${id}/approve`, { review_note: reviewNote });
export const rejectAdminPackageChangeRequest = (id, reviewNote = null) => http.post(`/admin/package-change-requests/${id}/reject`, { review_note: reviewNote });
