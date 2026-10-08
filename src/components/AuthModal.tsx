import React, { useState, useEffect } from 'react';
import { 
  KeyRound, 
  X, 
  RotateCw, 
  AlertCircle, 
  Lock,
  Radio,
  LogOut
} from 'lucide-react';
import { AbsUser } from '../types';
import { getStoredCredentials } from '../lib/authStorage';
import { stripTrailingSlash } from '../lib/safeFetch';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  user: AbsUser | null;
  currentServerUrl: string;
  defaultServerUrl?: string;
  currentSidecarUrl: string;
  currentUseProxy: boolean;
  onConnect: (params: {
    serverUrl: string;
    sidecarUrl: string;
    useProxy: boolean;
    authMode: 'token' | 'userpass';
    token?: string;
    username?: string;
    password?: string;
    isMock?: boolean;
    remember?: boolean;
  }) => Promise<void>;
  onDisconnect: () => void;
}

function getDefaultLocalSidecar(): string {
  const clientHost = typeof window !== 'undefined' && window.location?.hostname ? window.location.hostname : 'localhost';
  const isCloudHost = clientHost.includes('run.app') || clientHost.includes('webcontainer');
  return (!isCloudHost && clientHost !== 'localhost' && clientHost !== '127.0.0.1')
    ? `http://${clientHost}:13380`
    : 'http://localhost:13380';
}

function isLocalOrPlaceholder(u?: string | null): boolean {
  if (!u || typeof u !== 'string') return true;
  const trimmed = u.trim();
  return (
    trimmed === '' ||
    trimmed === 'http://localhost:13378' ||
    trimmed === 'http://127.0.0.1:13378' ||
    trimmed === 'https://localhost:13378' ||
    trimmed.includes('abs.example.com')
  );
}

function sanitizeUrl(url?: string | null): string {
  if (!url || typeof url !== 'string') return '';
  let trimmed = stripTrailingSlash(url.trim());
  if (trimmed.includes('abs.example.com')) return '';
  if (trimmed && !trimmed.startsWith('http://') && !trimmed.startsWith('https://')) {
    const isLocalScheme = trimmed.startsWith('localhost') || trimmed.startsWith('127.0.0.1') || trimmed.startsWith('192.168.') || trimmed.startsWith('10.');
    trimmed = isLocalScheme ? `http://${trimmed}` : `https://${trimmed}`;
  }
  return trimmed;
}

function resolveCandidateServerUrl(
  defaultUrl?: string,
  savedUrl?: string,
  currentUrl?: string
): string {
  const candidates = [defaultUrl, savedUrl, currentUrl]
    .map((u) => sanitizeUrl(u))
    .filter(Boolean);

  for (const cand of candidates) {
    if (!isLocalOrPlaceholder(cand)) {
      return cand;
    }
  }
  return candidates[0] || '';
}

function resolveInitialSidecarUrl(
  sidecarUrl?: string,
  fallbackSidecar: string = getDefaultLocalSidecar()
): string {
  if (!sidecarUrl || sidecarUrl.includes('[your ip:port')) {
    return fallbackSidecar;
  }
  return sidecarUrl;
}

function stripHttpPrefix(url: string): string {
  if (url.startsWith('https://')) return url.slice(8);
  if (url.startsWith('http://')) return url.slice(7);
  return url;
}

function canShowResetButton(defaultUrl?: string, currentUrl?: string): boolean {
  if (!defaultUrl) return false;
  if (defaultUrl === currentUrl) return false;
  return !defaultUrl.includes('abs.example.com');
}

interface UserBannerProps {
  user: AbsUser | null;
  onDisconnect: () => void;
  onClose: () => void;
}

