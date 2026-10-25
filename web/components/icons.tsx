type IconProps = { className?: string };

export function BookmarkIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
      <path fill="currentColor" d="M6.5 3.5h11a1 1 0 0 1 1 1V21l-6.5-3.6L5.5 21V4.5a1 1 0 0 1 1-1z" />
    </svg>
  );
}

export function PinIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path fill="currentColor" d="M14 3.2v6.1l2.2 2.2v1.8H13v6.2l-1 1-1-1V13.3H8.8v-1.8L11 9.3V3.2H14z" />
    </svg>
  );
}

export function SendIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path fill="currentColor" d="M3.2 11.4 20.4 4.2c.7-.3 1.4.4 1.1 1.1l-7.2 17.2c-.3.7-1.3.7-1.6 0l-3.2-6.4-6.4-3.2c-.7-.3-.7-1.3 0-1.5z" />
    </svg>
  );
}

export function PlusIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function TrashIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <path
        d="M5 7h14M9 7V5h6v2m-7 3 .6 9h6.8l.6-9"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ChatsIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path
        fill="currentColor"
        d="M5 5.5h10.5A2.5 2.5 0 0 1 18 8v5.2a2.5 2.5 0 0 1-2.5 2.5H9.2L5.5 19v-3.3A2.5 2.5 0 0 1 5 13.2V5.5z"
      />
    </svg>
  );
}

export function ArtifactIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path
        d="M14 3.5H9.2A1.7 1.7 0 0 0 7.5 5.2v9.1a1.7 1.7 0 0 0 1.7 1.7h7.6a1.7 1.7 0 0 0 1.7-1.7V8.2L14 3.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path d="M14 3.6V8.2h4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path
        d="M6.5 7.2A1.7 1.7 0 0 0 4.8 8.9v9.2A1.7 1.7 0 0 0 6.5 19.8h7.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function NetworkIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path
        d="M12 7.4 6.7 15.6M12 7.4l5.3 8.2M7.6 17.6h8.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <circle cx="12" cy="5.2" r="2.15" fill="currentColor" />
      <circle cx="5.4" cy="17.6" r="2.15" fill="currentColor" />
      <circle cx="18.6" cy="17.6" r="2.15" fill="currentColor" />
    </svg>
  );
}

export function GraphIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <circle cx="6" cy="12" r="2.2" fill="currentColor" />
      <circle cx="18" cy="7" r="2.2" fill="currentColor" />
      <circle cx="17" cy="17" r="2.2" fill="currentColor" />
      <path d="M8 11.2 15.6 8M8 12.8l7.2 3.2" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

export function LifecycleIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path
        d="M12 5a7 7 0 1 1-6.3 4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <path d="M5 4.5V9h4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function ReportFileIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 36 36" width="36" height="36" aria-hidden="true">
      <rect width="36" height="36" rx="8" fill="#e7f3fd" />
      <path
        fill="#fff"
        stroke="#3390ec"
        strokeWidth="1.3"
        strokeLinejoin="round"
        d="M11.4 9.2h7.2l7.2 6.8v11.2a1.4 1.4 0 0 1-1.4 1.4h-13a1.4 1.4 0 0 1-1.4-1.4V10.6a1.4 1.4 0 0 1 1.4-1.4z"
      />
      <path fill="#e7f3fd" stroke="#3390ec" strokeWidth="1.3" strokeLinejoin="round" d="M18.6 9.2v6.8h6.8" />
      <path d="M13.2 20.2h7.2M13.2 23.2h5" fill="none" stroke="#3390ec" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

export function DeckFileIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 36 36" width="36" height="36" aria-hidden="true">
      <rect width="36" height="36" rx="8" fill="#fdeee3" />
      <rect x="13" y="8" width="15" height="11" rx="2" fill="#f6d2b8" />
      <rect x="8" y="13" width="18" height="14" rx="2" fill="#fff" stroke="#e07a3a" strokeWidth="1.3" />
      <path d="M11 17.2h6.5" fill="none" stroke="#e07a3a" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M11 20.4h11M11 23h7.5" fill="none" stroke="#f0b48a" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

export function DownloadIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path d="M12 4.5v9" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="m8.2 10.2 3.8 3.8 3.8-3.8" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5.5 17.5v1.5h13v-1.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function BackIcon({ className }: IconProps) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path d="M14.5 6 8.5 12l6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
