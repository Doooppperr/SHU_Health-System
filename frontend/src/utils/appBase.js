export function appPath(path, baseUrl = import.meta.env.BASE_URL) {
  const base = `/${String(baseUrl || "/").replace(/^\/+|\/+$/g, "")}`;
  const suffix = `/${String(path || "").replace(/^\/+/, "")}`;
  return base === "/" ? suffix : `${base}${suffix}`;
}
