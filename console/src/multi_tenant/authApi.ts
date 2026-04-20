/**
 * Console multi-tenant plugin — extended authentication API.
 *
 * Replaces / augments upstream api/modules/auth with:
 * - **5-tuple login** (sysId, branchId, vorgCode, sapId, positionId + password)
 * - **initWorkspace** for SSO-authenticated users
 * - **resolveTenant** for business-system cookie integration
 * - **multi_tenant** flag in status response
 */

import { getApiUrl } from "../api/config";
import type {
  LoginRequest,
  LoginResponse,
  AuthStatusResponse,
  InitWorkspaceResponse,
} from "./types";
import { buildAuthHeaders, toBearerHeader } from "./authHeaders";

export const mtAuthApi = {
  // ── SSO integration ───────────────────────────────────────────────

  /**
   * Initialize tenant workspace for an SSO-authenticated user.
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
    return res.json();
  },

  /**
   * Resolve tenant identity from the upstream business-system token.
   * Used in integration mode (gateway sets Authorization cookie).
   */
  resolveTenant: async (): Promise<LoginResponse | null> => {
    const res = await fetch(getApiUrl("/auth/resolve-tenant"), {
      headers: buildAuthHeaders(),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.tenant_id ? data : null;
  },

  // ── Standalone (5-tuple) auth ─────────────────────────────────────

  /**
   * Login with 5-tuple identity + password.
   * The backend auto-registers new tenants on first successful login.
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
    return res.json();
  },

  /** Check auth status (includes multi_tenant flag when plugin is active). */
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
    return res.json();
  },
};
