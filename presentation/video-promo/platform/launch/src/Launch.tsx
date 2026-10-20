import React from 'react';
import {
  AbsoluteFill, Audio, Img, Sequence, continueRender, delayRender, interpolate, random,
  spring, staticFile, useCurrentFrame, useVideoConfig,
} from 'remotion';

// Brand tokens from the platform's own stylesheet (public/style.css).
const C = {
  bg: '#000', surface: '#0b0b0b', raised: '#1a1a1a', border: '#222', line: '#333', disabled: '#666',
  secondary: '#999', primary: '#e8e8e8', display: '#fff', accent: '#d71921', success: '#4a9e5c',
};
const DOTO = 'Doto, monospace', BODY = "'Space Grotesk', sans-serif", MONO = "'Space Mono', monospace";

const fontHandle = delayRender('Loading brand fonts');
Promise.all(
  ([['Doto', 'doto.ttf', '400 900'], ['Space Grotesk', 'space-grotesk.ttf', '300 700'], ['Space Mono', 'space-mono.ttf', '400']] as const)
    .map(([family, file, weight]) => new FontFace(family, `url(${staticFile('fonts/' + file)})`, {weight}).load()
      .then(face => document.fonts.add(face))),
).then(() => continueRender(fontHandle));

// Scene timeline (frames at 30 fps).
const T = {
  open: [0, 150], problem: [150, 270], title: [420, 150], stack: [570, 330],
  features: [900, 750], agent: [1650, 330], uses: [1980, 240], install: [2220, 240], end: [2460, 210],
} as const;
export const TOTAL = T.end[0] + T.end[1];
const SHOT = 150;

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

const useRise = (at: number, damping = 200) => {
  const frame = useCurrentFrame(), {fps} = useVideoConfig();
  return spring({frame: frame - at, fps, config: {damping, mass: 0.8}});
};

const Appear: React.FC<{at: number; y?: number; children: React.ReactNode; style?: React.CSSProperties}> = ({at, y = 24, children, style}) => {
  const p = useRise(at);
  return <div style={{opacity: p, transform: `translateY(${(1 - p) * y}px)`, ...style}}>{children}</div>;
};

// Dot-matrix headline: characters switch on one by one with a short flicker.
const DotText: React.FC<{text: string; at: number; step?: number; size: number; color?: string; seed?: string}> = ({text, at, step = 2, size, color = C.display, seed = text}) => {
  const frame = useCurrentFrame();
  return (
    <span style={{font: `600 ${size}px/1.05 ${DOTO}`, letterSpacing: '-0.03em', color, whiteSpace: 'pre'}}>
      {[...text].map((ch, i) => {
        const t = frame - (at + i * step);
        const on = t < 0 ? 0 : t < 6 ? (random(`${seed}-${i}-${frame}`) > 0.45 ? 1 : 0.15) : 1;
        return <span key={i} style={{opacity: on}}>{ch}</span>;
      })}
    </span>
  );
};

const Eyebrow: React.FC<{children: React.ReactNode; color?: string; size?: number}> = ({children, color = C.secondary, size = 22}) => (
  <div style={{font: `400 ${size}px ${MONO}`, letterSpacing: '0.1em', textTransform: 'uppercase', color}}>{children}</div>
);

const Scene: React.FC<{span: readonly [number, number]; children: React.ReactNode; fadeIn?: number; fadeOut?: number}> = ({span, children, fadeIn = 10, fadeOut = 12}) => (
  <Sequence from={span[0]} durationInFrames={span[1]}>
    <Fader dur={span[1]} fadeIn={fadeIn} fadeOut={fadeOut}>{children}</Fader>
  </Sequence>
);
const Fader: React.FC<{dur: number; fadeIn: number; fadeOut: number; children: React.ReactNode}> = ({dur, fadeIn, fadeOut, children}) => {
  const frame = useCurrentFrame();
  const o = interpolate(frame, [0, fadeIn, dur - fadeOut, dur], [0, 1, 1, 0], clamp);
  return <AbsoluteFill style={{opacity: o}}>{children}</AbsoluteFill>;
};