const AuthModalUserBanner: React.FC<UserBannerProps> = ({ user, onDisconnect, onClose }) => {
  if (!user) return null;
  return (
    <div className="my-4 p-3 bg-neutral-950 border border-neutral-800 flex items-center justify-between text-xs">
      <div className="flex items-center gap-2">
        <Lock className="w-3.5 h-3.5 text-neutral-400" />
        <span className="text-neutral-400">Current User:</span>
        <span className="font-semibold text-white">{user.username}</span>
      </div>
      <button
        type="button"
        onClick={() => {
          onDisconnect();
          onClose();
        }}
        className="text-neutral-400 hover:text-red-400 flex items-center gap-1 text-[11px] underline"
      >
        <LogOut className="w-3 h-3" />
        <span>Disconnect</span>
      </button>
    </div>
  );
};

interface AuthModalModeSelectProps {
  authMode: 'token' | 'userpass';
  setAuthMode: (mode: 'token' | 'userpass') => void;
}

const AuthModalModeSelect: React.FC<AuthModalModeSelectProps> = ({ authMode, setAuthMode }) => {
  const tokenLabelClass = authMode === 'token' ? 'text-white font-semibold' : 'text-neutral-400';
  const userpassLabelClass = authMode === 'userpass' ? 'text-white font-semibold' : 'text-neutral-400';
  return (
    <div className="pt-2 border-t border-neutral-800">
      <span className="block text-xs font-medium text-neutral-300 mb-2">
        Authentication Method:
      </span>
      <div className="flex items-center gap-5">
        <label htmlFor="auth-mode-token" className="flex items-center gap-2 text-xs cursor-pointer">
          <input
            id="auth-mode-token"
            type="radio"
            name="modalAuthMode"
            checked={authMode === 'token'}
            onChange={() => setAuthMode('token')}
            className="accent-neutral-200"
          />
          <span className={tokenLabelClass}>
            API Key / Bearer Token
          </span>
        </label>

        <label htmlFor="auth-mode-userpass" className="flex items-center gap-2 text-xs cursor-pointer">
          <input
            id="auth-mode-userpass"
            type="radio"
            name="modalAuthMode"
            checked={authMode === 'userpass'}
            onChange={() => setAuthMode('userpass')}
            className="accent-neutral-200"
          />
          <span className={userpassLabelClass}>
            Username & Password
          </span>
        </label>
      </div>
    </div>
  );
};

interface AuthModalCredentialFieldsProps {
  authMode: 'token' | 'userpass';
  token: string;
  setToken: (t: string) => void;
  username: string;
  setUsername: (u: string) => void;
  password: string;
  setPassword: (p: string) => void;
}

const AuthModalCredentialFields: React.FC<AuthModalCredentialFieldsProps> = ({
  authMode,
  token,
  setToken,
  username,
  setUsername,
  password,
  setPassword,
}) => {
  if (authMode === 'token') {
    return (
      <div>
        <label htmlFor="auth-modal-token-input" className="block text-xs font-medium text-neutral-300 mb-1">
          <span className="block mb-1">Audiobookshelf API Token</span>
          <input
            id="auth-modal-token-input"
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Paste API Token / Bearer Token from ABS Profile"
            className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] text-white px-3 py-2 text-xs focus:outline-none transition-colors"
          />
        </label>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <div>
        <label htmlFor="auth-modal-username-input" className="block text-xs font-medium text-neutral-300 mb-1">
          <span className="block mb-1">Username</span>
          <input
            id="auth-modal-username-input"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Your ABS username"
            className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] text-white px-3 py-2 text-xs focus:outline-none transition-colors"
          />
        </label>
      </div>
      <div>
        <label htmlFor="auth-modal-password-input" className="block text-xs font-medium text-neutral-300 mb-1">
          <span className="block mb-1">Password</span>
          <input
            id="auth-modal-password-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••••••"
            className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] text-white px-3 py-2 text-xs focus:outline-none transition-colors"
          />
        </label>
      </div>
    </div>
  );
};

