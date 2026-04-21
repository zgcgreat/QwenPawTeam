/**
 * Console multi-user plugin — Header user menu (Dropdown).
 *
 * Shows: user identity label + switch-user/logout dropdown.
 * Dynamic rendering from stored user field map.
 */

import { Dropdown } from "antd";
import {
  UserOutlined,
  LogoutOutlined,
  UserAddOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { getStoredUserInfo, clearUserSession } from "./userContext";
import { clearAuthToken } from "../api/config";
import type { UserInfo } from "./types";

/** Pick the label map matching the current UI language. */
function getCurrentLabels(userInfo: UserInfo | null): Record<string, string> | undefined {
  const lang = localStorage.getItem("language") || "en";
  const labels = userInfo?.fieldLabels;
  if (!labels) return undefined;
  if (lang.startsWith("zh")) return labels.zh;
  if (lang === "ja") return labels.ja;
  if (lang === "ru") return labels.ru;
  return labels.en;
}

export default function HeaderUserMenu({ className }: { className?: string }) {
  const { t } = useTranslation();
  const userInfo = getStoredUserInfo();

  const displayLabel = userInfo
    ? Object.values(userInfo.fields).slice(0, 3).join("/")
    : "";

  const getFieldLabel = (fieldKey: string): string => {
    // 1. Stored label (current language)
    const currentLabels = getCurrentLabels(userInfo);
    if (currentLabels?.[fieldKey]) return currentLabels[fieldKey];
    // 2. i18n fallback
    const i18nVal = t(`login.${fieldKey}`, fieldKey);
    if (i18nVal !== fieldKey) return i18nVal;
    // 3. Field key itself
    return fieldKey;
  };

  const handleSwitchUser = () => {
    clearAuthToken();
    clearUserSession();
    window.location.href = "/login";
  };

  const handleLogout = () => {
    clearAuthToken();
    clearUserSession();
    window.location.href = "/login";
  };

  const userInfoLines = userInfo
    ? Object.entries(userInfo.fields).map(([key, value]) => (
        <div key={key}>
          {getFieldLabel(key)}: {value}
        </div>
      ))
    : [];

  const userMenuItems = [
    ...(userInfo
      ? [
          {
            key: "userinfo",
            label: (
              <div style={{ fontSize: 12, lineHeight: 1.8 }}>
                {userInfoLines}
              </div>
            ),
            disabled: true,
          } as const,
          { type: "divider" as const },
        ]
      : []),
    {
      key: "switch",
      icon: <UserAddOutlined />,
      label: t("header.switchUser", "Switch User"),
      onClick: handleSwitchUser,
    },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: t("login.logout"),
      onClick: handleLogout,
    },
  ];

  if (!displayLabel) return null;

  return (
    <Dropdown
      menu={{ items: userMenuItems }}
      placement="bottomRight"
      className={className}
    >
      {/* Rendered as a slot — parent controls the Button wrapper */}
      <span
        role="button"
        tabIndex={0}
        style={{
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: 14,
        }}
      >
        <UserOutlined />
        {displayLabel}
      </span>
    </Dropdown>
  );
}
