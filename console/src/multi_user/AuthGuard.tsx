/**
 * Console multi-user plugin — authentication guard (3-level auth chain).
 *
 * Auth priority:
 *   1. SSO cookie (getSsoToken) → init-workspace → pass through
 *   2. localStorage token (from /auth/login) → /auth/verify → pass through
 *   3. No credentials → redirect to /login
 *
 * When MULTI_USER_ENABLED is false this component is never rendered
 * (App.tsx selects UpstreamAuthGuard instead), so all logic here is
 * guarded by the build-time flag.
 */

import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { Spin } from "antd";
import { LoadingOutlined } from "@ant-design/icons";
import { useTheme } from "../contexts/ThemeContext";
import { muAuthApi } from "./authApi";
import { storeVerifiedUserInfo } from "./userContext";
import { getSsoToken } from "./authHeaders";
import { getApiUrl, getApiToken, clearAuthToken } from "../api/config";
import { useTranslation } from "react-i18next";

// ── Initialising screen ────────────────────────────────────────────────

function InitializingScreen() {
  const { isDark } = useTheme();
  const { t } = useTranslation();

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 20,
        background: isDark
          ? "linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%)"
          : "linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%)",
      }}
    >
      <img
        src={`${import.meta.env.BASE_URL}${
          isDark ? "dark-logo.png" : "logo.png"
        }`}
        alt="QwenPaw"
        style={{ height: 48, opacity: 0.9 }}
      />
      <Spin
        indicator={
          <LoadingOutlined
            style={{
              fontSize: 32,
              color: isDark ? "#7c9eff" : "#4c6ef5",
            }}
            spin
          />
        }
      />
      <span
        style={{
          fontSize: 14,
          color: isDark
            ? "rgba(255,255,255,0.55)"
            : "rgba(0,0,0,0.45)",
          letterSpacing: "0.02em",
        }}
      >
        {t("app.initializing", "Initializing…")}
      </span>
    </div>
  );
}

// ── Guard component ────────────────────────────────────────────────────

interface AuthGuardProps {
  children: React.ReactNode;
}

type AuthStatus =
  | "loading"
  | "initializing"
  | "auth-required"
  | "ok";

export default function MuAuthGuard({ children }: AuthGuardProps) {
  const [status, setStatus] = useState<AuthStatus>("loading");

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        // ── Step 1: SSO cookie → init-workspace ────────────────────
        const ssoToken = getSsoToken();
        if (ssoToken) {
          if (!cancelled) setStatus("initializing");
          try {
            const info = await muAuthApi.initWorkspace(ssoToken);
            if (info && !cancelled) storeVerifiedUserInfo(info);
          } catch {
            // init-workspace failed — still pass through;
            // backend falls back to "default" user.
          }
          if (!cancelled) setStatus("ok");
          return;
        }

        // ── Step 2: localStorage token (from /auth/login) → verify ─
        const token = getApiToken();
        if (token) {
          if (!cancelled) setStatus("initializing");
          try {
            const r = await fetch(getApiUrl("/auth/verify"), {
              headers: { Authorization: `Bearer ${token}` },
            });
            if (cancelled) return;
            if (r.ok) {
              const data = await r.json();
              storeVerifiedUserInfo(data);
              if (!cancelled) setStatus("ok");
            } else {
              clearAuthToken();
              if (!cancelled) setStatus("auth-required");
            }
          } catch {
            if (!cancelled) {
              clearAuthToken();
              setStatus("auth-required");
            }
          }
          return;
        }

        // ── Step 3: No credentials → redirect to login ─────────────
        if (!cancelled) setStatus("auth-required");
      } catch {
        if (!cancelled) setStatus("auth-required");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  if (status === "loading") return null;
  if (status === "initializing") return <InitializingScreen />;
  if (status === "auth-required")
    return (
      <Navigate
        to={`/login?redirect=${encodeURIComponent(window.location.pathname)}`}
        replace
      />
    );
  return <>{children}</>;
}
