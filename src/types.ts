export interface AbsCredentials {
  serverUrl: string;
  authMode: 'token' | 'userpass';
  token?: string;
  username?: string;
  password?: string;
}

export interface EncryptedVault {
  ciphertext: ArrayBuffer;
  iv: Uint8Array;
  isEncrypted: boolean;
}

export interface AbsUser {
  id: string;
  username: string;
  type?: string;
}

export interface AbsBookmark {
  id?: string;
  title: string;
  time: number;
  createdAt?: number;
}

export interface AbsActiveSession {
  libraryItemId: string;
  episodeId?: string | null;
  bookTitle: string;
  subtitle?: string;
  author: string;
  chapterName: string;
  currentTime: number;
  audioFilePath: string;
  duration?: number;
  coverPath?: string;
  bookmarks?: AbsBookmark[];
}

export interface Snippet {
  id: string;
  bookTitle: string;
  subtitle?: string;
  author: string;
  chapterName: string;
  timestamp: string;
  startTime: number;
  currentTime?: number;
  duration: number;
  audioUrl: string;
  transcript: string;
  markdownContent: string;
  createdAt: number;
  userId?: string;
  username?: string;
  libraryItemId?: string;
  extractionStatus?: 'success' | 'unavailable' | string;
}

export interface StoredCredentials {
  serverUrl: string;
  sidecarUrl?: string;
  useProxy?: boolean;
  authMode: 'token' | 'userpass';
  token?: string;
  username?: string;
  password?: string;
  remember: boolean;
}

export interface SyncState {
  is_syncing: boolean;
  last_synced_at: string | null;
  total_synced: number;
  current_item?: string | null;
  last_error?: string | null;
  installation_date?: string;
  cutoff_datetime?: string;
  cutoff_mode?: 'from_start' | 'custom_date' | 'from_now';
  custom_cutoff_date?: string;
  installed_at?: string;
  skipped_before_cutoff?: number;
  skipped_tombstoned?: number;
}

export type CutoffMode = 'from_start' | 'custom_date' | 'from_now';

export interface CutoffConfig {
  cutoff_mode: CutoffMode;
  custom_date?: string;
  installation_date?: string;
  cutoff_datetime?: string;
  cutoff_timestamp?: number;
  installed_at?: string;
  note?: string;
}
