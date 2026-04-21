/**
 * Console multi-user plugin — dynamic user-fields login page.
 *
 * Renders a form with fields driven by the backend's `user_fields` config
 * (returned by `/auth/status`).  Falls back to a single `username` field when
 * the status endpoint is unavailable.
 */

import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams, useNavigate } from "react-router-dom";
import { Button, Form, Input } from "antd";
import { useAppMessage } from "../hooks/useAppMessage";
import {
  LockOutlined,
  UserOutlined,
  BankOutlined,
  ApartmentOutlined,
} from "@ant-design/icons";
import { muAuthApi } from "./authApi";
import { setAuthToken as _setAuthToken } from "../api/config";
import { storeVerifiedUserInfo } from "./userContext";
import { useTheme } from "../contexts/ThemeContext";
import type { AuthStatusResponse } from "./types";

/** Icon rotation for dynamic fields — cycles through these. */
const FIELD_ICONS = [
  BankOutlined,   // system/org level
  ApartmentOutlined, // department level
  UserOutlined,   // user level
  LockOutlined,   // generic
];

/** Default field (used when /auth/status is unavailable). */
const DEFAULT_FIELDS = ["username"];
const DEFAULT_LABELS_ZH: Record<string, string> = { username: "用户名", password: "密码" };
const DEFAULT_LABELS_EN: Record<string, string> = { username: "Username", password: "Password" };
const DEFAULT_LABELS_JA: Record<string, string> = { username: "ユーザー名", password: "パスワード" };
const DEFAULT_LABELS_RU: Record<string, string> = { username: "Имя пользователя", password: "Пароль" };

/** Merge backend labels into defaults (backend overrides defaults). */
function mergeLabels(defaults: Record<string, string>, overrides?: Record<string, string>): Record<string, string> {
  if (!overrides || Object.keys(overrides).length === 0) return defaults;
  return { ...defaults, ...overrides };
}

