/**
 * Console multi-user plugin — user context (session storage).
 *
 * Stores and retrieves verified user info after login / SSO / verify.
 * Now supports dynamic user fields via `UserFieldMap`.
 */

import type { UserInfo, UserFieldMap } from "./types";
import { USER_INFO_SESSION_KEY } from "./index";

/**
 * Store verified user info in sessionStorage after successful login/verify.
 * Clears the key if no user_id is available.
 *
 * Accepts either the new format (with `fields` key) or legacy format
 * (with individual field keys like username, orgId, etc.) for backward compatibility.
 *
 * @param data - Login/verify response data.
 * @param fieldLabels - Optional multi-language label maps from `/auth/status`
 *                      (all 4 languages: zh/en/ja/ru). Stored for display in HeaderUserMenu.
 *                      If omitted, existing stored labels are preserved (useful on page refresh).
 */
export function storeVerifiedUserInfo(
  data: {
    user_id?: string;
    fields?: UserFieldMap;
    /** Legacy support: individual field keys from backend response */
    [key: string]: unknown;
  },
  fieldLabels?: UserInfo["fieldLabels"],
): void {
  let fields: UserFieldMap = {};

  if (data.fields) {
    // New format: explicit fields map
    fields = { ...data.fields };
  } else {
    // Legacy format: extract all keys except known fixed fields
    const fixedKeys = new Set(["user_id", "token", "password", "initialized", "fields"]);
    for (const [k, v] of Object.entries(data)) {
      if (!fixedKeys.has(k) && typeof v === "string" && v) {
        fields[k] = v;
      }
    }
  }

  // Preserve existing labels if new ones not provided (page refresh / token verify)
  const existing = _getStoredUserInfoRaw();
  const labels = fieldLabels ?? existing?.fieldLabels;

  const info: UserInfo = {
    user_id: data.user_id || "",
    fields,
    ...(labels ? { fieldLabels: labels } : {}),
  };
  if (info.user_id) {
    sessionStorage.setItem(USER_INFO_SESSION_KEY, JSON.stringify(info));
  } else {
    sessionStorage.removeItem(USER_INFO_SESSION_KEY);
  }
}

/** Internal: get raw parsed object without type transformation. */
function _getStoredUserInfoRaw(): UserInfo | null {
  const raw = sessionStorage.getItem(USER_INFO_SESSION_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserInfo;
  } catch {
    return null;
  }
}

/**
 * Retrieve stored user info from sessionStorage.
 */
export function getStoredUserInfo(): UserInfo | null {
  const raw = sessionStorage.getItem(USER_INFO_SESSION_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    // Migrate legacy format (flat field keys) to new format
    if (parsed.fields) {
      return parsed as UserInfo;
    }
    // Legacy: convert flat keys to fields map
    const fixedKeys = new Set(["user_id", "username"]);
    const fields: UserFieldMap = {};
    for (const [k, v] of Object.entries(parsed)) {
      if (!fixedKeys.has(k) && typeof v === "string") {
        fields[k] = v;
      }
    }
    return { user_id: parsed.user_id || "", fields };
  } catch {
    return null;
  }
}

/** Clear all multi-user session state (for logout/switch-user). */
export function clearUserSession(): void {
  sessionStorage.removeItem(USER_INFO_SESSION_KEY);
  sessionStorage.removeItem("qwenpaw-agent-storage");
}
