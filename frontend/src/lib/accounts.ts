/** Saved Kotak Neo accounts, as the server describes them. Secrets are never sent to the browser: only masked values and "saved" flags. */
export interface Account {
  id: string;
  label: string;
  source: "saved" | "env";
  ucc: string;
  mobile: string;
  token: string;
  has_mpin: boolean;
  has_totp_secret: boolean;
  static_ip: string;
  default: boolean;
  connected: boolean;
  editable: boolean;
}
export interface AccountsResp {
  ok: boolean;
  mode: "sample" | "paper" | "live";
  can_connect: boolean;
  connected: boolean;
  active: string | null;
  default: string;
  readable: boolean;
  problem: string;
  accounts: Account[];
}
export interface AccountForm {
  label: string;
  consumer_key: string;
  mobile: string;
  ucc: string;
  mpin: string;
  totp_secret: string;
  clear_totp_secret: boolean;
  static_ip: string;
}
export const emptyForm = (): AccountForm => ({ label: "", consumer_key: "", mobile: "", ucc: "", mpin: "", totp_secret: "", clear_totp_secret: false, static_ip: "" });
