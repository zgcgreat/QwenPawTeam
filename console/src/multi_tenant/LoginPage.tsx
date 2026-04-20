/**
 * Console multi-tenant plugin — 5-tuple login page.
 *
 * Ported from CoPaw/console/src/pages/Login/index.tsx with:
 * - 6-field form (sysId, branchId, vorgCode, sapId, positionId, password)
 * - Auto-registration on first successful login
 * - Local logo from public/ assets
 */

import { useState } from "react";
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
import { mtAuthApi } from "./authApi";
import { setAuthToken as _setAuthToken } from "../api/config";
import { storeVerifiedUserInfo } from "./userContext";
import { useTheme } from "../contexts/ThemeContext";

export default function MtLoginPage() {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { isDark } = useTheme();
  const [loading, setLoading] = useState(false);
  const { message } = useAppMessage();

  const onFinish = async (values: {
    sysId: string;
    branchId: string;
    vorgCode: string;
    sapId: string;
    positionId: string;
    password: string;
  }) => {
    setLoading(true);
    try {
      const raw = searchParams.get("redirect") || "/chat";
      const redirect =
        raw.startsWith("/") && !raw.startsWith("//") ? raw : "/chat";

      const res = await mtAuthApi.login(values);
      if (res.token) {
        _setAuthToken(res.token);
        storeVerifiedUserInfo(res);
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
          {/* Row 1: System ID + Branch */}
          <div style={{ display: "flex", gap: 12 }}>
            <Form.Item
              name="sysId"
              label={t("login.sysId")}
              rules={[
                { required: true, message: t("login.sysIdRequired") },
              ]}
              style={{ ...formItemStyle, flex: 1 }}
            >
              <Input
                prefix={
                  <BankOutlined
                    style={{
                      color: isDark
                        ? "rgba(255,255,255,0.45)"
                        : undefined,
                    }}
                  />
                }
                placeholder={t("login.sysIdPlaceholder")}
                autoFocus
              />
            </Form.Item>
            <Form.Item
              name="branchId"
              label={t("login.branchId")}
              rules={[
                { required: true, message: t("login.branchIdRequired") },
              ]}
              style={{ ...formItemStyle, flex: 1 }}
            >
              <Input placeholder={t("login.branchIdPlaceholder")} />
            </Form.Item>
          </div>

          {/* Row 2: Center + User ID */}
          <div style={{ display: "flex", gap: 12 }}>
            <Form.Item
              name="vorgCode"
              label={t("login.vorgCode")}
              rules={[
                { required: true, message: t("login.vorgCodeRequired") },
              ]}
              style={{ ...formItemStyle, flex: 1 }}
            >
              <Input
                prefix={
                  <ApartmentOutlined
                    style={{
                      color: isDark
                        ? "rgba(255,255,255,0.45)"
                        : undefined,
                    }}
                  />
                }
                placeholder={t("login.blgorgidPlaceholder")}
              />
            </Form.Item>
            <Form.Item
              name="sapId"
              label={t("login.sapId")}
              rules={[
                { required: true, message: t("login.sapIdRequired") },
              ]}
              style={{ ...formItemStyle, flex: 1 }}
            >
              <Input
                prefix={
                  <UserOutlined
                    style={{
                      color: isDark
                        ? "rgba(255,255,255,0.45)"
                        : undefined,
                    }}
                  />
                }
                placeholder={t("login.useridPlaceholder")}
              />
            </Form.Item>
          </div>

          {/* Row 3: Position ID */}
          <Form.Item
            name="positionId"
            label={t("login.positionId")}
            rules={[
              { required: true, message: t("login.positionIdRequired") },
            ]}
            style={formItemStyle}
          >
            <Input placeholder={t("login.positionIdPlaceholder")} />
          </Form.Item>

          {/* Row 4: Password */}
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
