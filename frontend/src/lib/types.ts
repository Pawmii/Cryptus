export interface Quote {
  id: string; asset: string; symbol: string; asset_name: string; asset_kind: string; contract: string | null;
  decimals: number; network: string; network_name: string; environment: string; address: string;
  amount_due: string; amount_due_atomic: string; amount_seen: string; amount_confirmed: string;
  rate: string | null; rate_source: string | null; confirmations_required: number;
  payment_uri: string | null; explorer_url: string | null; qr_svg?: string;
}

export interface PaymentRow {
  id: string; txid: string; network: string; asset: string; symbol: string; amount: string; status: string;
  confirmations: number; required: number; sender: string | null; source: string; first_seen_at: string;
  confirmed_at: string | null; explorer_url: string | null;
}

export interface Flags { overpaid: boolean; paid_late: boolean; needs_review?: boolean }

export interface InvoiceRow {
  id: string; number: string; status: string; mode: string; price: { amount: string; currency: string };
  asset: string | null; network: string | null; amount_due: string | null; order_id: string | null;
  description: string | null; flags: Flags; created_at: string; expires_at: string; paid_at: string | null;
}

export interface InvoiceFull {
  id: string; number: string; mode: string; status: string; price: { amount: string; currency: string };
  allowed_assets: string[]; order_id: string | null; description: string | null; metadata: Record<string, unknown>;
  redirect_url: string | null; flags: Flags; payment: Quote | null; quotes: Quote[]; payments: PaymentRow[];
  checkout_url: string; created_at: string; expires_at: string; paid_at: string | null;
  events?: { id: string; type: string; created_at: string }[];
  deposit_details?: { id: string; keychain_id: string; index: number; path: string; prepared: boolean;
    monitor_until: string; swept_at: string | null; meta: Record<string, unknown> }[];
}

export interface Keychain {
  id: string; family: string; scope: string; label: string; mode: "watch_only" | "managed"; public_material: string;
  public_material_short: string; master_fingerprint: string | null; account_path: string | null; next_index: number;
  active: boolean; verified_at: string | null; created_at: string;
  preview?: { index: number; path: string; address: string }[]; networks?: string[];
}

export interface WalletInfo {
  network: string; keychain_id: string; keychain: Keychain | null; destination: string | null;
  destination_confirmed_at: string | null; sweep_enabled: boolean; sweep_interval_minutes: number;
  sweep_thresholds: Record<string, string>; max_fee: Record<string, string>; gas_address: string | null;
  last_sweep_run_at: string | null;
}

export interface NetworkRow {
  key: string; name: string; family: string; environment: "mainnet" | "testnet"; implementation: "implemented" | "planned";
  status: "operational" | "degraded" | "not_configured" | "disabled" | "planned"; native_symbol: string;
  chain_id: number | null; backends: string[]; backend: string | null; endpoint_url: string | null;
  has_credentials: boolean; enabled: boolean; confirmations: number; default_confirmations: number;
  options: Record<string, unknown>;
  health: { ok?: boolean; height?: number; latency_ms?: number; error?: string | null; detail?: Record<string, unknown> };
  health_checked_at: string | null; wallet: WalletInfo | null; note: string | null;
}

export interface AssetRow {
  id: string; network: string; network_name: string; environment: string; kind: string; symbol: string; name: string;
  decimals: number; contract: string | null; coingecko_id: string | null; fixed_usd_peg: boolean; enabled: boolean;
  builtin: boolean; verified_at: string | null; min_amount: string | null;
}

export interface SweepRow {
  id: string; network: string; network_name: string; asset: string; symbol: string; kind: string; status: string;
  amount: string; fee_estimate: string; fee_symbol: string; source: string | null; destination: string;
  deposits: number; txid: string | null; error: string | null; attempts: number; depends_on: string | null;
  artifact: Record<string, unknown>; created_at: string; updated_at: string; explorer_url: string | null;
}

export interface Paged<T> { total: number; items: T[] }
