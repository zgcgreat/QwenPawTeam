/**
 * Authorization + X-Agent-Id for API requests.
 *
 * When the multi-tenant plugin is active (VITE_MULTI_TENANT_ENABLED=true),
 * this delegates to the plugin version which also supports SSO cookies and
 * forwarded headers.  Otherwise falls back to the original upstream logic.
 *
 * This single delegation point ensures every upstream consumer that imports
 * from "../authHeaders" automatically benefits from multi-tenant auth — no
 * per-file import path changes needed.
 */
import { MULTI_TENANT_ENABLED } from "../multi_tenant/index";
import { buildAuthHeaders as mtBuildAuthHeaders } from "../multi_tenant/authHeaders";
import { getApiToken } from "./config";

function _upstreamBuildAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getApiToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  try {
    const agentStorage = sessionStorage.getItem("qwenpaw-agent-storage");
    if (agentStorage) {
      const parsed = JSON.parse(agentStorage);
      const selectedAgent = parsed?.state?.selectedAgent;
      if (selectedAgent) {
        headers["X-Agent-Id"] = selectedAgent;
      }
    }
  } catch (error) {
    console.warn("Failed to get selected agent from storage:", error);
  }
  return headers;
}

export function buildAuthHeaders(): Record<string, string> {
  return MULTI_TENANT_ENABLED
    ? mtBuildAuthHeaders()
    : _upstreamBuildAuthHeaders();
}
