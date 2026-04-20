/**
 * Console multi-tenant plugin — shared type definitions.
 *
 * Mirrors the Python-side types from backend plugin constants.py + auth_extension,
 * plus CoPaw-specific frontend types from App.tsx and api/modules/auth.ts.
 */

// ── User identity (5-tuple) ──────────────────────────────────────────────

export interface TenantUserInfo {
  tenant_id: string;
  sysId: string;
  branchId: string;
  vorgCode: string;
  sapId: string;
  positionId: string;
}

// ── API request / response types ─────────────────────────────────────────

export interface LoginResponse extends TenantUserInfo {
  token: string;
}

export interface AuthStatusResponse {
  enabled: boolean;
  has_users: boolean;
  /** Backend multi_tenant plugin adds this field when present. */
  multi_tenant?: boolean;
}

export interface InitWorkspaceResponse {
  tenant_id: string;
  initialized: boolean;
  sysId: string;
  branchId: string;
  vorgCode: string;
  sapId: string;
  positionId: string;
}

export interface LoginRequest {
  sysId: string;
  branchId: string;
  vorgCode: string;
  sapId: string;
  positionId: string;
  password: string;
}
