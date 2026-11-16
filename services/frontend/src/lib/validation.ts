// Client-side mirror of the server's TEXT pattern in central_api/app.py.
export const REASON_PATTERN = /^[A-Za-z0-9_.:\- ]{1,64}$/;
export const REASON_MAX = 64;
export const OVERRIDE_TTL_MIN = 1;
export const OVERRIDE_TTL_MAX = 86400;

export interface ValidationResult {
  ok: boolean;
  error: string | null;
}

export interface TtlValidation extends ValidationResult {
  value: number | null;
}

export function validateReason(reason: string): ValidationResult {
  if (reason.length === 0) return { ok: false, error: "A reason is required." };
  if (reason.length > REASON_MAX) return { ok: false, error: `Reason must be at most ${REASON_MAX} characters.` };
  if (!REASON_PATTERN.test(reason)) {
    return { ok: false, error: "Only letters, digits, space and _ . : - are allowed." };
  }
  if (reason.trim().length === 0) return { ok: false, error: "Reason cannot be only spaces." };
  return { ok: true, error: null };
}

export function validateTtl(raw: string): TtlValidation {
  if (!/^\d{1,6}$/.test(raw)) return { ok: false, error: "TTL must be a whole number of seconds.", value: null };
  const n = Number(raw);
  if (n < OVERRIDE_TTL_MIN || n > OVERRIDE_TTL_MAX) {
    return { ok: false, error: `TTL must be between ${OVERRIDE_TTL_MIN} and ${OVERRIDE_TTL_MAX} s.`, value: null };
  }
  return { ok: true, error: null, value: n };
}
