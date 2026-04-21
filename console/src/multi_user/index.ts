/**
 * Console Multi-User Plugin — entry point & activation gate.
 *
 * Activation: Set VITE_MULTI_USER_ENABLED=true in vite.config.ts.
 *
 * When **disabled** (default): all exports are no-ops, zero overhead.
 * When **enabled**: call initializeMultiUser() in main.tsx before React render.
 */

import i18n from "i18next";
import userResources from "./i18n/user.json";

/**
 * Runtime flag: is the multi-user plugin active?
 * Controlled by Vite define substitution at build time.
 */
export const MULTI_USER_ENABLED: boolean =
  import.meta.env.VITE_MULTI_USER_ENABLED === "true";

/** Session-storage key for verified user info after login/verify. */
export const USER_INFO_SESSION_KEY = "qwenpaw_user_info";

/**
 * One-time initialisation: inject multi-user i18n resources.
 * Called from main.tsx before React renders.
 *
 * When the plugin is disabled this is a no-op.
 */
export function initializeMultiUser(): void {
  if (!MULTI_USER_ENABLED) return;

  // Inject per-language translation bundles into react-i18next
  for (const [lang, resources] of Object.entries(userResources)) {
    if (!i18n.hasResourceBundle(lang, "translation")) continue;
    // Merge into existing "translation" namespace so keys are accessible via t("login.username") etc.
    i18n.addResourceBundle(lang, "translation", resources, true, true);
  }
}
