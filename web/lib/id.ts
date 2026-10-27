// Safari leaves crypto.randomUUID unset on a page that is not a secure context.
export function randomId(): string {
  const cryptoObj = globalThis.crypto;
  if (typeof cryptoObj?.randomUUID === "function") {
    return cryptoObj.randomUUID().replace(/-/g, "");
  }
  const bytes = new Uint8Array(16);
  cryptoObj.getRandomValues(bytes);
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