const Logo: React.FC<{size: number; at?: number}> = ({size, at = 0}) => {
  const frame = useCurrentFrame();
  const draw = interpolate(frame - at, [0, 30], [1, 0], clamp);
  const dots = interpolate(frame - at, [20, 40], [0, 1], clamp);
  return (
    <svg viewBox="0 0 48 48" style={{width: size, height: size}}>
      <g fill="none" stroke={C.display} strokeWidth={2}>
        <path d="M8 8h32M8 40h32M24 8v32" pathLength={1} strokeDasharray={1} strokeDashoffset={draw} />
        <path d="M16 8v32M32 8v32" strokeDasharray="1 4" opacity={dots} />
      </g>
    </svg>
  );
};

// Slowly drifting dot grid, echoing the dot-matrix type.
const Backdrop = () => {
  const frame = useCurrentFrame();
  const shift = (frame * 0.15) % 40;
  return (
    <AbsoluteFill style={{background: C.bg}}>
      <AbsoluteFill style={{
        backgroundImage: 'radial-gradient(#1c1c1c 1.4px, transparent 1.6px)', backgroundSize: '40px 40px',
        backgroundPosition: `${shift}px ${shift}px`,
      }} />
      <AbsoluteFill style={{background: 'radial-gradient(ellipse at 50% 45%, transparent 35%, rgba(0,0,0,0.92) 100%)'}} />
    </AbsoluteFill>
  );
};

// Persistent interface chrome, styled like the portal's header and footer.
const SECTIONS: [number, string][] = [
  [T.problem[0], '00 / Why'], [T.title[0], '01 / Introducing'], [T.stack[0], '02 / The stack'],
  [T.features[0], '03 / The platform'], [T.agent[0], '04 / AI agents'], [T.uses[0], '05 / Use it as'],
  [T.install[0], '06 / Install'],
];
const Hud = () => {
  const frame = useCurrentFrame(), {fps} = useVideoConfig();
  const o = interpolate(frame, [T.problem[0], T.problem[0] + 20, T.end[0] - 10, T.end[0] + 5], [0, 1, 1, 0], clamp);
  const label = [...SECTIONS].reverse().find(([s]) => frame >= s)?.[1] ?? '';
  const secs = Math.floor(frame / fps), ff = String(frame % fps).padStart(2, '0');
  const tc = `${String(Math.floor(secs / 60)).padStart(2, '0')}:${String(secs % 60).padStart(2, '0')}:${ff}`;
  const text: React.CSSProperties = {font: `400 18px ${MONO}`, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.secondary};
  return (
    <AbsoluteFill style={{opacity: o, pointerEvents: 'none'}}>
      <div style={{position: 'absolute', top: 44, left: 96, right: 96, display: 'flex', alignItems: 'center', gap: 16}}>
        <svg viewBox="0 0 48 48" style={{width: 30, height: 30}}><g fill="none" stroke={C.display} strokeWidth={2}><path d="M8 8h32M8 40h32M24 8v32" /><path strokeDasharray="1 4" d="M16 8v32M32 8v32" /></g></svg>
        <span style={{font: `500 28px ${BODY}`, letterSpacing: '-0.05em', color: C.display}}>iceberg</span>
        <span style={{font: `400 28px ${BODY}`, color: C.disabled}}>/</span>
        <span style={{...text, marginLeft: 8}}>[ {label} ]</span>
        <span style={{...text, marginLeft: 'auto'}}><span style={{display: 'inline-block', width: 8, height: 8, borderRadius: 4, background: C.success, marginRight: 12, verticalAlign: 'middle'}} />Local workspace</span>
      </div>
      <div style={{position: 'absolute', bottom: 40, left: 96, right: 96, display: 'flex', ...text, fontSize: 16}}>
        <span>Local <Sep /> Open standards <Sep /> Your infrastructure</span>
        <span style={{marginLeft: 'auto'}}>{tc}</span>
      </div>
      <div style={{position: 'absolute', bottom: 0, left: 0, height: 2, width: `${(frame / TOTAL) * 100}%`, background: C.accent}} />
    </AbsoluteFill>
  );
};
const Sep = () => <span style={{color: C.disabled, padding: '0 14px'}}>/</span>;

