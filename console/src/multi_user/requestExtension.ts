/**
 * Console multi-user plugin — 401 handler extension.
 *
 * Replaces the default 401 handler in `api/request.ts` with a
 * user-aware version that clears all auth/session state and
 * redirects to /login.
 *
 * In the SSO cookie flow, a 401 means the SSO token is invalid or
 * expired, so we must redirect to /login so the user can re-authenticate
 * via the login page.
 */

import { MULTI_USER_ENABLED, USER_INFO_SESSION_KEY } from "./index";
import { clearAuthToken } from "../api/config";
import { setHandle401 } from "../api/request";

/**
 * User-aware 401 handler.
 *
 * Clears all auth state and redirects to /login.
 */
function userHandle401(): void {
  // Clear upstream auth token
  clearAuthToken();

  // Clear user-specific session data
  if (MULTI_USER_ENABLED) {
    sessionStorage.removeItem(USER_INFO_SESSION_KEY);
    localStorage.removeItem("qwenpaw-agent-storage");
  }

  if (window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
}

/**
 * Patch the 401 handler in `api/request.ts` with the user-aware version.
 *
 * Called synchronously during multi-user initialization.
 * Uses the exported `setHandle401()` function instead of direct
 * module namespace mutation (which doesn't work in ESM).
 */
export function patchRequest401Handler(): void {
  if (!MULTI_USER_ENABLED) return;
  setHandle401(userHandle401);
}
