/**
 * Console multi-tenant plugin — Header user menu (Dropdown).
 *
 * Extracted from CoPaw/console/src/layouts/Header.tsx.
 * Shows: user identity label + switch-user/logout dropdown.
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

interface HeaderUserMenuProps {
  /** Additional className for styling. */
  className?: string;
}

export default function HeaderUserMenu({ className }: HeaderUserMenuProps) {
  const { t } = useTranslation();
  const userInfo = getStoredUserInfo();

  const displayLabel = userInfo
    ? `${userInfo.sysId}/${userInfo.branchId}/${userInfo.sapId}`
    : "";

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

  const userMenuItems = [
    ...(userInfo
      ? [
          {
            key: "userinfo",
            label: (
              <div style={{ fontSize: 12, lineHeight: 1.8 }}>
                <div>
                  {t("login.sysId")}: {userInfo.sysId}
                </div>
                <div>
                  {t("login.branchId")}: {userInfo.branchId}
                </div>
                <div>
                  {t("login.vorgCode")}: {userInfo.vorgCode}
                </div>
                <div>
                  {t("login.sapId")}: {userInfo.sapId}
                </div>
                <div>
                  {t("login.positionId")}: {userInfo.positionId}
                </div>
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
