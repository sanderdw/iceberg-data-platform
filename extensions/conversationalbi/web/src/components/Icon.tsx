// The platform portals' line icons (user_portal/public/app.js), 24px grid, 1.5 stroke.
const PATHS = {
  "semantic-model": (
    <>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M8.5 6h7M7 8.2l4 7.6M17 8.2l-4 7.6" />
    </>
  ),
  refresh: <path d="M20 11a8 8 0 0 0-14.9-3M4 5v4h4M4 13a8 8 0 0 0 14.9 3M20 19v-4h-4" />,
};

export function Icon({ kind }: { kind: keyof typeof PATHS }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true">{PATHS[kind]}</svg>;
}