/* ---------- 1. Cold open ---------- */
const Open = () => (
  <AbsoluteFill style={{padding: '0 160px', justifyContent: 'center'}}>
    <Appear at={4}><Eyebrow>Iceberg / Data platform</Eyebrow></Appear>
    <div style={{marginTop: 40}}><DotText text="Data." at={14} step={4} size={200} /></div>
    <div><DotText text="Under control." at={48} step={3} size={200} /></div>
  </AbsoluteFill>
);

/* ---------- 2. The problem ---------- */
const NEEDS = ['Catalog', 'Object storage', 'Identity', 'Engine', 'A place to work'];
const Problem = () => {
  const frame = useCurrentFrame();
  const box = interpolate(frame, [150, 185], [1, 0], clamp);
  const dim = interpolate(frame, [150, 175], [1, 0.45], clamp);
  return (
    <AbsoluteFill style={{padding: '0 160px', justifyContent: 'center'}}>
      <Appear at={0}><div style={{font: `400 76px/1.15 ${BODY}`, letterSpacing: '-0.02em', color: C.display, maxWidth: 1400}}>
        Getting started with Apache Iceberg<br /><span style={{color: C.secondary}}>takes more than a table format.</span>
      </div></Appear>
      <div style={{position: 'relative', marginTop: 90, display: 'flex', gap: 28, padding: 28}}>
        <svg style={{position: 'absolute', inset: 0, width: '100%', height: '100%', overflow: 'visible'}}>
          <rect x={1} y={1} width="calc(100% - 2px)" height="calc(100% - 2px)" rx={10} fill="none" stroke={C.accent} strokeWidth={2} pathLength={1} strokeDasharray={1} strokeDashoffset={box} />
        </svg>
        {frame > 180 && <div style={{position: 'absolute', top: -34, left: 28, opacity: interpolate(frame, [180, 192], [0, 1], clamp)}}><Eyebrow color={C.accent} size={18}>Vendor bundle</Eyebrow></div>}
        {NEEDS.map((n, i) => (
          <Appear key={n} at={40 + i * 12} style={{flex: 1, opacity: undefined}}>
            <div style={{borderTop: `1px solid ${C.line}`, paddingTop: 22, opacity: dim}}>
              <Eyebrow size={18} color={C.disabled}>[ 0{i + 1} ]</Eyebrow>
              <div style={{font: `400 30px ${BODY}`, color: C.primary, marginTop: 12}}>{n}</div>
            </div>
          </Appear>
        ))}
      </div>
      <Appear at={170}><div style={{font: `400 40px ${BODY}`, color: C.secondary, marginTop: 70}}>
        Vendors make that part easy. <span style={{color: C.display}}>That's usually where <span style={{color: C.accent}}>lock-in</span> starts.</span>
      </div></Appear>
    </AbsoluteFill>
  );
};

/* ---------- 3. Title ---------- */
const Title = () => (
  <AbsoluteFill style={{padding: '0 160px', justifyContent: 'center'}}>
    <div style={{display: 'flex', alignItems: 'center', gap: 36}}>
      <Logo size={120} at={0} />
      <Appear at={12}><Eyebrow>Introducing · open source · v0.5.3</Eyebrow></Appear>
    </div>
    <div style={{marginTop: 40}}><DotText text="Iceberg Data" at={18} step={2} size={190} /></div>
    <div><DotText text="Platform." at={40} step={2} size={190} /></div>
    <Appear at={70}><div style={{font: `400 52px ${BODY}`, color: C.secondary, marginTop: 44}}>
      An open lakehouse <span style={{color: C.display}}>you actually own.</span>
    </div></Appear>
  </AbsoluteFill>
);

