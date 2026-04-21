/**
 * Console multi-user plugin — extended authentication API.
 *
 * Replaces / augments upstream api/modules/auth with:
 * - **Dynamic user fields login** (fields configured on backend + password)
 * - **initWorkspace** for SSO-authenticated users
 * - **resolveUser** for business-system cookie integration
 * - **multi_user** flag in status response
 */

import { getApiUrl } from "../api/config";
import type {
  LoginRequest,
  LoginResponse,
  AuthStatusResponse,
  InitWorkspaceResponse,
} from "./types";
import { buildAuthHeaders, toBearerHeader } from "./authHeaders";

export const muAuthApi = {
  // ── SSO integration ───────────────────────────────────────────────

  /**
   * Initialize user workspace for an SSO-authenticated user.
   *
   * @param ssoToken - Raw SSO token (bare or Bearer-prefixed).
   *                   Omit to send without Authorization header.
   */
  initWorkspace: async (
    ssoToken?: string,
  ): Promise<InitWorkspaceResponse | null> => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (ssoToken) headers.Authorization = toBearerHeader(ssoToken);

    const res = await fetch(getApiUrl("/auth/init-workspace"), {
      method: "POST",
      headers,
      body: JSON.stringify({}),
    });
    if (!res.ok) return null;
    const data = await res.json();
    // Transform backend flat response to frontend UserInfo format
    return _transformResponse(data);
  },

  /**
   * Resolve user identity from the upstream business-system token.
   * Used in integration mode (gateway sets Authorization cookie).
   */
  resolveUser: async (): Promise<LoginResponse | null> => {
    const res = await fetch(getApiUrl("/auth/resolve-user"), {
      headers: buildAuthHeaders(),
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (!data.user_id) return null;
    return _transformResponse(data);
  },

  // ── Standalone (dynamic fields) auth ──────────────────────────────

  /**
   * Login with dynamic user fields + password.
   * The backend auto-registers new users on first successful login.
   */
  login: async (fields: LoginRequest): Promise<LoginResponse> => {
    const res = await fetch(getApiUrl("/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Login failed");
    }
    const data = await res.json();
    return _transformResponse(data);
  },

  /** Check auth status (includes multi_user flag when plugin is active). */
  getStatus: async (): Promise<AuthStatusResponse> => {
    const res = await fetch(getApiUrl("/auth/status"));
    if (!res.ok) throw new Error("Failed to check auth status");
    return res.json();
  },

  /** Update password (current + new). */
  updateProfile: async (
    currentPassword: string,
    newPassword?: string,
  ): Promise<LoginResponse> => {
    const token =
      localStorage.getItem("qwenpaw_auth_token") ||
      localStorage.getItem("copaw_auth_token") ||
      "";
    const res = await fetch(getApiUrl("/auth/update-profile"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword || null,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Update failed");
    }
    const data = await res.json();
    return _transformResponse(data);
  },
};

/**
 * Transform a backend response (flat fields: username, orgId, etc.)
 * into the frontend UserInfo format with a `fields` map.
 */
function _transformResponse(data: Record<string, unknown>): LoginResponse {
  const fixedKeys = new Set([
    "token", "user_id", "password", "initialized", "valid", "fields",
  ]);
  const fields: Record<string, string> = {};

  // If backend already provides `fields` key (future-proof)
  if (data.fields && typeof data.fields === "object") {
    Object.assign(fields, data.fields as Record<string, string>);
  } else {
    // Extract dynamic user fields from flat response
    for (const [k, v] of Object.entries(data)) {
      if (!fixedKeys.has(k) && typeof v === "string") {
        fields[k] = v;
      }
    }
  }

  return {
    token: (data.token as string) || "",
    user_id: (data.user_id as string) || "",
    fields,
  };
}
