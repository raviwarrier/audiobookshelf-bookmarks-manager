/**
 * Web Cryptography API in-memory credential encryption.
 * Keeps sensitive ABS API keys and passwords encrypted in memory with an ephemeral AES-256-GCM key.
 * Credentials are NEVER written to localStorage, cookies, or IndexedDB.
 */

let ephemeralSessionKey: CryptoKey | null = null;

async function getOrCreateKey(): Promise<CryptoKey> {
  if (!ephemeralSessionKey) {
    ephemeralSessionKey = await window.crypto.subtle.generateKey(
      {
        name: 'AES-GCM',
        length: 256,
      },
      false, // non-extractable key: cannot be exported or leaked
      ['encrypt', 'decrypt']
    );
  }
  return ephemeralSessionKey;
}

export interface EncryptedData {
  ciphertext: ArrayBuffer;
  iv: Uint8Array;
}

export async function encryptInMemory(plaintext: string): Promise<EncryptedData> {
  const key = await getOrCreateKey();
  const iv = window.crypto.getRandomValues(new Uint8Array(12));
  const encoded = new TextEncoder().encode(plaintext);

  const ciphertext = await window.crypto.subtle.encrypt(
    {
      name: 'AES-GCM',
      iv: iv,
    },
    key,
    encoded
  );

  return { ciphertext, iv };
}

export async function decryptInMemory(encrypted: EncryptedData): Promise<string> {
  const key = await getOrCreateKey();
  const decrypted = await window.crypto.subtle.decrypt(
    {
      name: 'AES-GCM',
      iv: encrypted.iv as Uint8Array,
    },
    key,
    encrypted.ciphertext
  );

  return new TextDecoder().decode(decrypted);
}

export function wipeSessionKey(): void {
  ephemeralSessionKey = null;
}