/* ---------- 4. The stack ---------- */
const BLOCKS = [
  {role: 'Catalog', name: 'Apache Polaris', note: 'Iceberg REST catalog and grants', speaks: ['Iceberg REST']},
  {role: 'Identity', name: 'Keycloak', note: 'People, notebooks and agents', speaks: ['OIDC', 'MCP']},
  {role: 'Object storage', name: 'RustFS', note: 'Parquet and metadata files', speaks: ['S3']},
  {role: 'Metadata', name: 'PostgreSQL', note: 'Durable catalog state', speaks: []},
  {role: 'Notebooks', name: 'marimo', note: 'One shared filespace per team', speaks: ['Iceberg REST', 'S3', 'OIDC']},
  {role: 'Engine', name: 'DuckDB', note: 'Fast SQL on Iceberg tables', speaks: ['Iceberg REST', 'S3']},
];
const CHIPS = ['Iceberg REST', 'S3', 'OIDC', 'MCP'];
const CHIP_AT = 110, CHIP_STEP = 28, SWAP_AT = 235;
const Stack = () => {
  const frame = useCurrentFrame();
  const active = frame >= CHIP_AT && frame < CHIP_AT + CHIPS.length * CHIP_STEP ? CHIPS[Math.floor((frame - CHIP_AT) / CHIP_STEP)] : null;
  const engine = frame < SWAP_AT + 12 ? 'DuckDB' : frame < SWAP_AT + 40 ? 'PyIceberg' : 'Your engine';
  const swapping = frame >= SWAP_AT;
  const glitch = swapping && ((frame >= SWAP_AT + 8 && frame < SWAP_AT + 16) || (frame >= SWAP_AT + 36 && frame < SWAP_AT + 44));
  return (
    <AbsoluteFill style={{padding: '150px 126px 0'}}>
      <Appear at={0}><Eyebrow>Six open building blocks</Eyebrow></Appear>
      <div style={{marginTop: 18}}><DotText text="Every block, open." at={4} step={1} size={100} /></div>
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 24, marginTop: 48}}>
        {BLOCKS.map((b, i) => {
          const isEngine = b.role === 'Engine';
          const lit = (active && b.speaks.includes(active)) || (isEngine && swapping);
          const color = isEngine && swapping ? C.accent : C.display;
          return (
            <Appear key={b.role} at={20 + i * 8}>
              <div style={{
                height: 200, padding: '26px 30px', borderRadius: 10, background: lit ? '#101010' : C.surface,
                border: `1px solid ${lit ? color : C.border}`, display: 'flex', flexDirection: 'column',
              }}>
                <div style={{display: 'flex', justifyContent: 'space-between'}}>
                  <Eyebrow size={17}>{b.role}</Eyebrow>
                  <Eyebrow size={17} color={C.disabled}>0{i + 1}</Eyebrow>
                </div>
                <div style={{font: `500 44px ${BODY}`, letterSpacing: '-0.03em', color: isEngine && swapping ? C.accent : C.display, marginTop: 20, opacity: glitch ? 0.2 : 1}}>
                  {isEngine ? engine : b.name}
                </div>
                <div style={{font: `400 22px ${BODY}`, color: C.secondary, marginTop: 'auto'}}>
                  {isEngine && swapping ? 'Anything that speaks Iceberg REST' : b.note}
                </div>
              </div>
            </Appear>
          );
        })}
      </div>
      <div style={{display: 'flex', alignItems: 'center', gap: 16, marginTop: 44}}>
        <Appear at={90}><Eyebrow size={18} color={C.disabled}>Connected through</Eyebrow></Appear>
        {CHIPS.map((c, i) => (
          <Appear key={c} at={94 + i * 4}>
            <div style={{
              font: `400 20px ${MONO}`, letterSpacing: '0.06em', textTransform: 'uppercase', padding: '10px 22px', borderRadius: 999,
              border: `1px solid ${active === c ? C.display : C.line}`, background: active === c ? C.display : 'transparent',
              color: active === c ? C.bg : C.secondary,
            }}>{c}</div>
          </Appear>
        ))}
        <div style={{marginLeft: 'auto'}}>
          <Appear at={SWAP_AT + 4}><div style={{font: `400 30px ${BODY}`, color: C.primary}}>Replace one block. <span style={{color: C.secondary}}>Keep the rest.</span></div></Appear>
        </div>
      </div>
    </AbsoluteFill>
  );
};

