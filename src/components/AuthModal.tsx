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
  // Compute default sidecar URL based on client host or standard 13380
  const clientHost = typeof window !== 'undefined' && window.location?.hostname ? window.location.hostname : 'localhost';
  const isCloudHost = clientHost.includes('run.app') || clientHost.includes('webcontainer');
  const defaultLocalSidecar = (!isCloudHost && clientHost !== 'localhost' && clientHost !== '127.0.0.1')
    ? `http://${clientHost}:13380`
    : 'http://localhost:13380';

  const isLocalOrPlaceholder = (u?: string | null) => {
    if (!u || typeof u !== 'string') return true;
    const trimmed = u.trim();
    return (
      trimmed === '' ||
      trimmed === 'http://localhost:13378' ||
      trimmed === 'http://127.0.0.1:13378' ||
      trimmed === 'https://localhost:13378' ||
      trimmed.includes('abs.example.com')
    );
  };

  const sanitizeUrl = (url?: string | null) => {
    if (!url || typeof url !== 'string') return '';
    let trimmed = url.trim().replace(/\/+$/, '');
    if (trimmed.includes('abs.example.com')) return '';
    if (trimmed && !trimmed.startsWith('http://') && !trimmed.startsWith('https://')) {
      if (trimmed.startsWith('localhost') || trimmed.startsWith('127.0.0.1') || trimmed.startsWith('192.168.') || trimmed.startsWith('10.')) {
        trimmed = `http://${trimmed}`;
      } else {
        trimmed = `https://${trimmed}`;
      }
    }
    return trimmed;
  };

  const savedInitial = typeof window !== 'undefined' ? getStoredCredentials() : null;

  const computeBestServerCandidate = () => {
    const saved = sanitizeUrl(savedInitial?.serverUrl);
    const def = sanitizeUrl(defaultServerUrl);
    const curr = sanitizeUrl(currentServerUrl);

    // 1. If saved credentials has a real, customized server (not dummy localhost), keep it
    if (saved && !isLocalOrPlaceholder(saved)) return saved;
    // 2. Prioritize configured server default (e.g. books.raviwarrier.net from ecosystem.config.cjs)
    if (def && !isLocalOrPlaceholder(def)) return def;
    // 3. Fallback to current non-placeholder server
    if (curr && !isLocalOrPlaceholder(curr)) return curr;
    // 4. Fallback to whatever default or current or localhost
    return def || curr || saved || 'http://localhost:13378';
  };

  const [serverUrl, setServerUrl] = useState(computeBestServerCandidate);
  const [rememberCredentials, setRememberCredentials] = useState(savedInitial?.remember ?? true);
  const [sidecarUrl, setSidecarUrl] = useState(
    savedInitial?.sidecarUrl || (currentSidecarUrl && !currentSidecarUrl.includes('[your ip:port') ? currentSidecarUrl : defaultLocalSidecar)
  );
  const [useProxy, setUseProxy] = useState(savedInitial?.useProxy !== undefined ? savedInitial.useProxy : currentUseProxy);
  const [authMode, setAuthMode] = useState<'token' | 'userpass'>(savedInitial?.authMode || 'token');

  const [token, setToken] = useState(savedInitial?.token || '');
  const [username, setUsername] = useState(savedInitial?.username || '');
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

  useEffect(() => {
    if (!isOpen) return;
    const saved = getStoredCredentials();
    const savedCandidate = sanitizeUrl(saved?.serverUrl);
    const defCandidate = sanitizeUrl(defaultServerUrl);
    const currCandidate = sanitizeUrl(currentServerUrl);

    let candidate = '';
    if (savedCandidate && !isLocalOrPlaceholder(savedCandidate)) {
      candidate = savedCandidate;
    } else if (defCandidate && !isLocalOrPlaceholder(defCandidate)) {
      candidate = defCandidate;
    } else if (currCandidate && !isLocalOrPlaceholder(currCandidate)) {
      candidate = currCandidate;
    } else {
      candidate = defCandidate || currCandidate || savedCandidate || 'http://localhost:13378';
    }

    if (saved) {
      if (candidate) setServerUrl(candidate);
      if (saved.sidecarUrl) setSidecarUrl(saved.sidecarUrl);
      if (saved.useProxy !== undefined) setUseProxy(saved.useProxy);
      if (saved.authMode) setAuthMode(saved.authMode);
      if (saved.token) setToken(saved.token);
      if (saved.username) setUsername(saved.username);
      setRememberCredentials(saved.remember ?? true);
    } else {
      setServerUrl(candidate);
      if (!currentSidecarUrl || currentSidecarUrl.includes('[your ip:port')) {
        setSidecarUrl(defaultLocalSidecar);
      } else {
        setSidecarUrl(currentSidecarUrl);
      }
      setUseProxy(currentUseProxy);
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

  const handleMockConnect = async () => {
    setErrorMsg(null);
    setIsConnecting(true);
    try {
      await onConnect({
        serverUrl,
        sidecarUrl,
        useProxy,
        authMode: 'token',
        token: 'sample_demo_token',
        isMock: true,
      });
      onClose();
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to load test session');
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
        {user && (
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
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="mt-4 space-y-4">
          
          {/* Server URL */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-xs font-medium text-neutral-300">
                Audiobookshelf Server URL
              </label>
              {defaultServerUrl && defaultServerUrl !== serverUrl && !defaultServerUrl.includes('abs.example.com') && (
                <button
                  type="button"
                  onClick={() => setServerUrl(defaultServerUrl)}
                  className="text-[10px] text-neutral-400 hover:text-white underline cursor-pointer"
                >
                  Reset to Server Default ({defaultServerUrl.replace(/^https?:\/\//, '')})
                </button>
              )}
            </div>

            <div className="relative flex items-center">
              <input
                type="url"
                required
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                placeholder="http://192.168.1.100:13378 or https://audiobooks.yourdomain.com"
                className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] text-white px-3 py-2 text-xs focus:outline-none transition-colors font-mono"
              />
            </div>
            <p className="mt-1 text-[11px] text-neutral-500">
              Address of your Audiobookshelf server (e.g. host LAN IP with port 13378 or reverse proxy domain).
            </p>
          </div>

          {/* Auth Mode Select */}
          <div className="pt-2 border-t border-neutral-800">
            <span className="block text-xs font-medium text-neutral-300 mb-2">
              Authentication Method:
            </span>
            <div className="flex items-center gap-5">
              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input
                  type="radio"
                  name="modalAuthMode"
                  checked={authMode === 'token'}
                  onChange={() => setAuthMode('token')}
                  className="accent-neutral-200"
                />
                <span className={authMode === 'token' ? 'text-white font-semibold' : 'text-neutral-400'}>
                  API Key / Bearer Token
                </span>
              </label>

              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input
                  type="radio"
                  name="modalAuthMode"
                  checked={authMode === 'userpass'}
                  onChange={() => setAuthMode('userpass')}
                  className="accent-neutral-200"
                />
                <span className={authMode === 'userpass' ? 'text-white font-semibold' : 'text-neutral-400'}>
                  Username & Password
                </span>
              </label>
            </div>
          </div>

          {/* Inputs depending on mode */}
          {authMode === 'token' ? (
            <div>
              <label className="block text-xs font-medium text-neutral-300 mb-1">
                Audiobookshelf API Token
              </label>
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Paste API Token / Bearer Token from ABS Profile"
                className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] text-white px-3 py-2 text-xs focus:outline-none transition-colors"
              />
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1">
                  Username
                </label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Your ABS username"
                  className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] text-white px-3 py-2 text-xs focus:outline-none transition-colors"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-neutral-300 mb-1">
                  Password
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••••••"
                  className="w-full bg-[#181818] border border-neutral-700 hover:border-neutral-500 focus:border-neutral-300 focus:bg-[#202020] text-white px-3 py-2 text-xs focus:outline-none transition-colors"
                />
              </div>
            </div>
          )}

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
            <label className="flex items-center gap-2 text-xs text-neutral-300 hover:text-white cursor-pointer select-none">
              <input
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