export const AuthModal: React.FC<AuthModalProps> = ({
  isOpen,
  onClose,
  user,
  currentServerUrl,
  defaultServerUrl,
  currentSidecarUrl,
  currentUseProxy,
  onConnect,
  onDisconnect,
}) => {
  const defaultLocalSidecar = getDefaultLocalSidecar();
  const savedInitial = typeof window !== 'undefined' ? getStoredCredentials() : null;

  const [serverUrl, setServerUrl] = useState(() =>
    resolveCandidateServerUrl(defaultServerUrl, savedInitial?.serverUrl, currentServerUrl)
  );
  const [rememberCredentials, setRememberCredentials] = useState(savedInitial?.remember ?? true);
  const [sidecarUrl, setSidecarUrl] = useState(
    savedInitial?.sidecarUrl ?? resolveInitialSidecarUrl(currentSidecarUrl, defaultLocalSidecar)
  );
  const [useProxy, setUseProxy] = useState(savedInitial?.useProxy ?? currentUseProxy);
  const [authMode, setAuthMode] = useState<'token' | 'userpass'>(savedInitial?.authMode ?? 'token');

  const [token, setToken] = useState(savedInitial?.token ?? '');
  const [username, setUsername] = useState(savedInitial?.username ?? '');
  const [password, setPassword] = useState('');

  const [isConnecting, setIsConnecting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Automatically adopt configured server URL when /api/config loads asynchronously
  useEffect(() => {
    if (!defaultServerUrl) return;
    const cleanDefault = sanitizeUrl(defaultServerUrl);
    if (!cleanDefault || isLocalOrPlaceholder(cleanDefault)) return;

    setServerUrl((prev) => {
      // If current input is empty, local, placeholder, or not a custom server, auto-fill with the configured default
      if (isLocalOrPlaceholder(prev)) {
        return cleanDefault;
      }
      return prev;
    });
  }, [defaultServerUrl]);

  const applySavedCredentials = (
    saved: NonNullable<ReturnType<typeof getStoredCredentials>>,
    candidate: string
  ) => {
    if (candidate) setServerUrl(candidate);
    if (saved.sidecarUrl) setSidecarUrl(saved.sidecarUrl);
    if (saved.useProxy !== undefined) setUseProxy(saved.useProxy);
    if (saved.authMode) setAuthMode(saved.authMode);
    if (saved.token) setToken(saved.token);
    if (saved.username) setUsername(saved.username);
    setRememberCredentials(saved.remember ?? true);
  };

  const applyDefaultCredentials = (candidate: string) => {
    setServerUrl(candidate);
    setSidecarUrl(resolveInitialSidecarUrl(currentSidecarUrl, defaultLocalSidecar));
    setUseProxy(currentUseProxy);
  };

  useEffect(() => {
    if (!isOpen) return;
    const saved = getStoredCredentials();
    const candidate = resolveCandidateServerUrl(
      defaultServerUrl,
      saved?.serverUrl,
      currentServerUrl
    );

    if (saved) {
      applySavedCredentials(saved, candidate);
    } else {
      applyDefaultCredentials(candidate);
    }
    setErrorMsg(null);
  }, [isOpen, defaultServerUrl, currentServerUrl]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);

    if (authMode === 'token' && !token.trim()) {
      setErrorMsg('Please enter an Audiobookshelf API Token or Bearer Token.');
      return;
    }

    if (authMode === 'userpass' && (!username.trim() || !password)) {
      setErrorMsg('Please enter both your Audiobookshelf Username and Password.');
      return;
    }

    setIsConnecting(true);
    try {
      await onConnect({
        serverUrl: serverUrl.trim(),
        sidecarUrl: sidecarUrl.trim(),
        useProxy,
        authMode,
        token: token.trim(),
        username: username.trim(),
        password,
        isMock: false,
        remember: rememberCredentials,
      });
      // Clear sensitive unencrypted temporary password input from state
      setPassword('');
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Authentication failed';
      setErrorMsg(msg);
    } finally {
      setIsConnecting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/85 backdrop-blur-sm flex items-center justify-center p-4 font-mono">
      <div className="w-full max-w-xl bg-[#0c0c0c] border border-neutral-700 shadow-2xl p-5 sm:p-6 relative overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        
        {/* Header */}
        <div className="flex items-start justify-between pb-4 border-b border-neutral-800">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 bg-neutral-900 border border-neutral-700 flex items-center justify-center text-white">
              <KeyRound className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-semibold text-white tracking-tight uppercase">
                {user ? 'Change Audiobookshelf User' : 'Connect to Audiobookshelf'}
              </h2>
            </div>
          </div>

          {/* Close button */}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-neutral-500 hover:text-white transition-colors p-1"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Currently Connected Info (if switching user) */}
        <AuthModalUserBanner user={user} onDisconnect={onDisconnect} onClose={onClose} />

        {/* Form */}
        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          
          {/* Server URL */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="block text-xs font-medium text-neutral-300">
                Audiobookshelf Server URL
              </span>
              {canShowResetButton(defaultServerUrl, serverUrl) && defaultServerUrl && (
                <button
                  type="button"
                  onClick={() => setServerUrl(defaultServerUrl)}
                  className="text-[10px] text-neutral-400 hover:text-white underline cursor-pointer"
                >
                  Reset to Server Default ({stripHttpPrefix(defaultServerUrl)})
                </button>
              )}
            </div>

            <label htmlFor="auth-modal-server-url-input" className="block relative">
              <span className="sr-only">Audiobookshelf Server URL</span>
              <input
                id="auth-modal-server-url-input"
                type="url"
                required
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                placeholder="https://192.168.1.100:13378 or https://audiobooks.yourdomain.com"
                className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] text-white px-3 py-2 text-xs focus:outline-none transition-colors font-mono"
              />
            </label>
            <p className="mt-1 text-[11px] text-neutral-500">
              Address of your Audiobookshelf server (e.g. host LAN IP with port 13378 or reverse proxy domain).
            </p>
          </div>

          {/* Auth Mode Select */}
          <AuthModalModeSelect authMode={authMode} setAuthMode={setAuthMode} />

          {/* Inputs depending on mode */}
          <AuthModalCredentialFields
            authMode={authMode}
            token={token}
            setToken={setToken}
            username={username}
            setUsername={setUsername}
            password={password}
            setPassword={setPassword}
          />

          {/* Error message */}
          {errorMsg && (
            <div className="p-3 bg-neutral-950 border border-neutral-600 text-xs text-neutral-200 flex items-start gap-2.5">
              <AlertCircle className="w-4 h-4 shrink-0 text-white mt-0.5" />
              <div className="space-y-1">
                <div className="font-semibold text-white">Connection Error</div>
                <div className="text-neutral-300">{errorMsg}</div>
              </div>
            </div>
          )}

          {/* Remember Connection Checkbox */}
          <div className="pt-2 border-t border-neutral-800/80 flex items-center justify-between">
            <label htmlFor="auth-modal-remember-checkbox" className="flex items-center gap-2 text-xs text-neutral-300 hover:text-white cursor-pointer select-none">
              <input
                id="auth-modal-remember-checkbox"
                type="checkbox"
                checked={rememberCredentials}
                onChange={(e) => setRememberCredentials(e.target.checked)}
                className="accent-neutral-200 w-3.5 h-3.5 cursor-pointer"
              />
              <span>Remember connection on this device</span>
            </label>
          </div>

          {/* Action Buttons */}
          <div className="pt-3 border-t border-neutral-800 flex flex-col sm:flex-row items-stretch sm:items-center justify-end gap-3">
            <div className="flex items-center justify-end gap-2.5 order-1 sm:order-2 shrink-0">
              <button
                type="button"
                onClick={onClose}
                disabled={isConnecting}
                className="px-3 py-2 border border-neutral-700 hover:border-neutral-500 text-xs text-neutral-300 hover:text-white transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isConnecting}
                className="px-4 py-2 bg-neutral-100 text-black hover:bg-white text-xs font-semibold flex items-center justify-center gap-2 disabled:opacity-50 transition-colors shadow-sm shrink-0 whitespace-nowrap"
              >
                {isConnecting ? (
                  <>
                    <RotateCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Connecting...</span>
                  </>
                ) : (
                  <>
                    <Radio className="w-3.5 h-3.5" />
                    <span>Connect & Load Session</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