/* ---------- 5. Product tour ---------- */
const SHOTS = [
  {img: '02-admin-databases', title: 'Administer.', body: 'Teams, users with a role per team, databases and data shares, in one administration portal.', focus: '35% 55%'},
  {img: '18-user-table-snapshots', title: 'Explore.', body: 'Schemas, snapshots, branches, tags and a 100-row preview, all with your own permissions.', focus: '55% 55%'},
  {img: '40-user-received-notebook', title: 'Compute.', body: 'Team notebooks in marimo. Each run is isolated and uses that user\'s own permissions.', focus: '55% 40%'},
  {img: '33-user-share-credential', title: 'Share.', body: 'Share tables with another team, or give an external party a credential and a DuckDB script.', focus: '30% 40%'},
  {img: '05-admin-infrastructure', title: 'Observe.', body: 'Live health of every service in the stack, from the catalog down to storage.', focus: '35% 55%'},
];
const Shot: React.FC<{s: typeof SHOTS[number]; i: number}> = ({s, i}) => {
  const frame = useCurrentFrame();
  const enter = useRise(0, 26);
  const zoom = interpolate(frame, [0, SHOT], [1.0, 1.12]);
  return (
    <AbsoluteFill>
      <div style={{position: 'absolute', left: 96, top: 250, width: 560}}>
        <Appear at={4}><Eyebrow>[ 0{i + 1} / 0{SHOTS.length} ]</Eyebrow></Appear>
        <div style={{marginTop: 26}}><DotText text={s.title} at={8} step={2} size={92} seed={s.img} /></div>
        <Appear at={22}><p style={{font: `400 34px/1.45 ${BODY}`, color: C.secondary, margin: '34px 0 0'}}>{s.body}</p></Appear>
      </div>
      <div style={{
        position: 'absolute', left: 720, top: 150, width: 1320, height: 825, borderRadius: 14, overflow: 'hidden',
        border: `1px solid ${C.line}`, boxShadow: '0 40px 120px rgba(0,0,0,0.8)',
        transform: `translateX(${(1 - enter) * 160}px)`, opacity: enter,
      }}>
        <Img src={staticFile(`shots/${s.img}.png`)} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top left', transform: `scale(${zoom})`, transformOrigin: s.focus}} />
      </div>
    </AbsoluteFill>
  );
};
const Features = () => (
  <AbsoluteFill>
    {SHOTS.map((s, i) => (
      <Sequence key={s.img} from={i * SHOT} durationInFrames={SHOT}>
        <Fader dur={SHOT} fadeIn={8} fadeOut={8}><Shot s={s} i={i} /></Fader>
      </Sequence>
    ))}
  </AbsoluteFill>
);

