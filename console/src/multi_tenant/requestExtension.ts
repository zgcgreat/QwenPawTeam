/**
 * Console multi-tenant plugin — 401 handler extension.
 *
 * Replaces the default 401 handler in `api/request.ts` with a
 * tenant-aware version that clears all auth/session state and
 * redirects to /login.
 *
 * In the SSO cookie flow, a 401 means the SSO token is invalid or
 * expired, so we must redirect to /login so the user can re-authenticate
 * via the login page.
 */

import { MULTI_TENANT_ENABLED, USER_INFO_SESSION_KEY } from "./index";
import { clearAuthToken } from "../api/config";

/**
 * Tenant-aware 401 handler.
 *
 * Clears all auth state and redirects to /login.
 */
function tenantHandle401(): void {
  // Clear upstream auth token
  clearAuthToken();

  // Clear tenant-specific session data
  if (MULTI_TENANT_ENABLED) {
    sessionStorage.removeItem(USER_INFO_SESSION_KEY);
    localStorage.removeItem("qwenpaw-agent-storage");
  }

  if (window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
}

/**
 * Patch the 401 handler in `api/request.ts` with the tenant-aware version.
 *
 * Called synchronously during multi-tenant initialization.
 */
export function patchRequest401Handler(): void {
  if (!MULTI_TENANT_ENABLED) return;

  // Import the request module and replace handle401 synchronously.
  // Note: handle401 is exported as `export let`, so we must assign
  // through the module namespace object, not the local binding.
  const requestMod = require("../api/request");
  requestMod.handle401 = tenantHandle401;
}
