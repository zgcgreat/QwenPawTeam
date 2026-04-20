/**
 * Console Multi-Tenant Plugin — entry point & activation gate.
 *
 * Activation: Set VITE_MULTI_TENANT_ENABLED=true in vite.config.ts.
 *
 * When **disabled** (default): all exports are no-ops, zero overhead.
 * When **enabled**: call initializeMultiTenant() in main.tsx before React render.
 */

import i18n from "i18next";
import tenantResources from "./i18n/tenant.json";

/**
 * Runtime flag: is the multi-tenant plugin active?
 * Controlled by Vite define substitution at build time.
 */
export const MULTI_TENANT_ENABLED: boolean =
  import.meta.env.VITE_MULTI_TENANT_ENABLED === "true";

/** Session-storage key for verified user info after login/verify. */
export const USER_INFO_SESSION_KEY = "qwenpaw_user_info";

/**
 * One-time initialisation: inject multi-tenant i18n resources.
 * Called from main.tsx before React renders.
 *
 * When the plugin is disabled this is a no-op.
 */
export function initializeMultiTenant(): void {
  if (!MULTI_TENANT_ENABLED) return;

  // Inject per-language translation bundles into react-i18next
  for (const [lang, resources] of Object.entries(tenantResources)) {
    if (!i18n.hasResourceBundle(lang, "translation")) continue;
    // Merge into existing "translation" namespace so keys are accessible via t("login.sysId") etc.
    i18n.addResourceBundle(lang, "translation", resources, true, true);
  }
}
