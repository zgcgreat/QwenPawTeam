/**
 * Console multi-user plugin — authentication header builder.
 *
 * Ported from CoPaw/console/src/api/authHeaders.ts with QwenPaw
 * sessionStorage key adaptation.
 *
 * When MULTI_USER_ENABLED is false (default) all public functions
 * degrade to safe no-op / upstream-compatible behaviour with zero side-effects.
 */

import { MULTI_USER_ENABLED } from "./index";
import { getApiToken } from "../api/config";

// ── Cookie utilities ────────────────────────────────────────────────────

/**
 * Read a single cookie value by name.
 * Returns empty string when not found or HttpOnly.
 */
export function getCookieValue(name: string): string {
  if (!MULTI_USER_ENABLED) return "";
  const match = document.cookie.match(
    new RegExp(
      `(?:^|; )${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}=([^;]*)`,
    ),
  );
  return match ? decodeURIComponent(match[1]) : "";
}

/**
 * Normalise a raw token into a proper ``Authorization`` header value.
 *
 * Some systems store bare tokens (``"eyJhbGci..."``), others already include
 * the scheme prefix (``"Bearer eyJhbGci..."``).  This ensures exactly one
 * ``Bearer `` prefix regardless of input.
 *
 * Non-Bearer schemes (``Basic``, ``Digest`` …) are returned as-is.
 */
export function toBearerHeader(token: string): string {
  if (!token) return "";
  if (/^[A-Za-z]+ /u.test(token)) return token;
  return `Bearer ${token}`;
}

// ── SSO configuration ───────────────────────────────────────────────────

/** Cookie name for the business-system Authorization token (empty = disable). */
const AUTH_COOKIE_NAME = "authorization";

/**
 * SSO cookie names to probe, in priority order.
 *
 * Built-in defaults cover common naming conventions. Override at build time via
 * ``VITE_SSO_COOKIE_NAMES`` env var (comma-separated).
 *
 * At runtime the first non-empty cookie in the list wins.
 */
const _envNames = import.meta.env.VITE_SSO_COOKIE_NAMES as string | undefined;

export const SSO_COOKIE_NAMES: readonly string[] =
  MULTI_USER_ENABLED && _envNames
    ? _envNames.split(",").map((s) => s.trim()).filter(Boolean)
    : MULTI_USER_ENABLED
      ? [
          "sso.token",
          "sso_token",
          "SSO_TOKEN",
          "id_token",
          "access_token",
          "auth_token",
        ]
      : [];

/**
 * Return the first non-empty SSO cookie value found, or ``""`` when none present.
 */
export function getSsoToken(): string {
  if (!MULTI_USER_ENABLED) return "";
  for (const name of SSO_COOKIE_NAMES) {
    const value = getCookieValue(name);
    if (value) return value;
  }
  return "";
}

// ── Forwarded cookies ───────────────────────────────────────────────────

/** Cookies forwarded to backend via X-Forwarded-* headers (NOT the auth cookie). */
const FORWARDED_COOKIE_NAMES: string[] = ["u-token", "x-token"];

// ── Public API ─────────────────────────────────────────────────────────

/**
 * Build request headers.  Behaviour depends on activation state:
 *
 * **Plugin ENABLED** (full mode):
 *   1. Authorization from business-system cookie OR localStorage fallback.
 *   2. X-Agent-Id from sessionStorage agent selection.
 *   3. X-Forwarded-* headers for specified cookies.
 *
 * **Plugin DISABLED** (upstream-compatible):
 *   1. Bearer token from localStorage only (original QwenPaw behaviour).
 *   2. X-Agent-Id from sessionStorage agent selection.
 */
export function buildAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};

  if (MULTI_USER_ENABLED) {
    // Priority: business-system cookie > localStorage token
    const cookieAuth = AUTH_COOKIE_NAME ? getCookieValue(AUTH_COOKIE_NAME) : "";
    if (cookieAuth) {
      headers.Authorization = toBearerHeader(cookieAuth);
    } else {
      const token = getApiToken();
      if (token) {
        headers.Authorization = `Bearer ${token}`;
      }
    }

    // Forward selected cookies
    for (const name of FORWARDED_COOKIE_NAMES) {
      const value = getCookieValue(name);
      if (value) {
        headers[
          `X-Forwarded-${name.charAt(0).toUpperCase()}${name.slice(1)}`
        ] = value;
      }
    }
  } else {
    // Upstream-compatible: simple Bearer + agent ID only
    const token = getApiToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
  }

  // Agent selection (both modes)
  // NOTE: agentStore uses localStorage (via zustand/persist), so we must
  // read from localStorage — NOT sessionStorage — to match the actual
  // storage location.  Some legacy code clears sessionStorage on login
  // which would lose the agent selection if we only checked there.
  try {
    const agentStorage = localStorage.getItem("qwenpaw-agent-storage");
    if (agentStorage) {
      const parsed = JSON.parse(agentStorage);
      const selectedAgent = parsed?.state?.selectedAgent;
      if (selectedAgent) {
        headers["X-Agent-Id"] = selectedAgent;
      }
    }
  } catch {
    // ignore parse errors
  }

  return headers;
}
