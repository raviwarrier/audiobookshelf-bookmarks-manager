import React from 'react';
import { Bookmark, Library, User as UserIcon, LogIn, Trash2, ChevronDown } from 'lucide-react';
import { AbsUser } from '../types';

interface NavbarProps {
  activeView: 'capture' | 'library';
  onViewChange: (view: 'capture' | 'library') => void;
  user: AbsUser | null;
  snippetCount: number;
  onOpenAuthModal: () => void;
  onWipeSession: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({
  activeView,
  onViewChange,
  user,
  snippetCount,
  onOpenAuthModal,
  onWipeSession,
}) => {
  return (
    <header className="border-b border-neutral-800 bg-[#080808] px-4 md:px-8 py-3.5">
      <div className="max-w-6xl mx-auto flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        {/* App Title */}
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded border border-neutral-700 bg-[#141414] flex items-center justify-center shrink-0 p-1">
            <img
              src="/abs-logo-dark.svg"
              alt="Audiobookshelf Bookmarks Manager"
              className="w-full h-full object-contain hidden dark:block"
            />
            <img
              src="/abs-logo-light.svg"
              alt="Audiobookshelf Bookmarks Manager"
              className="w-full h-full object-contain block dark:hidden"
            />
          </div>
          <div className="flex items-center gap-2">
            <h1 className="text-sm md:text-base font-semibold tracking-tight text-white">
              Audiobookshelf Bookmarks Manager
            </h1>
            <span className="text-[10px] font-mono px-1.5 py-0.5 bg-neutral-800 text-neutral-400 border border-neutral-700">
              v2.1.0
            </span>
          </div>
        </div>

        {/* Navigation & User Actions */}
        <div className="flex items-center gap-2 sm:gap-3 self-end sm:self-auto">
          {/* Module Switcher Tabs */}
          <div className="flex items-center border border-neutral-700 bg-[#121212] p-0.5">
            <button
              id="nav-capture-btn"
              onClick={() => onViewChange('capture')}
              className={`flex items-center gap-2 px-3 py-1.5 text-xs font-medium transition-colors ${
                activeView === 'capture'
                  ? 'bg-neutral-200 text-black font-semibold'
                  : 'text-neutral-400 hover:text-white'
              }`}
            >
              <Bookmark className="w-3.5 h-3.5" />
              <span>Bookmark and Snip</span>
            </button>

            <button
              id="nav-library-btn"
              onClick={() => onViewChange('library')}
              className={`flex items-center gap-2 px-3 py-1.5 text-xs font-medium transition-colors ${
                activeView === 'library'
                  ? 'bg-neutral-200 text-black font-semibold'
                  : 'text-neutral-400 hover:text-white'
              }`}
            >
              <Library className="w-3.5 h-3.5" />
              <span>Snippets ({snippetCount})</span>
            </button>
          </div>

          {/* User Status / Change User Button */}
          {user ? (
            <div className="flex items-center gap-1 border border-neutral-700 bg-[#141414] px-1 py-0.5 text-xs">
              <button
                id="change-user-btn"
                onClick={onOpenAuthModal}
                title="Click to switch user or update server URLs"
                className="flex items-center gap-1.5 px-2 py-1 text-neutral-200 hover:text-white hover:bg-neutral-800 transition-colors"
              >
                <UserIcon className="w-3.5 h-3.5 text-neutral-400" />
                <span className="font-semibold text-white">{user.username}</span>
                <span className="text-[10px] text-neutral-400 bg-neutral-800 px-1.5 py-0.5 border border-neutral-700">
                  Switch
                </span>
                <ChevronDown className="w-3 h-3 text-neutral-500" />
              </button>

              <button
                id="wipe-session-btn"
                title="Disconnect & wipe credentials"
                onClick={onWipeSession}
                className="p-1 text-neutral-400 hover:text-red-400 hover:bg-neutral-800 transition-colors"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ) : (
            <button
              id="header-connect-btn"
              onClick={onOpenAuthModal}
              className="flex items-center gap-1.5 border border-neutral-600 bg-[#161616] hover:bg-[#202020] hover:border-neutral-400 px-3 py-1.5 text-xs font-medium text-white transition-colors"
            >
              <LogIn className="w-3.5 h-3.5 text-neutral-400" />
              <span>Connect User</span>
            </button>
          )}
        </div>
      </div>
    </header>
  );
};
