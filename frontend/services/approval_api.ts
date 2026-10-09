/**
 * WHY this file exists: it is the ONLY place in the frontend permitted to call the
 * M4.9 approval endpoints (the `market_data_api.ts` choke-point law, restated for
 * this route). Components call typed functions here; they never call `fetch` and
 * never learn the endpoint path - a contract change lands in this one file, not in
 * every surface that renders an approval state.
 *
 * Two decisions worth knowing:
 *
 * 1. ROLE IS AN EXPLICIT ARGUMENT, never a module global. The backend normalises
 *    `X-Role` exactly like the account router (strip + lower); no argument means no
 *    header means 403 - fail-closed on both sides of the wire, and no stale role
 *    can leak between surfaces.
 *
 * 2. ERRORS CARRY THE FROZEN `error.code` plus the server's `retryable` verdict, so
 *    the panel switches on code alone: 409 -> inline message (row untouched),
 *    503 -> banner (row untouched), anything else -> inline message. No failure path
 *    mutates local state optimistically (spec m4.9 section 3).
 */

export type ApprovalState =
  | "draft"
  | "testing"
  | "approved"
  | "rejected"
  | "discontinued";

/** One registry strategy with its effective M4.8 state (GET response row). */
export interface ApprovalRow {
  name: string;
  strategy_id: string;
  version: string;
  state: ApprovalState;
  /** null = implicit draft (no store row yet); the UI renders an em dash. */
  changed_by: string | null;
  /** Server-stamped UTC instant, rendered exactly as returned - never re-rounded. */
  changed_at: string | null;
  note: string | null;
}

/**
 * POST body. `changed_at` is deliberately absent: the store is the only clock
 * (a client timestamp is rejected server-side as an unknown extra field).
 */
export interface SetStateInput {
  strategy_id: string;
  version: string;
  new_state: ApprovalState;
  note?: string | null;
  changed_by?: string;
}

/** POST response - the store's read-back proof, used to replace the row verbatim. */
export interface ApprovalUpdate {
  strategy_id: string;
  version: string;
  state: ApprovalState;
  changed_by: string;
  changed_at: string;
  note: string | null;
}

export class ApprovalApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;

  constructor(code: string, message: string, status: number, retryable: boolean) {
    super(message);
    this.name = "ApprovalApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

interface Envelope<T> {
  data: T | null;
  meta: {
    correlation_id: string;
    as_of: string;
    schema_version: string;
    next_cursor?: string | null;
    has_more?: boolean;
  };
  error: { code: string; message: string; retryable?: boolean } | null;
}

const BASE = "/api/v1/strategy-approvals";

async function call<T>(
  role: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "X-Role": role,
      "X-Correlation-Id": crypto.randomUUID(),
      ...(init?.headers ?? {}),
    },
  });

  let body: Envelope<T>;
  try {
    body = (await response.json()) as Envelope<T>;
  } catch {
    // A non-JSON body means something in front of the API answered instead of it -
    // a deployment problem, reported as one rather than as "no approval state".
    throw new ApprovalApiError(
      "bad_response",
      `non-JSON response from ${BASE}${path}`,
      response.status,
      response.status === 503,
    );
  }
  if (body.error !== null) {
    throw new ApprovalApiError(
      body.error.code,
      body.error.message,
      response.status,
      body.error.retryable ?? response.status === 503,
    );
  }
  if (body.data === null) {
    throw new ApprovalApiError(
      "http_error",
      `${BASE}${path} returned no data`,
      response.status,
      response.status === 503,
    );
  }
  return body.data;
}

/** Every registry strategy with its effective state (implicit drafts included). */
export function listApprovals(role: string): Promise<ApprovalRow[]> {
  return call<{ strategies: ApprovalRow[] }>(role, "").then(
    (data) => data.strategies,
  );
}

/** One legal transition; the response is the store's read-back proof. */
export function setApprovalState(
  role: string,
  input: SetStateInput,
): Promise<ApprovalUpdate> {
  return call<ApprovalUpdate>(role, "/state", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}