export default function MuLoginPage() {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { isDark } = useTheme();
  const [loading, setLoading] = useState(false);
  const { message } = useAppMessage();

  // Dynamic field config from backend (all 4 languages)
  const [userFields, setUserFields] = useState<string[]>(DEFAULT_FIELDS);
  const [fieldLabelsZh, setFieldLabelsZh] = useState<Record<string, string>>(DEFAULT_LABELS_ZH);
  const [fieldLabelsEn, setFieldLabelsEn] = useState<Record<string, string>>(DEFAULT_LABELS_EN);
  const [fieldLabelsJa, setFieldLabelsJa] = useState<Record<string, string>>(DEFAULT_LABELS_JA);
  const [fieldLabelsRu, setFieldLabelsRu] = useState<Record<string, string>>(DEFAULT_LABELS_RU);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status: AuthStatusResponse = await muAuthApi.getStatus();
        if (cancelled) return;
        if (status.user_fields && status.user_fields.length > 0) {
          setUserFields(status.user_fields);
          // Merge backend labels into defaults; backend overrides defaults
          setFieldLabelsZh(mergeLabels(DEFAULT_LABELS_ZH, status.user_field_labels_zh));
          setFieldLabelsEn(mergeLabels(DEFAULT_LABELS_EN, status.user_field_labels_en));
          setFieldLabelsJa(mergeLabels(DEFAULT_LABELS_JA, status.user_field_labels_ja));
          setFieldLabelsRu(mergeLabels(DEFAULT_LABELS_RU, status.user_field_labels_ru));
        }
      } catch {
        // Keep defaults
      }
    })();
    return () => { cancelled = true; };
  }, []);

  /**
   * Get the field label map for the current i18n language.
   * Language code matches localStorage key used by the project's i18n.ts.
   */
  const getCurrentFieldLabels = (): Record<string, string> => {
    const lang = localStorage.getItem("language") || "en";
    if (lang.startsWith("zh")) return fieldLabelsZh;
    if (lang === "ja") return fieldLabelsJa;
    if (lang === "ru") return fieldLabelsRu;
    return fieldLabelsEn; // "en" and any unknown lang
  };

  const onFinish = async (values: Record<string, string>) => {
    setLoading(true);
    try {
      const raw = searchParams.get("redirect") || "/chat";
      const redirect =
        raw.startsWith("/") && !raw.startsWith("//") ? raw : "/chat";

      const res = await muAuthApi.login(values);
      if (res.token) {
        _setAuthToken(res.token);
        // Store user info with ALL language field labels for HeaderUserMenu language switching
        storeVerifiedUserInfo(res, {
          zh: fieldLabelsZh,
          en: fieldLabelsEn,
          ja: fieldLabelsJa,
          ru: fieldLabelsRu,
        });
        sessionStorage.removeItem("qwenpaw-agent-storage");
        message.success(t("login.success", "Login successful"));
        navigate(redirect, { replace: true });
      } else {
        message.info(t("login.authNotEnabled"));
        navigate(redirect, { replace: true });
      }
    } catch {
      message.error(t("login.failed"));
    } finally {
      setLoading(false);
    }
  };

  const formItemStyle: React.CSSProperties = { marginBottom: 0 };

  /** Get a label for a field: backend labels (current language) first, then i18n, then field name. */
  const getFieldLabel = (field: string): string => {
    // 1. Backend-provided label for current language (covers custom fields like sysId, branchId)
    const currentLabels = getCurrentFieldLabels();
    if (currentLabels[field]) return currentLabels[field];
    // 2. Try i18n (covers built-in fields like username, orgId)
    const i18nKey = `login.${field}`;
    const i18nVal = t(i18nKey);
    if (i18nVal !== i18nKey) return i18nVal;
    // 3. Auto-generate from field name
    return field.replace(/([A-Z])/g, " $1").replace(/^./, (s) => s.toUpperCase());
  };

  /** Get placeholder text for a field. */
  const getFieldPlaceholder = (field: string): string => {
    const i18nKey = `login.${field}Placeholder`;
    const i18nVal = t(i18nKey);
    if (i18nVal !== i18nKey) return i18nVal;
    // Fallback: "Enter {label}"
    const label = getFieldLabel(field);
    return `${label}`;
  };

  /** Get required validation message for a field. */
  const getFieldRequired = (field: string): string => {
    const i18nKey = `login.${field}Required`;
    const i18nVal = t(i18nKey);
    if (i18nVal !== i18nKey) return i18nVal;
    const label = getFieldLabel(field);
    return label;
  };

  /** Get icon for a field based on its position and name heuristics. */
  const getFieldIcon = (field: string, index: number) => {
    const lower = field.toLowerCase();
    if (lower.includes("user") || lower.includes("sap") || lower.includes("userid"))
      return <UserOutlined style={{ color: isDark ? "rgba(255,255,255,0.45)" : undefined }} />;
    if (lower.includes("branch") || lower.includes("dept") || lower.includes("org"))
      return <ApartmentOutlined style={{ color: isDark ? "rgba(255,255,255,0.45)" : undefined }} />;
    if (lower.includes("sys") || lower.includes("system"))
      return <BankOutlined style={{ color: isDark ? "rgba(255,255,255,0.45)" : undefined }} />;
    // Cycle through icons
    const IconComp = FIELD_ICONS[index % FIELD_ICONS.length];
    return <IconComp style={{ color: isDark ? "rgba(255,255,255,0.45)" : undefined }} />;
  };

  /** Build form field rows: pair up fields 2 per row, last odd field gets its own row. */
  const renderFieldRows = () => {
    const rows: React.ReactNode[] = [];
    for (let i = 0; i < userFields.length; i += 2) {
      const field1 = userFields[i];
      const field2 = userFields[i + 1];

      if (field2) {
        // Two fields side by side
        rows.push(
          <div key={`row-${i}`} style={{ display: "flex", gap: 12 }}>
            <Form.Item
              name={field1}
              label={getFieldLabel(field1)}
              rules={[{ required: true, message: getFieldRequired(field1) }]}
              style={{ ...formItemStyle, flex: 1 }}
            >
              <Input
                prefix={getFieldIcon(field1, i)}
                placeholder={getFieldPlaceholder(field1)}
                autoFocus={i === 0}
              />
            </Form.Item>
            <Form.Item
              name={field2}
              label={getFieldLabel(field2)}
              rules={[{ required: true, message: getFieldRequired(field2) }]}
              style={{ ...formItemStyle, flex: 1 }}
            >
              <Input
                prefix={getFieldIcon(field2, i + 1)}
                placeholder={getFieldPlaceholder(field2)}
              />
            </Form.Item>
          </div>
        );
      } else {
        // Single field (last odd one)
        rows.push(
          <Form.Item
            key={`row-${i}`}
            name={field1}
            label={getFieldLabel(field1)}
            rules={[{ required: true, message: getFieldRequired(field1) }]}
            style={formItemStyle}
          >
            <Input
              prefix={getFieldIcon(field1, i)}
              placeholder={getFieldPlaceholder(field1)}
            />
          </Form.Item>
        );
      }
    }
    return rows;
  };

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: isDark
          ? "linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%)"
          : "linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%)",
      }}
    >
      <div
        style={{
          width: 420,
          maxHeight: "90vh",
          overflowY: "auto",
          padding: 32,
          borderRadius: 12,
          background: isDark ? "#1f1f1f" : "#fff",
          boxShadow: isDark
            ? "0 4px 24px rgba(0,0,0,0.4)"
            : "0 4px 24px rgba(0,0,0,0.1)",
        }}
      >
        {/* Logo + title */}
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <img
            src={`${import.meta.env.BASE_URL}${
              isDark ? "dark-logo.png" : "logo.png"
            }`}
            alt="QwenPaw"
            style={{ height: 48, marginBottom: 12 }}
          />
          <h2
            style={{
              margin: 0,
              fontWeight: 600,
              fontSize: 20,
            }}
          >
            {t("login.title")}
          </h2>
        </div>

        <Form
          layout="vertical"
          onFinish={onFinish}
          autoComplete="off"
          size="large"
        >
          {/* Dynamic user field rows */}
          {renderFieldRows()}

          {/* Password (always present) */}
          <Form.Item
            name="password"
            label={t("login.password")}
            rules={[
              { required: true, message: t("login.passwordRequired") },
            ]}
            style={formItemStyle}
          >
            <Input.Password
              prefix={
                <LockOutlined
                  style={{
                    color: isDark
                      ? "rgba(255,255,255,0.45)"
                      : undefined,
                  }}
                />
              }
              placeholder={t("login.passwordPlaceholder")}
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0, marginTop: 8 }}>
            <Button
              type="primary"
              htmlType="submit"
              loading={loading}
              block
              style={{ height: 44, borderRadius: 8, fontWeight: 500 }}
            >
              {t("login.submit")}
            </Button>
          </Form.Item>
        </Form>
      </div>
    </div>
  );
}
