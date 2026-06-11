import { getApiUrl, getApiToken } from "../config";

export interface LoginResponse {
  token: string;
  username: string;
  message?: string;
}

export interface AuthStatusResponse {
  enabled: boolean;
  has_users: boolean;
  /** Backend multi-user plugin adds this field when present. */
  multi_user?: boolean;
  /** Configured user field names when multi-user is active. */
  user_fields?: string[];
}

export const authApi = {
  login: async (username: string, password: string): Promise<LoginResponse> => {
    const res = await fetch(getApiUrl("/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Login failed");
    }
    return res.json();
  },

  register: async (
    username: string,
    password: string,
  ): Promise<LoginResponse> => {
    const res = await fetch(getApiUrl("/auth/register"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Registration failed");
    }
    return res.json();
  },

  getStatus: async (): Promise<AuthStatusResponse> => {
    const res = await fetch(getApiUrl("/auth/status"));
    if (!res.ok) throw new Error("Failed to check auth status");
    return res.json();
  },

  updateProfile: async (
    currentPassword: string,
    newUsername?: string,
    newPassword?: string,
  ): Promise<LoginResponse> => {
    const token = getApiToken();
    const res = await fetch(getApiUrl("/auth/update-profile"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        current_password: currentPassword,
        new_username: newUsername || null,
        new_password: newPassword || null,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Update failed");
    }
    return res.json();
  },
};
