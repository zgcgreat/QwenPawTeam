/**
 * Console multi-user plugin — shared type definitions.
 *
 * User fields are dynamic (configured via QWENPAW_USER_FIELDS env var
 * on the backend).  The types below use `Record<string, string>` for
 * field maps.
 */

// ── User identity (dynamic fields) ─────────────────────────────────────

/** Dynamic user field map — keys match backend USER_FIELDS. */
export type UserFieldMap = Record<string, string>;

export interface UserInfo {
  user_id: string;
  /** Dynamic user fields (e.g. {username: "admin"} or {orgId: "...", deptId: "..."} ) */
  fields: UserFieldMap;
  /**
   * Field labels in all supported languages.
   * Key: language code (zh/en/ja/ru), Value: field label map.
   * HeaderUserMenu picks the right one based on current localStorage.language.
   */
  fieldLabels?: {
    zh?: Record<string, string>;
    en?: Record<string, string>;
    ja?: Record<string, string>;
    ru?: Record<string, string>;
  };
}

// ── API request / response types ─────────────────────────────────────────

export interface LoginResponse extends UserInfo {
  token: string;
}

export interface AuthStatusResponse {
  enabled: boolean;
  has_users: boolean;
  /** Backend multi_user plugin adds this field when present. */
  multi_user?: boolean;
  /** Configured user field names, e.g. ["username"] or ["orgId","deptId","userId"] */
  user_fields?: string[];
  /** Chinese label map, e.g. {"username":"用户名"} */
  user_field_labels_zh?: Record<string, string>;
  /** English label map, e.g. {"username":"Username"} */
  user_field_labels_en?: Record<string, string>;
  /** Japanese label map, e.g. {"username":"ユーザー名"} */
  user_field_labels_ja?: Record<string, string>;
  /** Russian label map, e.g. {"username":"Имя пользователя"} */
  user_field_labels_ru?: Record<string, string>;
}

export interface InitWorkspaceResponse extends UserInfo {
  initialized: boolean;
}

/** Login request — dynamic fields plus password */
export interface LoginRequest extends UserFieldMap {
  password: string;
}
