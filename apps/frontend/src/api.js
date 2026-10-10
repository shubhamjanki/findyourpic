const API_BASE = import.meta.env.VITE_API_BASE || "";

export async function requestJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed (${response.status}).`);
  }
  return payload;
}

export function imageUrl(path) {
  return `${API_BASE}/api/image?path=${encodeURIComponent(path)}`;
}

export function faceUrl(face) {
  const box = face.bbox;
  return `${API_BASE}/api/face?path=${encodeURIComponent(face.path)}`
    + `&x1=${box.x1}&y1=${box.y1}&x2=${box.x2}&y2=${box.y2}`;
}

export async function downloadZip(paths) {
  const response = await fetch(`${API_BASE}/api/download-zip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `Download failed (${response.status}).`);
  }
  return response.blob();
}