/* ---------- Terminal ---------- */
type Line = {at: number; text: string; color?: string; type?: boolean; prompt?: string};
const Terminal: React.FC<{lines: Line[]; title: string; style?: React.CSSProperties; size?: number}> = ({lines, title, style, size = 24}) => {
  const frame = useCurrentFrame();
  const typing = lines.find(l => l.type && frame >= l.at && frame < l.at + l.text.length / 1.6 + 4);
  return (
    <div style={{background: C.surface, border: `1px solid ${C.line}`, borderRadius: 14, overflow: 'hidden', boxShadow: '0 40px 120px rgba(0,0,0,0.8)', ...style}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 10, padding: '18px 24px', borderBottom: `1px solid ${C.border}`}}>
        {[0, 1, 2].map(k => <span key={k} style={{width: 12, height: 12, borderRadius: 6, background: C.line}} />)}
        <span style={{font: `400 16px ${MONO}`, color: C.disabled, marginLeft: 16, letterSpacing: '0.06em'}}>{title}</span>
      </div>
      <div style={{padding: '28px 32px', font: `400 ${size}px/1.75 ${MONO}`, color: C.primary}}>
        {lines.filter(l => frame >= l.at).map((l, k) => {
          const shown = l.type ? l.text.slice(0, Math.floor((frame - l.at) * 1.6)) : l.text;
          const cursor = typing === l || (!typing && k === lines.filter(x => frame >= x.at).length - 1 && Math.floor(frame / 15) % 2 === 0);
          return (
            <div key={k} style={{color: l.color ?? C.primary, whiteSpace: 'pre-wrap', wordBreak: 'break-all'}}>
              {l.prompt && <span style={{color: C.accent}}>{l.prompt}</span>}{shown}
              {cursor && <span style={{display: 'inline-block', width: size * 0.6, height: size * 1.1, background: C.primary, verticalAlign: 'text-bottom', marginLeft: 4}} />}
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ---------- 6. AI agents over MCP ---------- */
const Agent = () => (
  <AbsoluteFill>
    <div style={{position: 'absolute', left: 96, top: 250, width: 600}}>
      <Appear at={0}><Eyebrow>Model Context Protocol</Eyebrow></Appear>
      <div style={{marginTop: 26}}><DotText text="Agents sign" at={4} step={2} size={96} /></div>
      <div><DotText text="in too." at={24} step={2} size={96} /></div>
      <Appear at={40}><p style={{font: `400 34px/1.45 ${BODY}`, color: C.secondary, margin: '34px 0 0'}}>
        Claude Code, Codex, Copilot and other MCP clients sign in through Keycloak and work with <span style={{color: C.display}}>the same grants as you.</span>
      </p></Appear>
    </div>
    <Terminal title="claude — ~/iceberg" size={22} style={{position: 'absolute', left: 760, top: 170, width: 1064, height: 790}} lines={[
      {at: 14, prompt: '$ ', text: 'claude mcp add --transport http iceberg http://localhost:3002/mcp', type: true},
      {at: 64, text: 'Added HTTP MCP server iceberg', color: C.secondary},
      {at: 86, prompt: '> ', text: 'What data does grid-planning have on smart meters?', type: true},
      {at: 124, text: '↗ Opening Keycloak sign-in in your browser…', color: C.secondary},
      {at: 146, text: '✓ Signed in as sander · grid-planning · read & write', color: C.success},
      {at: 170, text: '⏺ iceberg · list_databases()', color: C.display},
      {at: 188, text: '⏺ iceberg · list_tables(smart-meter-readings, synthetic)', color: C.display},
      {at: 206, text: '⏺ iceberg · describe_table(neighborhood_electricity)', color: C.display},
      {at: 224, text: '⏺ iceberg · preview_rows(limit=5)', color: C.display},
      {at: 250, text: '● smart-meter-readings holds neighborhood_electricity:', color: C.primary},
      {at: 262, text: '  street-level consumption and solar production,', color: C.secondary},
      {at: 272, text: '  read with your own permissions.', color: C.secondary},
    ]} />
  </AbsoluteFill>
);

/* ---------- 7. Use it as ---------- */
const USES = [
  {k: 'A reference architecture', big: '6', body: 'Six open blocks. See how catalog, storage, identity and engines fit together, then reuse what fits.'},
  {k: 'A classroom', big: '48', body: 'The demo-company skill sets up a fictional company with a sign-in slip for each participant.'},
  {k: 'A personal lab', big: 'v3', body: 'Snapshots, branches and Iceberg v3 on your laptop. No cloud account, no trial clock.'},
];
const Uses = () => (
  <AbsoluteFill style={{padding: '190px 126px 0'}}>
    <Appear at={0}><Eyebrow>One platform, three ways in</Eyebrow></Appear>
    <div style={{marginTop: 18}}><DotText text="Use it as." at={4} step={2} size={110} /></div>
    <div style={{display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 48, marginTop: 70}}>
      {USES.map((u, i) => (
        <Appear key={u.k} at={30 + i * 18}>
          <div style={{borderTop: `1px solid ${C.line}`, paddingTop: 30}}>
            <Eyebrow size={18}>[ 0{i + 1} ]</Eyebrow>
            <div style={{marginTop: 20}}><DotText text={u.big} at={36 + i * 18} step={3} size={150} color={i === 1 ? C.accent : C.display} /></div>
            <div style={{font: `500 38px ${BODY}`, color: C.display, marginTop: 16, letterSpacing: '-0.02em'}}>{u.k}</div>
            <p style={{font: `400 27px/1.5 ${BODY}`, color: C.secondary, margin: '16px 0 0'}}>{u.body}</p>
          </div>
        </Appear>
      ))}
    </div>
  </AbsoluteFill>
);

/* ---------- 8. Install ---------- */
const Install = () => (
  <AbsoluteFill style={{padding: '190px 126px 0'}}>
    <Appear at={0}><Eyebrow>Docker with Compose v2 · macOS · Linux · Windows</Eyebrow></Appear>
    <div style={{marginTop: 18}}><DotText text="One command." at={4} step={2} size={110} /></div>
    <Terminal title="bash — ~" size={25} style={{marginTop: 56, height: 470}} lines={[
      {at: 20, prompt: '$ ', text: 'curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | sh', type: true},
      {at: 100, text: '✓ Created ~/iceberg-data-platform', color: C.success},
      {at: 116, text: '✓ Generated .env with random credentials', color: C.success},
      {at: 132, text: '✓ Pulled images and started the platform', color: C.success},
      {at: 156, text: '→ Administration portal   http://localhost:3000', color: C.display},
      {at: 166, text: '→ User portal             http://localhost:3002', color: C.display},
    ]} />
  </AbsoluteFill>
);

/* ---------- 9. End card ---------- */
const End = () => (
  <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', textAlign: 'center'}}>
    <Logo size={110} at={0} />
    <div style={{marginTop: 40}}><DotText text="Iceberg Data Platform" at={10} step={1} size={120} /></div>
    <Appear at={40}><div style={{font: `400 44px ${BODY}`, color: C.secondary, marginTop: 30}}>
      An open lakehouse <span style={{color: C.display}}>you actually own.</span>
    </div></Appear>
    <Appear at={60}><div style={{
      marginTop: 60, font: `400 30px ${MONO}`, color: C.bg, background: C.display, padding: '20px 44px', borderRadius: 999, letterSpacing: '0.02em',
    }}>github.com/sanderdw/iceberg-data-platform</div></Appear>
    <Appear at={78}><div style={{marginTop: 44}}><Eyebrow size={20}>Apache-2.0 <Sep /> Open source <Sep /> Runs on one laptop</Eyebrow></div></Appear>
  </AbsoluteFill>
);

export const Launch = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{background: C.bg}}>
      <Backdrop />
      <Scene span={T.open} fadeIn={1}><Open /></Scene>
      <Scene span={T.problem}><Problem /></Scene>
      <Scene span={T.title}><Title /></Scene>
      <Scene span={T.stack}><Stack /></Scene>
      <Scene span={T.features} fadeIn={1} fadeOut={1}><Features /></Scene>
      <Scene span={T.agent}><Agent /></Scene>
      <Scene span={T.uses}><Uses /></Scene>
      <Scene span={T.install}><Install /></Scene>
      <Scene span={T.end} fadeOut={1}><End /></Scene>
      <Hud />
      <Audio src={staticFile('music.wav')} volume={f => interpolate(f, [0, 20, TOTAL - 60, TOTAL], [0, 0.9, 0.9, 0], clamp)} />
      <AbsoluteFill style={{background: C.bg, opacity: interpolate(frame, [TOTAL - 20, TOTAL], [0, 1], clamp), pointerEvents: 'none'}} />
    </AbsoluteFill>
  );
};
