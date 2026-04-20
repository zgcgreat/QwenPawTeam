/**
 * Console multi-tenant plugin — user context (session storage).
 *
 * Stores and retrieves verified user info after login / SSO / verify.
 * Based on CoPaw App.tsx storeVerifiedUserInfo / getStoredUserInfo.
 */

import type { TenantUserInfo } from "./types";
import { USER_INFO_SESSION_KEY } from "./index";

/**
 * Store verified user info in sessionStorage after successful login/verify.
 * Clears the key if no tenant_id is available.
 */
export function storeVerifiedUserInfo(data: {
  tenant_id?: string;
  sysId?: string;
  branchId?: string;
  vorgCode?: string;
  sapId?: string;
  positionId?: string;
}): void {
  const info: TenantUserInfo = {
    tenant_id: data.tenant_id || "",
    sysId: data.sysId || "",
    branchId: data.branchId || "",
    vorgCode: data.vorgCode || "",
    sapId: data.sapId || "",
    positionId: data.positionId || "",
  };
  if (info.tenant_id) {
    sessionStorage.setItem(USER_INFO_SESSION_KEY, JSON.stringify(info));
  } else {
    sessionStorage.removeItem(USER_INFO_SESSION_KEY);
  }
}

/**
 * Retrieve stored user info from sessionStorage.
 */
export function getStoredUserInfo(): TenantUserInfo | null {
  const raw = sessionStorage.getItem(USER_INFO_SESSION_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** Clear all multi-tenant session state (for logout/switch-user). */
export function clearUserSession(): void {
  sessionStorage.removeItem(USER_INFO_SESSION_KEY);
  sessionStorage.removeItem("qwenpaw-agent-storage");
}
