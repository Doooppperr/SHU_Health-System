import http from "./http";

async function getWithLegacyFallback(primary, legacy, config = {}) {
  try {
    return await http.get(primary, config);
  } catch (error) {
    if (error?.response?.status !== 404 || !legacy) throw error;
    return http.get(legacy, config);
  }
}

export function fetchPublicOrganizations(params = {}) {
  return getWithLegacyFallback("/public/organizations", "/organizations", { params });
}

export function fetchPublicContact() {
  return http.get("/public/contact");
}

export function fetchPublicInstitution(institutionId) {
  return getWithLegacyFallback(
    `/public/institutions/${institutionId}`,
    `/institutions/${institutionId}`,
  );
}

export function fetchPublicInstitutionPackages(institutionId) {
  return getWithLegacyFallback(
    `/public/institutions/${institutionId}/packages`,
    `/institutions/${institutionId}/packages`,
  );
}

export function fetchPublicInstitutionComments(institutionId, params = {}) {
  return getWithLegacyFallback(
    `/public/institutions/${institutionId}/comments`,
    null,
    { params },
  );
}
